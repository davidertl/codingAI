# Phase 12 Advanced Autonomy Improvements

Phase 12 adds adaptive autonomy behavior on top of the Phase 11 baseline.

## Implemented features

1. Strategy-memory quarantine and decay (`ai-agent/core/test_runner.py`)
   - memory now tracks:
     - `consecutive_failures`
     - `cooldown_until`
     - `last_success_at`
     - `last_failure_at`
   - memory score now includes:
     - recency decay (`memory_half_life_seconds`)
     - failure-streak penalty
   - quarantined strategies are skipped while cooldown is active.
2. Policy-driven strategy runtime controls
   - `strategy.max_attempts`
   - `strategy.quarantine_threshold`
   - `strategy.quarantine_seconds`
   - `strategy.memory_half_life_seconds`
3. Staged test-patch fallback (`ai-agent/main.py`)
   - run tests on base patch first.
   - if failed and enabled, request test patch ops and apply them only when confidence meets threshold.
   - rerun tests on patched content before any GitHub write path.
4. Policy-driven test-patch controls
   - `patch.test_patch_on_failure_only`
   - `patch.test_patch_min_confidence`
5. API/UI visibility
   - tracked issue summary now includes:
     - test-patch applied/confidence/ops-added
     - strategy autonomy settings used

## Policy additions

Defaults in `ai-agent/config/policies/default.json`:

1. `patch.test_patch_on_failure_only: true`
2. `patch.test_patch_min_confidence: 0.55`
3. `strategy.max_attempts: 3`
4. `strategy.quarantine_threshold: 3`
5. `strategy.quarantine_seconds: 43200`
6. `strategy.memory_half_life_seconds: 604800`

## Behavior note

These improvements preserve the core safety invariant:

1. patches are applied and tested locally first.
2. GitHub writes occur only after passing test results (or never in dry-run mode).
