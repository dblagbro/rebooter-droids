# `.69` Duplicate Row Cleanup Memo (2026-05-16)

## Situation

During the recovery / re-enroll work, `.69` came back online under a new active
hub row while the older row remained offline.

Current IDs:

- active live row: `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT`
  - display name: `lab-69`
- stale offline row: `dev_01KR9VZKGW72DS7DAQEFAV3T58`
  - display name: `Erica's R.L. Speaker`

The live row is the one currently receiving heartbeats and power rows.

## Recommendation

1. Keep `dev_01KRQBRSG1BZ5SR87QQ2KSSVFT` as the authoritative active row.
2. Mark `dev_01KR9VZKGW72DS7DAQEFAV3T58` as superseded / archived / historical,
   rather than treating it as an active device.
3. Preserve old history, but do not let the stale row remain visible as a normal
   active device in operator-facing lists by default.
4. Avoid destructive merge behavior while firmware-side central stability is
   still being retested.

## Minimum safe fallback if the hub lacks a formal merge/archive primitive

If there is no first-class duplicate-resolution flow yet:

- keep the new live row untouched
- rename or annotate the old row as stale / superseded
- hide it from default active-device views
- preserve it for audit/history only

## Why this is the safest move

- the live row already matches current device behavior
- deleting or mutating the live row risks losing the good state we have now
- preserving the stale row as historical state avoids accidental data loss while
  keeping the operator view clean

## Firmware-side note

This memo is intentionally scoped just to row cleanup. Separately, firmware is
still chasing a broader central-enabled soak instability on `0.1.29`, so the hub
team should avoid assuming that duplicate identity cleanup alone resolves the
larger fleet stability question.
