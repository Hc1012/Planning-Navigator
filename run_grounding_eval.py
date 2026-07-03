"""
Grounding-verifier eval runner - a mini RepoVeritas for Planning Navigator's policy verifier.

What it does:
  1. Loads the hand-labelled fixtures (grounding_fixtures.yaml).
  2. Drift-checks each fixture's excerpt against the LIVE gold-label map - fails loudly if they diverge,
     so a change to the map can't silently invalidate the labels.
  3. Runs each (excerpt, claim) through whatever verifier is currently wired in (pe.default_llm():
     deterministic now, the live LLM once PLANNING_USE_ANTHROPIC=1 + ANTHROPIC_API_KEY are set).
  4. Reports a 3x3 confusion matrix, overall accuracy, per-class precision/recall, and - the numbers
     that matter for this product - the false-ACCEPT rate and false-REJECT rate.

Two error types, and why they are not symmetric:
  * FALSE-ACCEPT  = an ungrounded or contradicted claim the verifier waved through as SUPPORTED.
                    This is the DANGEROUS error: the user is shown a confident, wrong policy statement
                    as if it were grounded. For a cite-or-abstain tool this should be ~0.
  * FALSE-REJECT  = a genuinely grounded claim the verifier suppressed (INSUFFICIENT/CONTRADICTED).
                    This is the SAFE-but-annoying error: an unnecessary abstention. Tolerable; too many
                    just make the tool timid.

Run:
    python run_grounding_eval.py                 # scores whatever LLM is wired in
    PLANNING_USE_ANTHROPIC=1 ANTHROPIC_API_KEY=... python run_grounding_eval.py   # live verifier
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import policy_engine as pe  # noqa: E402

FIXTURES_PATH = Path(__file__).parent / "grounding_fixtures.yaml"
RESULTS_PATH = Path(__file__).parent / "grounding_eval_results.json"
MAP_PATH = ROOT / "single_storey_rear_extension.yaml"

LABELS = [pe.SUPPORTED, pe.INSUFFICIENT, pe.CONTRADICTED]
_GOLD = {"supported": pe.SUPPORTED, "insufficient": pe.INSUFFICIENT, "contradicted": pe.CONTRADICTED}


# ---------------------------------------------------------------------------
# Fixtures + drift check
# ---------------------------------------------------------------------------
def load_fixtures() -> list[dict]:
    data = yaml.safe_load(open(FIXTURES_PATH))
    fixtures = data["fixtures"]
    seen = set()
    for f in fixtures:
        for key in ("id", "policy_id", "excerpt", "claim", "gold"):
            assert f.get(key) not in (None, ""), f"fixture missing {key}: {f}"
        assert f["gold"] in _GOLD, f"{f['id']}: bad gold label '{f['gold']}'"
        assert f["id"] not in seen, f"duplicate fixture id {f['id']}"
        seen.add(f["id"])
    return fixtures


def drift_check(fixtures: list[dict], m: dict) -> None:
    """Every fixture excerpt must match the map's excerpt for that policy_id, verbatim."""
    map_excerpt = {}
    for p in pe._iter_policies(m):
        map_excerpt.setdefault(p["id"], p.get("excerpt"))
    errors = []
    for f in fixtures:
        want = map_excerpt.get(f["policy_id"])
        if want is None:
            errors.append(f"{f['id']}: policy {f['policy_id']} not in map")
        elif f["excerpt"] != want:
            errors.append(
                f"{f['id']}: excerpt drift vs map for {f['policy_id']}\n"
                f"    fixture: {f['excerpt']!r}\n    map    : {want!r}"
            )
    if errors:
        raise ValueError("fixture/map drift - labels may be stale:\n  - " + "\n  - ".join(errors))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(rows: list[dict]) -> dict:
    n = len(rows)
    conf = {g: {p: 0 for p in LABELS} for g in LABELS}
    for r in rows:
        conf[r["gold"]][r["pred"]] += 1

    correct = sum(conf[c][c] for c in LABELS)
    accuracy = correct / n if n else 0.0

    per_class = {}
    for c in LABELS:
        gold_total = sum(conf[c][p] for p in LABELS)   # actual c
        pred_total = sum(conf[g][c] for g in LABELS)   # predicted c
        tp = conf[c][c]
        per_class[c] = {
            "support": gold_total,
            "recall": (tp / gold_total) if gold_total else None,
            "precision": (tp / pred_total) if pred_total else None,
        }

    not_supported = [r for r in rows if r["gold"] in (pe.INSUFFICIENT, pe.CONTRADICTED)]
    false_accept = sum(1 for r in not_supported if r["pred"] == pe.SUPPORTED)
    supported = [r for r in rows if r["gold"] == pe.SUPPORTED]
    false_reject = sum(1 for r in supported if r["pred"] != pe.SUPPORTED)

    return {
        "n": n,
        "accuracy": accuracy,
        "confusion": conf,
        "per_class": per_class,
        "false_accept": false_accept,
        "false_accept_rate": (false_accept / len(not_supported)) if not_supported else None,
        "false_reject": false_reject,
        "false_reject_rate": (false_reject / len(supported)) if supported else None,
    }


def _selftest_metrics() -> None:
    """Verify the metric math on synthetic predictions (independent of any real verifier)."""
    S, I, C = pe.SUPPORTED, pe.INSUFFICIENT, pe.CONTRADICTED
    rows = [
        {"gold": S, "pred": S},              # correct
        {"gold": S, "pred": I},              # false-reject
        {"gold": I, "pred": I},              # correct
        {"gold": I, "pred": S},              # false-accept
        {"gold": C, "pred": C},              # correct
        {"gold": C, "pred": S},              # false-accept
    ]
    m = compute_metrics(rows)
    assert m["n"] == 6
    assert abs(m["accuracy"] - 3 / 6) < 1e-9
    assert m["false_accept"] == 2 and abs(m["false_accept_rate"] - 2 / 4) < 1e-9  # 2 of 4 not-supported
    assert m["false_reject"] == 1 and abs(m["false_reject_rate"] - 1 / 2) < 1e-9  # 1 of 2 supported
    assert m["per_class"][S]["recall"] == 0.5
    assert m["per_class"][I]["recall"] == 0.5 and m["per_class"][C]["recall"] == 0.5


# ---------------------------------------------------------------------------
# Run + report
# ---------------------------------------------------------------------------
def run_eval(fixtures: list[dict], llm) -> list[dict]:
    rows = []
    for f in fixtures:
        row = {"id": f["id"], "policy_id": f["policy_id"], "gold": _GOLD[f["gold"]], "error": None}
        try:
            row["pred"] = llm.verify(f["excerpt"], f["claim"])
        except Exception as exc:  # don't let one call kill the run; fail safe to abstain
            row["pred"] = pe.INSUFFICIENT
            row["error"] = str(exc)
        rows.append(row)
    return rows


def _pct(x):
    return "  n/a" if x is None else f"{100 * x:5.1f}%"


def format_report(rows, metrics, llm_name, model=None) -> str:
    out = []
    label = f"{llm_name} ({model})" if model else llm_name
    out.append(f"Grounding-verifier eval  ·  verifier = {label}  ·  prompts {pe.PROMPTS_VERSION}  ·  {metrics['n']} fixtures")
    out.append("=" * 64)

    # per-fixture
    for r in rows:
        mark = "PASS" if r["pred"] == r["gold"] else "FAIL"
        err = f"  [error: {r['error']}]" if r["error"] else ""
        out.append(f"  {mark}  {r['id']:<14} gold={r['gold']:<12} pred={r['pred']:<12}{err}")

    # confusion matrix
    out.append("")
    out.append("Confusion matrix  (rows = gold, cols = predicted)")
    header = " " * 14 + "".join(f"{p[:5]:>8}" for p in LABELS)
    out.append(header)
    for g in LABELS:
        out.append(f"  {g:<12}" + "".join(f"{metrics['confusion'][g][p]:>8}" for p in LABELS))

    # per-class
    out.append("")
    out.append("Per-class            precision   recall   support")
    for c in LABELS:
        pc = metrics["per_class"][c]
        out.append(f"  {c:<12}      {_pct(pc['precision'])}   {_pct(pc['recall'])}   {pc['support']:>7}")

    # headline
    out.append("")
    out.append(f"Accuracy            : {_pct(metrics['accuracy'])}  ({metrics['n']} fixtures)")
    out.append(f"False-ACCEPT (unsafe): {metrics['false_accept']}  -> rate {_pct(metrics['false_accept_rate'])}"
               "   (ungrounded/contradicted claims shown as SUPPORTED - want 0)")
    out.append(f"False-REJECT (timid) : {metrics['false_reject']}  -> rate {_pct(metrics['false_reject_rate'])}"
               "   (grounded claims wrongly suppressed)")

    n_err = sum(1 for r in rows if r["error"])
    if n_err:
        out.append(f"\n! {n_err} verifier call(s) errored and were counted as INSUFFICIENT - see rows above.")

    if "Deterministic" in llm_name:
        out.append(
            "\nNOTE: this is the DETERMINISTIC fallback, not a real semantic verifier. It only returns\n"
            "SUPPORTED when the excerpt appears verbatim in the claim, so it abstains on every paraphrase.\n"
            "These numbers are a self-test that the harness runs end to end - real calibration needs the\n"
            "live LLM (PLANNING_USE_ANTHROPIC=1 + ANTHROPIC_API_KEY)."
        )
    return "\n".join(out)


def main() -> int:
    _selftest_metrics()
    fixtures = load_fixtures()
    m = pe.load_map(MAP_PATH)
    drift_check(fixtures, m)

    llm = pe.default_llm()
    llm_name = type(llm).__name__
    model = getattr(llm, "model", None)  # AnthropicLLM exposes the model string; deterministic has none
    rows = run_eval(fixtures, llm)
    metrics = compute_metrics(rows)

    print(format_report(rows, metrics, llm_name, model))

    RESULTS_PATH.write_text(json.dumps({
        "verifier": llm_name,
        "model": model,
        "prompts_version": pe.PROMPTS_VERSION,
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": metrics,
        "rows": rows,
    }, indent=2))
    print(f"\nWrote {RESULTS_PATH.relative_to(ROOT)}")

    # CI-style signal: the safety-critical criterion is zero false-accepts.
    ok = metrics["false_accept"] == 0
    print(f"\nSafety criterion (false-accept == 0): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
