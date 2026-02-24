# CodingAI Versioning Policy
Version: experimental-0.23.0

## Scope

This versioning mode is optional for CodingAI operations.

If enabled for a workflow/release, treat the version as a global variable:

1. Pick one target version for the push.
2. Set all `Version:` headers in maintained docs to exactly that value before push.

## Scheme

Format: `experimental-x1.x2.x3`

1. `x1` = major version
   - Increased manually only.
   - Requires major change + successful clean audit + manual testing from multiple instances.
2. `x2` = significant release
   - Increase by 1 for major functional/API/interface/DB-impacting changes.
   - Reset `x3` to `0` when `x2` is increased.
3. `x3` = patch/feature release
   - Increase by 1 for smaller patches, fixes, and feature additions.

## Current baseline rule for this branch

For the current correction set, `x1` is explicitly fixed to `0` and `x2` must be `>20`.
The synchronized docs target is therefore `experimental-0.23.0`.

## Pre-push checklist

1. Confirm release type (`x2` or `x3` bump, or manual `x1` bump).
2. Update all doc `Version:` headers to the same value.
3. Verify with:
   - `grep -RIn "^Version:" docs`
4. Push only after versions are consistent.
