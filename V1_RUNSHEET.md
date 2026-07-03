# Planning Navigator — v1 run-sheet

Purpose: v1 is a bounded portfolio MVP. Each item below has a definition of done and a stopping
rule, so "finishing" cannot quietly become a new phase. After v1 ships: decide continue vs. next
project — that decision is out of scope for this sheet.

## Definition of done, per item

**1. Live LLM grounding eval — done when:**
- `run_grounding_eval.py` has run against the live verifier (Haiku default; results JSON
  records verifier, model, prompts_version, run timestamp), AND
- a browser pass on the three presets (SE21 7BG, SE1 9TG, SE15 5JR) with the live LLM on shows
  no explanation introducing a number, threshold, or obligation absent from its excerpt (spot-read).

**2. Failing rows — done when false-accept = 0. Stopping rules:**
- Diagnosis order per failing row: gold-label bug → verify-prompt issue → genuine verifier weakness.
- Label bugs: fix the fixture.
- Prompt issues: MAX TWO verify-prompt iterations; bump `PROMPTS_VERSION` in policy_engine.py on
  every edit so eval JSONs stay attributable.
- If a false-accept survives both iterations: tighten toward strictness (accept more false-rejects)
  or step the verifier model up and document the trade. Do NOT grow the fixture set in v1.
- False-rejects: documented, not optimised — UNLESS the live verifier suppresses most good
  paraphrases (tool feels broken), which buys ONE loosening iteration; the shipped setting must
  still hold false-accept = 0. When in doubt, ship strict.

**Iteration 1 (prompts v2) — pre-registered BEFORE the rerun:**
- Live run 2026-07-02 (haiku-4.5, prompts v1): FA 0/12, FR 0/6, insufficient recall 3/6. All three
  misses were INSUFFICIENT→CONTRADICTED (absence read as negation); binary show/suppress: 18/18.
  Gold labels re-examined per the diagnosis order — all stand → classified as a prompt issue.
- prompts v2 change: CONTRADICTED narrowed to strict incompatibility ("cannot both be true");
  INSUFFICIENT made the explicit residual; "absence of support is never contradiction".
- SHIP v2 iff: FA = 0 AND FR = 0 AND insufficient recall > 3/6. Otherwise revert to prompts v1
  (policy_engine.py in the rc1 zip) and ship the v1 numbers, documenting the attempt.
- Iteration 2 stays UNSPENT unless v2 fails with an obvious targeted correction (a contrastive
  few-shot pair is the reserved move). The cap is a ceiling, not a target.
- Before rerunning, preserve the prompts-v1 artifact:
  `Copy-Item eval\grounding_eval_results.json eval\grounding_eval_results_prompts-v1.json`

**RESULT (2026-07-02, haiku-4.5, prompts v2) — measured against the rule above:**
- FA 0/12 · FR 0/6 · insufficient recall **5/6** · accuracy **94.4%**. Rule MET on all three
  conditions → **v2 SHIPPED**. v2 dominates v1 on every class (no regression); show/suppress 18/18.
- **Iteration 2 UNSPENT** — deliberately. v2 passed, so per the pre-registration we stop. Not
  "couldn't fix the last row" but "shouldn't": chasing it would break the pre-registration, risk
  perturbing 17 correct rows, and optimise a fixture with zero product impact.
- Residual: `P56-insuf-1` stays INSUFFICIENT→CONTRADICTED (both suppress; wording-only). Kept at its
  correct gold label (NOT relabelled to score 100% — that would be goodharting) and documented in
  README limitations.
- Frozen: `grounding_eval_results.json` (v2 live) · `..._prompts-v1.json` (v1 live) ·
  `grounding_report_v2.txt`.

**3. UI cleanup — done when:** (shipped in v1-rc1)
- Native <details>/<summary> collapsibles: constraints OPEN, policy OPEN, listed-buildings open
  only when there's something to see; documents CLOSED (count in summary); abstentions OPEN
  (cite-or-ABSTAIN is the pitch); summary strip up top. No framework, no redesign, no mobile pass.

**4. Packaging — done when the repo tells the story without you in the room:**
- README: live-eval results table, honest limitations section, and the P55→P56 checklist-typo
  story (curation caught an error in the council's own checklist — the 3-sentence case for
  grounding discipline).
- Screenshots of the three presets (constraints + policy sections open — the defaults).
- The live eval report + results JSON committed to the repo as the frozen v1 baseline.
- Final character-exact check of the P56/P61/P68 excerpts against the Plan PDF, and the OGL
  version printed on it (v2.0 vs v3.0) — these excerpts are simultaneously grounding ground-truth
  and user-facing "verbatim" claims.
- LICENSE (MIT) + .gitignore present (done). Tag v1.0 with short release notes.

## Session protocol

**Session A (local, Harry):**
1. Extract the rc1 zip; `pip install -r requirements.txt`.
2. `python test_policy_engine.py` → expect 14/14.
3. `uvicorn app:app --port 8000`, then `python smoke_test_week1.py` → expect 112/112
   (64 prior + 16 policy checks × 3 postcodes; run from the project root).
4. Browser pass 1, deterministic mode (no env flags): verify the collapsible UI + strip on the
   three presets. Any weirdness here is UI, not LLM.
5. Set the two env flags (screenshot-safe PowerShell block in the chat log / README).
6. `python eval\run_grounding_eval.py` → results JSON now self-documents model + prompts version.
7. Browser pass 2, live mode: spot-read explanations against their excerpts on the three presets.
8. Send: console report, grounding_eval_results.json, anything odd from pass 2.

**Session B:** read results together; classify failing rows (label → prompt → verifier); apply
stopping rules above. UI/smoke/logging work already landed in rc1, so B is results-only.

**Session C:** packaging per item 4; tag v1.0.

## Explicitly OUT of v1
Hosting/live demo · second scheme type (loft/dormer — the policy-map format is scheme-generic;
v1 ships single-storey rear extension end-to-end) · vector RAG · fixture-set growth · dimension /
threshold checking · PDF export · panel redesign.

## Current verification state (offline, sandbox)
- Engine tests: 14/14 · Smoke checks: 112 expected (policy checks tamper-proven offline)
- Eval harness: metric math self-tested; fixtures drift-checked against the live map
- Deterministic eval baseline: false-accept 0 (harness self-test, not a calibration)
- Live eval (2026-07-02, haiku-4.5, **prompts v2 — SHIPPED**): FA 0/12 · FR 0/6 · insufficient
  recall 5/6 · accuracy 94.4% · show/suppress 18/18. (v1 was 3/6 recall / 83.3%; see Iteration 1.)
