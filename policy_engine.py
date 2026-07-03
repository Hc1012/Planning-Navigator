"""
Planning Navigator - policy engine (cite-or-abstain).

Pipeline for ONE scheme (single-storey rear extension):
  1. resolve()          - DETERMINISTIC. The curated YAML map decides which policies apply
                          (base + any constraint-triggered by the site's constraints). No LLM here.
  2. explain (LLM)      - plain-English summary of a policy, grounded ONLY in its verbatim excerpt.
  3. verify  (LLM)      - three-way grounding check: SUPPORTED / INSUFFICIENT / CONTRADICTED
                          (the RepoVeritas verifier, applied to policy claims).
  4. assemble           - keep only SUPPORTED explanations; suppress the rest and abstain; attach
                          citations, required documents, standing abstentions, disclaimer.

The LLM is behind a small Protocol so the whole loop is testable offline with MockLLM.
Real runs use AnthropicLLM (needs ANTHROPIC_API_KEY); validate live, like the Week-1 API calls.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import yaml

DISCLAIMER = (
    "This surfaces relevant Southwark planning policies and required documents as context - "
    "it is not planning advice, and does not decide whether permission will be granted or whether "
    "your scheme complies. A planning officer makes that judgement."
)

# Grounding verdicts (kept as constants so callers/tests don't stringly-type).
SUPPORTED = "SUPPORTED"
INSUFFICIENT = "INSUFFICIENT"
CONTRADICTED = "CONTRADICTED"
_VERDICTS = {SUPPORTED, INSUFFICIENT, CONTRADICTED}


# ---------------------------------------------------------------------------
# Map loading
# ---------------------------------------------------------------------------
def _iter_policies(m: dict):
    """Yield every policy dict in the map (base + all constraint-triggered)."""
    yield from m.get("base_policies", []) or []
    for block in (m.get("constraint_triggered_policies") or {}).values():
        yield from block.get("policies", []) or []


def check_map_integrity(m: dict) -> None:
    """Raise ValueError if the gold-label map is malformed. Enforced on load so the YAML can't
    silently regress (dangling policy refs, unfilled TO_CURATE excerpts, empty excerpts)."""
    errors: list[str] = []
    if not m.get("scheme_type"):
        errors.append("missing scheme_type")
    if not m.get("base_policies"):
        errors.append("missing base_policies")
    for c, block in (m.get("constraint_triggered_policies") or {}).items():
        if not (isinstance(block.get("policies"), list) and block["policies"]):
            errors.append(f"constraint '{c}': missing policies[]")

    curated = {p.get("id") for p in _iter_policies(m)}
    for p in _iter_policies(m):
        pid = p.get("id")
        exc = str(p.get("excerpt", "") or "")
        if not exc.strip():
            errors.append(f"{pid}: empty excerpt")
        if "TO_CURATE" in exc:
            errors.append(f"{pid}: excerpt still marked TO_CURATE")
        if not p.get("title"):
            errors.append(f"{pid}: missing title")

    for bucket in ("always", "conditional"):
        for doc in (m.get("required_documents", {}) or {}).get(bucket, []) or []:
            for pid in doc.get("policies", []) or []:
                if pid not in curated:
                    errors.append(f"required_documents '{doc.get('name')}' references uncurated policy {pid}")

    if errors:
        raise ValueError("policy map integrity errors:\n  - " + "\n  - ".join(errors))


def load_map(path: str | Path) -> dict:
    with open(path) as f:
        m = yaml.safe_load(f)
    check_map_integrity(m)  # fail fast on a malformed gold-label file
    return m


def active_constraints_from_site(site_result: dict) -> set[str]:
    """Derive the constraint keys the map cares about from a /api/site response."""
    active: set[str] = set()
    for c in site_result.get("constraints", []) or []:
        ds = c.get("dataset")
        if ds:
            active.add(ds)
    # listed-building trigger = site overlaps an outline OR a listed building is nearby
    lb = site_result.get("listed_buildings") or {}
    if (lb.get("site_listing") or {}).get("overlaps") or (lb.get("count") or 0) > 0:
        active.add("listed-building")
    return active


# ---------------------------------------------------------------------------
# Step 1: deterministic resolution (no LLM)
# ---------------------------------------------------------------------------
def _plan_source(m: dict) -> dict:
    """The Southwark Plan DM-policies doc, used as the citation source for Plan policies."""
    for d in m.get("source_docs", []):
        if "development management policies" in d.get("name", "").lower():
            return {"source_url": d.get("url"), "source_version": d.get("version")}
    d = (m.get("source_docs") or [{}])[0]
    return {"source_url": d.get("url"), "source_version": d.get("version")}


def resolve(m: dict, active_constraints: set[str]) -> dict:
    """Return the applicable policies (deduped), required documents, and standing abstentions."""
    src = _plan_source(m)

    resolved: dict[str, dict] = {}      # id -> policy dict (dedupe; P21 recurs across constraints)
    triggered_by: dict[str, set[str]] = {}

    def add(policy: dict, trigger: str):
        pid = policy["id"]
        triggered_by.setdefault(pid, set())
        if trigger:
            triggered_by[pid].add(trigger)
        if pid not in resolved:
            resolved[pid] = {
                "id": pid,
                "title": policy.get("title"),
                "excerpt": policy.get("excerpt"),
                "status": policy.get("status"),
                "why": policy.get("why"),
                **src,
            }

    for p in m["base_policies"]:
        add(p, trigger="")  # base = always

    ctp = m.get("constraint_triggered_policies") or {}
    triggered_docs: list[dict] = []
    constraint_notes: list[str] = []
    for c in sorted(active_constraints):
        block = ctp.get(c)
        if not block:
            continue  # constraint present at site but no policy mapping (e.g. not in this slice)
        for p in block["policies"]:
            add(p, trigger=c)
        for doc in block.get("required_documents_added", []) or []:
            triggered_docs.append({"name": doc, "trigger": c})
        for note in block.get("notes", []) or []:
            constraint_notes.append(note)

    # tag each policy with what triggered it (base vs which constraint)
    policies = []
    for pid, pol in resolved.items():
        trig = sorted(triggered_by.get(pid, set()))
        pol["applies_because"] = "base policy (always)" if not trig else "triggered by: " + ", ".join(trig)
        policies.append(pol)

    required_documents = _assemble_documents(m, triggered_docs)

    return {
        "policies": policies,
        "required_documents": required_documents,
        "standing_abstentions": list(m.get("abstention_notes", []) or []),
        "constraint_notes": constraint_notes,
    }


def _assemble_documents(m: dict, triggered_docs: list[dict]) -> dict:
    always = list(m["required_documents"].get("always", []) or [])
    triggered_names = {d["name"].split(" (")[0].strip().lower() for d in triggered_docs}
    # conditional docs that are design-dependent (not already added by a constraint trigger)
    design_dependent = []
    for doc in m["required_documents"].get("conditional", []) or []:
        name_key = doc["name"].split(" (")[0].strip().lower()
        if name_key not in triggered_names:
            design_dependent.append(doc)
    return {
        "always": always,
        "triggered_by_constraints": triggered_docs,
        "design_dependent": design_dependent,   # "may also be needed, depending on your design"
    }


# ---------------------------------------------------------------------------
# Steps 2-3: the LLM (behind a Protocol so the loop is testable offline)
# ---------------------------------------------------------------------------
class LLM(Protocol):
    def explain(self, excerpt: str, scheme_type: str) -> str: ...
    def verify(self, excerpt: str, claim: str) -> str: ...  # returns one of _VERDICTS


# Bump this on ANY edit to _EXPLAIN_SYS or _VERIFY_SYS, so every eval result is attributable
# to the exact prompts that produced it (the v1 rule allows max two verify-prompt iterations).
# v2 (iteration 1): narrowed CONTRADICTED to strict incompatibility ("cannot both be true") and
# made INSUFFICIENT the explicit residual, after the 2026-07-02 haiku run read absence as negation
# on 3/6 insufficient fixtures (INSUFFICIENT->CONTRADICTED; binary show/suppress boundary was 18/18).
PROMPTS_VERSION = "v2"

_EXPLAIN_SYS = (
    "You explain a single UK planning policy to a homeowner planning a single-storey rear extension. "
    "You are given ONE short policy excerpt. Write 1-2 plain-English sentences on what it means for "
    "their project. Use ONLY what the excerpt supports - do NOT introduce requirements, thresholds, "
    "numbers, or obligations not present in the excerpt, and do NOT say whether permission will be "
    "granted. If the excerpt is too thin to explain meaningfully, reply with exactly: INSUFFICIENT."
)

_VERIFY_SYS = (
    "You are a strict grounding verifier for planning-policy explanations. You are given a POLICY "
    "EXCERPT (the only ground truth) and a CLAIM (a plain-English explanation). Decide whether the "
    "excerpt SUPPORTS the claim. Reply with exactly one label on the first line:\n"
    "SUPPORTED - every substantive assertion in the claim is directly supported by the excerpt.\n"
    "CONTRADICTED - the excerpt states something incompatible with the claim: the excerpt and the "
    "claim cannot both be true at the same time.\n"
    "INSUFFICIENT - everything else. The claim asserts requirements, numbers, procedures, or other "
    "specifics that the excerpt does not establish (even if they are plausibly true elsewhere). If "
    "the excerpt is merely SILENT on what the claim asserts, that is INSUFFICIENT, not CONTRADICTED "
    "- absence of support is never contradiction. A claim that attributes to the policy something "
    "this excerpt does not contain is INSUFFICIENT unless the excerpt states the opposite.\n"
    "Then one short reason line."
)


def parse_verdict(text: str) -> str:
    """Fail safe: anything unparseable -> INSUFFICIENT (abstain rather than assert)."""
    first = (text or "").strip().splitlines()[0].strip().upper() if (text or "").strip() else ""
    for v in _VERDICTS:
        if first.startswith(v):
            return v
    return INSUFFICIENT


class AnthropicLLM:
    """Production LLM. Needs ANTHROPIC_API_KEY. Model is configurable (small/cheap by default)."""

    def __init__(self, model: str | None = None, max_tokens: int = 300):
        from anthropic import Anthropic  # imported lazily so offline tests don't need the package
        self._client = Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self._max_tokens = max_tokens

    def _msg(self, system: str, user: str) -> str:
        r = self._client.messages.create(
            model=self.model, max_tokens=self._max_tokens,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()

    def explain(self, excerpt: str, scheme_type: str) -> str:
        return self._msg(_EXPLAIN_SYS, f"Scheme: {scheme_type}\nPolicy excerpt:\n\"{excerpt}\"")

    def verify(self, excerpt: str, claim: str) -> str:
        return parse_verdict(self._msg(_VERIFY_SYS, f"POLICY EXCERPT:\n\"{excerpt}\"\n\nCLAIM:\n\"{claim}\""))


class DeterministicLLM:
    """No-API fallback used for the MVP. Produces a faithful, grounded restatement of the excerpt
    (it invents nothing), so the full pipeline runs without a key. Swap in AnthropicLLM for real prose.
    Because the 'explanation' embeds the excerpt verbatim, it passes the grounding check trivially -
    which is honest: the citation IS the explanation until a real model paraphrases it."""

    def explain(self, excerpt: str, scheme_type: str) -> str:
        scheme = scheme_type.replace("-", " ")
        return f"For a {scheme}, this policy is relevant. In the Plan's words: \u201c{excerpt}\u201d"

    def verify(self, excerpt: str, claim: str) -> str:
        return SUPPORTED if excerpt and excerpt in claim else INSUFFICIENT


def default_llm() -> "LLM":
    """Use the real API only if explicitly opted in (PLANNING_USE_ANTHROPIC=1) AND a key is present;
    otherwise the deterministic fallback. This keeps the MVP key-free and avoids surprise API costs."""
    if os.environ.get("PLANNING_USE_ANTHROPIC") and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicLLM()
        except Exception as exc:  # pragma: no cover
            print(f"[policy_engine] Anthropic init failed ({exc}); using deterministic fallback")
    return DeterministicLLM()


# ---------------------------------------------------------------------------
# Step 4: orchestration (resolve -> explain -> verify -> assemble/abstain)
# ---------------------------------------------------------------------------
def build_policy_response(m: dict, active_constraints: set[str], llm: LLM) -> dict:
    r = resolve(m, active_constraints)
    dynamic_abstentions: list[str] = []
    applicable = []

    for pol in r["policies"]:
        excerpt = pol["excerpt"]
        explanation = (llm.explain(excerpt, m["scheme_type"]) or "").strip()

        if explanation.upper() == INSUFFICIENT:
            verdict, shown = INSUFFICIENT, None
        else:
            verdict = llm.verify(excerpt, explanation)
            shown = explanation if verdict == SUPPORTED else None

        if verdict != SUPPORTED:
            dynamic_abstentions.append(
                f"{pol['id']} ({pol['title']}): couldn't produce a confidently grounded summary "
                f"({verdict.lower()}) - read the policy text or ask a Southwark planning officer."
            )

        applicable.append({
            "id": pol["id"],
            "title": pol["title"],
            "applies_because": pol["applies_because"],
            "plain_english": shown,                 # None when not SUPPORTED -> UI shows excerpt + abstention
            "cited_excerpt": excerpt,               # verbatim; always shown so the user can check
            "source_url": pol["source_url"],
            "source_version": pol["source_version"],
            "excerpt_provenance": pol["status"],    # 'verified' vs 'verified-curator'
            "grounding_status": verdict,
        })

    return {
        "scheme_type": m["scheme_type"],
        "applicable_policies": applicable,
        "required_documents": r["required_documents"],
        "notes": r["constraint_notes"],             # e.g. listed-building consent, Article 4, flood caveat
        "abstentions": r["standing_abstentions"] + dynamic_abstentions,
        "attribution": (m.get("provenance_and_licence") or {}).get("attribution_required"),
        "disclaimer": DISCLAIMER,
    }
