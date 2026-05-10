param(
    [string]$ComPort = "COM11",
    [int]$Baud = 19200,
    [int]$FlashAttempts = 5,
    [switch]$SkipBuild,
    [switch]$BuildOnly,
    [switch]$FlashOnly,
    [switch]$CleanBuild,
    [switch]$KillStaleProcesses
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pioExe = "C:\Program Files\Python312\Scripts\pio.exe"
$firmwarePath = Join-Path $repoRoot ".pio\build\sonoff_s31_bootstrap\firmware.bin"
$buildDir = Join-Path $repoRoot ".pio\build\sonoff_s31_bootstrap"
$ldFile = Join-Path $buildDir "ld\local.eagle.app.v6.common.ld"
$bootstrapConfigPath = Join-Path $repoRoot "include\bootstrap_config.h"
$provisioningConfigPath = Join-Path $repoRoot "include\provisioning_config.h"

function Get-BootstrapFirmwareUrls {
    if (-not (Test-Path $bootstrapConfigPath)) {
        return @()
    }

    $content = Get-Content $bootstrapConfigPath
    $urls = @()
    foreach ($line in $content) {
        if ($line -match 'PRIMARY_FIRMWARE_URL\[\] = "([^"]+)"') {
            $urls += $matches[1]
        } elseif ($line -match 'SECONDARY_FIRMWARE_URL\[\] = "([^"]+)"') {
            $urls += $matches[1]
        }
    }
    return $urls
}

function Get-SetupApPassword {
    if (-not (Test-Path $provisioningConfigPath)) {
        return $null
    }

    foreach ($line in (Get-Content $provisioningConfigPath)) {
        if ($line -match 'SETUP_AP_PASSWORD\[\] = "([^"]+)"') {
            return $matches[1]
        }
    }

    return $null
}

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Get-StaleProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match "python|pio|platformio|scons"
    } | Select-Object ProcessId, Name, CommandLine
}

function Show-State {
    Write-Section "State"
    Write-Host "Repo:             $repoRoot"
    Write-Host "COM port:         $ComPort"
    Write-Host "Baud:             $Baud"
    Write-Host "PIO exe:          $pioExe"
    Write-Host "Build dir:        $buildDir"
    Write-Host "Firmware path:    $firmwarePath"
    Write-Host "Linker script:    $ldFile"
    Write-Host "Firmware exists:  $([bool](Test-Path $firmwarePath))"
    Write-Host "LD script exists: $([bool](Test-Path $ldFile))"
    $urls = Get-BootstrapFirmwareUrls
    if ($urls.Count -gt 0) {
        Write-Host "Bootstrap OTA URLs:"
        foreach ($url in $urls) {
            Write-Host "  - $url"
        }
    }
    $setupPassword = Get-SetupApPassword
    if ($setupPassword) {
        Write-Host "Bootstrap setup AP:"
        Write-Host "  - SSID pattern: Rebooter-Setup-<chipid>"
        Write-Host "  - Password:     $setupPassword"
        Write-Host "  - Portal:       http://192.168.4.1"
    }
    if ($BuildOnly) {
        Write-Host "Mode:             build-only"
    } elseif ($FlashOnly) {
        Write-Host "Mode:             flash-only"
    } else {
        Write-Host "Mode:             build-then-flash"
    }
}

function Stop-StaleProcessesIfRequested {
    $stale = Get-StaleProcesses
    if (-not $stale) {
        Write-Host "No python/platformio-style processes found."
        return
    }

    Write-Section "Process Check"
    $stale | Format-Table -AutoSize

    if ($KillStaleProcesses) {
        Write-Host "Stopping stale processes..." -ForegroundColor Yellow
        $stale | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Start-Sleep -Seconds 1
    } else {
        Write-Host "Processes shown above may be locking build files. Re-run with -KillStaleProcesses to stop them." -ForegroundColor Yellow
        if ($stale.CommandLine -match "esptool .*read-flash") {
            Write-Host "An old esptool read-flash process is still running. That is the most likely reason the bootstrap build is locked right now." -ForegroundColor Yellow
        }
    }
}

function Clean-BuildDirIfRequested {
    if (-not $CleanBuild) { return }
    if (Test-Path $buildDir) {
        Write-Section "Clean Build"
        Write-Host "Removing $buildDir" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $buildDir
    }
}

function Build-Bootstrap {
    if ($SkipBuild) {
        Write-Host "Skipping build per flag."
        return
    }

    Write-Section "Build Bootstrap"
    try {
        & $pioExe run -e sonoff_s31_bootstrap
    } catch {
        Write-Host "Bootstrap build failed." -ForegroundColor Red
        if (Test-Path $ldFile) {
            Write-Host "The linker script exists, so this looks like a regular build failure, not a missing-target problem."
        } else {
            Write-Host "The linker script was never produced. If the prior error said the file was in use, a stale build process is still the most likely cause."
        }
        throw
    }
}

function Ensure-FirmwareExists {
    Write-Section "Firmware Check"
    if (-not (Test-Path $firmwarePath)) {
        throw "Bootstrap firmware does not exist at $firmwarePath"
    }
    $item = Get-Item $firmwarePath
    Write-Host "Bootstrap firmware ready."
    Write-Host "Path: $($item.FullName)"
    Write-Host "Size: $($item.Length) bytes"
}

function Flash-Bootstrap {
    if ($BuildOnly) {
        Write-Host "Build-only mode selected; skipping flash."
        return
    }

    Write-Section "Flash Bootstrap"
    for ($attempt = 1; $attempt -le $FlashAttempts; $attempt++) {
        Write-Host "Attempt $attempt of $FlashAttempts" -ForegroundColor Cyan
        Write-Host "When ready:"
        Write-Host "1. Power off the Sonoff"
        Write-Host "2. Hold GPIO0 low"
        Write-Host "3. Power on the Sonoff"
        Write-Host "4. Keep holding GPIO0 while flashing starts"
        Write-Host "5. Release after flashing is clearly underway"
        Write-Host ""
        Read-Host "Press Enter to start flashing"

        py -m esptool --port $ComPort --baud $Baud --no-stub write-flash --flash-size detect --no-compress 0x00000 $firmwarePath
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Flash succeeded." -ForegroundColor Green
            return
        }

        Write-Host "Flash attempt $attempt failed." -ForegroundColor Yellow
        if ($attempt -lt $FlashAttempts) {
            Write-Host "Most likely cause: missed bootloader timing or unstable serial entry. We'll retry." -ForegroundColor Yellow
        }
    }

    throw "All $FlashAttempts flash attempts failed."
}

Show-State
Stop-StaleProcessesIfRequested
Clean-BuildDirIfRequested
if (-not $FlashOnly) {
    Build-Bootstrap
}
Ensure-FirmwareExists
Flash-Bootstrap
