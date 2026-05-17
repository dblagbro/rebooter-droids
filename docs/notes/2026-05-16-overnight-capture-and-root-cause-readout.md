# Overnight Capture And Root-Cause Readout (2026-05-16)

## Summary

- `0.1.29-dev-central-safe` is not dealing with a `.225`-only outlier anymore.
- Focused split-tests on `.225` and the completed 24-hour soak both point at a
  broader central-enabled transport / heap-churn failure mode.
- The OTA stress harness was updated so a reboot-cutoff upload is no longer
  misclassified as a failed upload when the device clearly accepted firmware and
  came back.

## `.225` Root-Cause Pass

### Controlled split-tests

Using local auth (`X-Rebooter-Auth: BenchPass123!`) on `http://192.168.1.225`:

1. `central.enabled=false`, `power.enabled=false`
   - device rebooted cleanly
   - remained `recovery_mode=false`
   - crossed the healthy-mark window and stayed healthy through `t+123s`

2. `central.enabled=true`, `power.enabled=false`
   - config save succeeded
   - device later returned to `recovery_mode=true`
   - `last_known_good_restored=true`

### Conclusion

The remaining trigger is in the central-enabled path itself, not just the
power-upload path.

## Broader soak result

The 24-hour power capture completed at `2026-05-16 15:49 -04:00`.

Capture scope:

- `.48`
- `.67`
- `.30`
- `.225`

By the end of the soak, all captured devices were back in `recovery_mode=true`.
Separate live checks also showed `.69` in `recovery_mode=true`.

### Last healthy timestamps from `status.ndjson`

- `.225`: `2026-05-15T22:34:38.8170161-04:00`
- `.30`: `2026-05-16T00:23:29.5493801-04:00`
- `.67`: `2026-05-16T02:11:10.5790494-04:00`
- `.48`: `2026-05-16T07:12:33.2588215-04:00`

Notes:

- The `first_recovery` field in the generated summary is not the best "when did
  it finally fail" signal, because some devices had earlier temporary recovery
  states before later healthy runs.
- `last_healthy` is the better timestamp for the final overnight transition.

## Transport / heap signal

Captured event snapshots from the soak show repeated transport failures after
healthy boot, followed by steadily declining free heap.

Representative `.30` sequence from the latest event snapshot:

- firmware assignment check transport failures
- heartbeat transport failures
- command poll transport failures
- power-sample transport failures
- free heap stepping down from roughly `19 KB` toward `11 KB`

Related live signals:

- `.67` currently reports `reset_reason="Exception"`
- `.48`, `.69`, `.30`, and `.225` currently show `reset_reason="Software/System restart"`
  from the current recovery boot, which likely masks the earlier failing boot's
  real reset reason
- `.48` event snapshot explicitly shows:
  - previous boot ended early
  - auto-recovery triggered after repeated early boot failures
  - last-known-good restored

## Best current diagnosis

The best current explanation is:

1. central-enabled steady-state work enters a repeated HTTPS failure cycle
2. transport failure handling plus failure logging continues allocating/churning
   heap
3. free heap ratchets downward over time
4. later boots fail early enough to trigger auto-recovery and last-known-good
   restore

This is broader than "power uploads are too heavy." The `.225` split-test proves
that central-enabled work alone is sufficient.

## G2 readout

The dedicated G2 timing run (`.48` / `.69`) completed at
`2026-05-15 23:19 -04:00`, before the later soak instability.

### `.48`

- samples: `1130`
- low-RTT samples (`<=1000 ms`): `1031`
- low-RTT offset average: `-55.6 ms`
- low-RTT offset range: `-167 ms .. 427 ms`
- last sampled state: healthy, not in recovery

### `.69`

- samples: `1130`
- low-RTT samples (`<=1000 ms`): `1049`
- low-RTT offset average: `-22.7 ms`
- low-RTT offset range: `-139 ms .. 445 ms`
- last sampled state: healthy, not in recovery

### Interpretation

The G2 data is still encouraging as a timing baseline, but it is no longer the
main release question. The central-enabled soak instability must be fixed before
we treat those timing numbers as operationally representative.

## OTA harness follow-up

`scripts/qa-ota-stress.ps1` now records:

- raw `upload_accepted`
- `effective_upload_accepted`
- `effective_upload_reason`

This keeps a reboot-cutoff OTA from being misreported as a failed upload when
the device clearly rebooted and came back.

## Recommended next step

Do not widen rollout.

The next firmware pass should focus on the central failure path:

- reduce repeated transport-failure churn
- reduce failure-log write/amplification under sustained outage
- widen spacing/backoff for firmware check / heartbeat / poll / power transport
  after consecutive failures
- rerun a tighter soak on a small set before trusting `0.1.29` again
