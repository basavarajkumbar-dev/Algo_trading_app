# Preservation report

**Original project:** `gainzalgo-pro-max` working copy, branch `master` at
`8c841f740f2445159b6bdf4c56f9e00d8fd50b9a` ("Phase 14: live trading safety
layer", 28 commits). One untracked file: `backend/app/scripts/forensic_audit.py`.

**Target:** `github.com/basavarajkumbar-dev/Algo_trading_app`, which was empty
(no branches) when inspected on 2026-10-19, so nothing there could be
overwritten. No force-push was used at any point.

## How it was copied

1. The full history of the original was imported unchanged; `8c841f7` is an
   ancestor of `main` (`git merge-base --is-ancestor` succeeds) and the set of
   tracked files at `8c841f7` equals `git ls-files` of the original.
2. Commit `a9fb9d1` "Preserved project migration" added only: the untracked
   audit script, `frontend/public/.gitkeep`, and `.gitignore` entries; it
   untracked `backend/dump.rdb` (a 307-byte runtime Redis snapshot). It was
   pushed as `main` and is the base of all upgrade commits.
3. Not copied: virtualenvs, `node_modules`, `.next`, tool caches, runtime
   databases, any `.env`. History was scanned for Kite keys/tokens, private
   keys and cloud tokens: none found. `.env.example` holds placeholders only.

## Evidence the original is unchanged

| Check | Before (20:42 IST) | After (end of work) |
|---|---|---|
| HEAD | `8c841f740f24…` | `8c841f740f24…` |
| `git status --short` | `?? backend/app/scripts/forensic_audit.py` | identical |
| Commits | 28 | 28 |
| SHA-256 manifest of 3,726 files (excluding `.git`, `node_modules`, `.next`) | manifest sha256 `27c07dc4009d3ca18bd0eb20979ca23a84cf38e98d6e0aa5650cdb5f06dfb301` | **identical**: same hash, `diff` empty |

No file in the original was modified, added or deleted. All upgrade work was
done in the new repository only.

## Not preserved here

A copy of the project on the owner's own Windows computer was not reachable
from this session. Its uncommitted edits (including an `.env.example` that the
October audit found to contain real-looking Kite values) were therefore **not**
copied — by design, since credentials must never enter Git. If those edits
matter, compare them with this repository yourself and keep any real keys
only in a local `.env`.
