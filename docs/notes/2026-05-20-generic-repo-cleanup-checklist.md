# Generic Repo Cleanup / Relocation Checklist

A practical, reusable checklist for any team tidying up or moving a code
repository. Grounded in real issues seen across the rebooter-droids and
rebooter-firmware repos. Work top to bottom; nothing here is destructive
on its own, but several steps produce *proposals* that a human should
approve before deletion.

## 1. Scan for committed secrets — but judge sensitivity

- Grep history and the working tree for private keys, API tokens, cloud
  credentials, and `.env` files: look for `BEGIN ... PRIVATE KEY`,
  `AKIA[0-9A-Z]{16}`, `password=`, `api_key`, bearer tokens.
- **Judge what you find — do not blanket-purge.** A genuinely public
  value (e.g. a guest-Wi-Fi password already posted on a wall, a public
  API base URL, a sample/placeholder token) is not a security concern
  and does not need history rewriting.
- Real secrets (production credentials, signing keys, private certs)
  must be rotated first, then removed from history. History rewrites are
  destructive — propose them, get sign-off, never run them unilaterally.

## 2. One repo = one working copy

- A repo should have a single working copy on disk per machine. Multiple
  full clones of the same repo (e.g. `repo`, `repo-fork`, `repo-copy`)
  drift apart, hide uncommitted work, and make "which one is real?"
  ambiguous.
- Before retiring an extra clone, confirm every branch and stash it holds
  is preserved on the remote. Push any unique local branch as an archive
  branch first. Only then propose retiring the redundant clone.

## 3. No copy-pasted snapshot directories — use branches

- Directories like `feature-v2-backup/` or `old-version-2026-05-01/`
  committed alongside live code are a maintenance trap.
- Capture point-in-time state with a git branch or tag, not a copied
  folder. Branches are diffable, mergeable, and do not bloat the tree.

## 4. Commit and push WIP regularly

- Uncommitted work is unbacked-up work — a disk failure or a bad command
  loses it. Treat a long-lived dirty working tree as a data-loss risk.
- Commit logical chunks frequently with plain, factual messages. Push to
  the remote (or to a `wip/` branch) at least daily.
- If WIP is not ready for a shared branch, push it to a clearly named
  `wip/<topic>-<date>` branch so it is backed up without disrupting others.

## 5. Prune stale branches — by proposal

- List local branches already merged into the mainline
  (`git branch --merged main`) and remote branches with no open work.
- These are pruning *candidates*. Produce the list with counts and names;
  let a human approve actual deletion. Never `git branch -D` or delete
  remote branches as part of an automated sweep.

## 6. Keep build artifacts out of git

- `.pio/`, `node_modules/`, `dist/`, `build/`, `__pycache__/`, compiled
  binaries, and coverage output should all be in `.gitignore`.
- If artifacts were committed previously, propose removing them from
  tracking (`git rm --cached`) in a reviewed change — and add the
  `.gitignore` entry so they do not return.

## 7. Split oversized source files

- Files that have grown into thousands of lines (large web-server or
  client modules are common offenders) are hard to review and merge.
- Split by responsibility into focused modules. Do it as its own
  reviewed change, separate from behavior changes, so the diff is a
  pure move.

## 8. Retire orphan repos

- Identify repos no longer built, deployed, or referenced. Confirm their
  history is fully pushed to a remote, then propose archiving them
  (mark read-only / archive on the host) rather than deleting.
- Document what each retired repo was, where its history lives, and what
  superseded it.

## 9. Every repo has a README and an adequate `.gitignore`

- The README states what the repo is, how to build/run it, and where it
  is deployed. Keep it truthful — stale claims (a feature described as a
  "stub" long after it shipped) actively mislead. Verify doc claims
  against the code and CHANGELOG when cleaning up.
- The `.gitignore` covers the language/toolchain in use (build output,
  dependency dirs, editor/OS cruft, local env files).

## Quick pass summary

1. Secret scan (rotate real ones; ignore genuinely public values).
2. One working copy per repo.
3. Branches/tags instead of snapshot folders.
4. WIP committed and pushed regularly.
5. Stale-branch pruning list — for human approval.
6. Build artifacts gitignored.
7. Oversized files split.
8. Orphan repos archived, not deleted.
9. README accurate; `.gitignore` adequate.
