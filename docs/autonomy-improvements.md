# Autonomy Improvements
Version: experimental-0.22.0

## Implemented capabilities

1. Strategy memory with decay and quarantine:
   - per-strategy runs/success/failure memory
   - cooldown window after repeated failures
2. Confidence-gated strategy switching:
   - LLM recommendation accepted only when confidence passes threshold
   - memory/history fallback when confidence is low
3. Error fingerprint reuse:
   - failed fingerprints map to winning strategies for future biasing
4. Test-patch fallback controls (policy-driven):
   - configurable test patch generation gate and confidence threshold
5. Governance knobs surfaced via policy:
   - strategy max attempts
   - confidence threshold
   - quarantine thresholds/durations
   - memory half-life

## Web strategy extension

The adaptive runner now includes `web_live_playwright` for website-oriented validation:

1. Local web project detection (Node/web heuristics).
2. Browser-driven smoke interactions.
3. Optional external target mode via `WEB_SMOKE_BASE_URL`.
4. Deterministic CodingAI dashboard control checks when dashboard profile is active.
