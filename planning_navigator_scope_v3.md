# Planning Navigator — Scope v3: Policy layer (cite-or-abstain)

## Status / boundary
Heritage slice (v2) is green. This is the policy layer. **One scheme type only —
single-storey rear extension.** No loft/dormer until this works end to end. And
**no RAG-for-relevance**: that is architecturally excluded, not merely deferred.

## The core principle
The "which policies apply" decision is **not** made by a model. Southwark's own
Householder SPD + validation checklist already *are* a scheme→requirements mapping; we
encode that mapping once, by hand, verified against the source. Retrieval + LLM do only
two bounded jobs: (1) surface the exact cited policy text, (2) explain it in plain
English — each explanation gated by a grounding check. This is the RepoVeritas lesson
transferred directly: **the curated map is the gold labels; the grounding check is the
verifier; the _insufficient_ class is the abstention.**

## Grounding in the real documents (verified against live Southwark sources)
Three real sources, cited-and-linked (never reproduced wholesale):
- **Southwark Plan 2022** — the statutory development-management policies. Adopted
  23 Feb 2022, plan period 2019–2036.
  Policy page: https://www.southwark.gov.uk/planning-environment-and-building-control/planning/planning-policy-and-guidance/southwark-plan-2022
  DM policies PDF: https://services.southwark.gov.uk/assets/attach/233497/Southwark-Plan-development-management-policies.pdf
- **Householder SPD** — guidance for *applying* the Plan's policies to home
  alterations/extensions; explicitly "does not contain new policy"; a material
  consideration in decisions.
  PDF: https://moderngov.southwark.gov.uk/documents/s126754/Householder%20SPD.pdf
  **Verify at curation:** sources show both a *draft* and a later version — confirm
  adopted-vs-draft status and its date before stamping the version.
- **Householder validation checklist** — the required-documents list per householder
  application, which itself cites the Plan policy numbers.
  https://www.southwark.gov.uk/planning-environment-and-building-control/planning/planning-checklists-householder-application

**Raw material for the map (from the official checklist):** householder applications map
to the design/amenity policy cluster **P13, P14, P15, P16, P17, P18, P19, P20, P21, P23,
P24, P25, P26**, plus **P56** (drawings/context), **P68** (noise), **P59/P61** (cycle
parking), and explicit triggers such as "design & access statement if in a conservation
area," tree protection for TPO / conservation-area trees, and archaeological-priority-area
assessment. **Note:** these are the real policy *numbers*; their *titles and exact text*
are NOT assumed here — verifying each and narrowing to the single-storey-rear-extension
subset is step 1 of the build (the gold-label step).

## The curated map (the heart of the system)
A hand-authored, versioned, reviewable file in the repo, e.g.
`single_storey_rear_extension.yaml`:
```
scheme_type: single-storey-rear-extension
source_docs:
  - {name: "Southwark Plan 2022", version: "adopted 2022-02-23", url: ...}
  - {name: "Householder SPD",     version: "<confirm draft/adopted + date>", url: ...}
  - {name: "Householder validation checklist", version: "<retrieved YYYY-MM-DD>", url: ...}
base_policies:                    # always apply to this scheme
  - {id: "P13", title: "<verified>", url: "<link incl. page/section>", why: "<one line, human>"}
  - ...
constraint_triggered_policies:    # apply ONLY if the site has this constraint (from v1/v2 layer)
  conservation-area:        [ {id, title, url, why}, ... ]   # + required: design & access statement
  article-4-direction-area: [ ... ]                          # + the PD-vs-application caveat (below)
  listed-building:          [ ... ]                          # overlap/nearby; point to Historic England; abstain on specifics
  flood-risk-zone:          [ ... ]                          # + flood risk assessment
  tree-preservation-zone:   [ ... ]                          # + arboricultural report / tree-work consent
required_documents:               # from the validation checklist
  - {name: "...", when: "always|conservation-area|...", source: "checklist", url: ...}
abstention_notes:                 # known gaps -> "not specifically addressed; ask an officer"
  - "..."
policy_store:                     # verbatim SHORT excerpts, keyed by policy id (hand-curated for slice 1)
  P13: {excerpt: "<short verbatim quote>", url: ..., version: ...}
```
Properties: **small, hand-verified, git-tracked, auditable** (a reviewer can check it line
by line against the SPD) and **version-stamped** (source updates → re-verify).

## Retrieval design — and why (almost) no vector DB
Because policies are looked up **by known ID** (the map decides applicability), retrieval
is a **keyed lookup** into `policy_store`, not semantic relevance ranking. For slice 1 the
store is **hand-curated verbatim excerpts** (short, cited) — so there is **no PDF-parsing
pipeline and no vector index** yet. This is deliberate: it removes the two biggest
hallucination/fragility surfaces at once (relevance drift + parser errors) and proves the
cite-or-abstain + grounding loop on real, hand-verified text. Automated ingestion (parse
the official PDFs into an ID-keyed store) is a *later* step, added only once the loop is
proven — the same "prove the loop first" discipline as Week 1's hand-wired plumbing.
Embeddings enter only if a concrete need appears (e.g. selecting a sub-clause within a long
policy) — never for deciding applicability.

## The generation + grounding loop (the RepoVeritas transfer)
For each applicable policy:
1. **Explain** — an LLM writes a plain-English summary given ONLY the verbatim excerpt as
   evidence, instructed to ground every clause in it and to say nothing the excerpt doesn't
   support.
2. **Grounding check** — a second pass classifies the explanation against the excerpt as
   **supported / insufficient / contradicted** (the exact three-way label from RepoVeritas).
   Anything not `supported` is suppressed or softened; `contradicted` is a hard fail (logged).
3. **Output per policy** — {id, title, plain_english (grounded claims only), cited_excerpt
   (verbatim, short), source_url + version, grounding_status}. The user sees only verified
   explanations plus the verbatim text they can click through to check — **citations are the
   evidence spans.**

This is your verification methodology operating **inside** the product — and the strongest
portfolio thread: the tool eats the research's dog food.

## Abstention design (the hardest calibration — your _insufficient_ class)
Two honest triggers:
- **Uncovered feature** — the scheme has an aspect the map/documents don't address →
  "not specifically addressed in the Plan/SPD for this scheme — check with a Southwark
  planning officer." Never fabricate a policy to fill the gap.
- **Ungrounded claim** — an explanation fails the grounding check → suppress it, show the
  verbatim policy text, and say "we can't confidently summarise this — read the policy or
  ask an officer."

Calibration is the real work: **over-abstention is as wrong as hallucination** (your
benchmark's core finding). Base policies always resolve (the map guarantees them), so the
tool is never empty; abstain only on genuine gaps or ungrounded claims. Framing throughout:
the tool surfaces *relevant policies, what they say, and required documents* — it does
**not** decide whether permission will be granted or whether the scheme complies. That is
the officer's job.

## How the constraint layer (v1/v2) feeds this
The constraints already surfaced drive `constraint_triggered_policies`, making the output
site-specific:
- **conservation-area** → conservation/design-in-heritage policies + "design & access
  statement" required doc.
- **article-4-direction-area** → the big branch: an Article 4 direction can remove
  permitted-development rights, so a rear extension that is often PD may need a **full
  application** here. Surface as **context** and **abstain on the definitive call** — it
  depends on the specific direction's scope. (The SPD also notes PD never applies to
  flats/maisonettes — another honest branch to surface.)
- **listed-building overlap/nearby** → listed building consent likely relevant; surface +
  point to Historic England; abstain on specifics.
- **flood-risk-zone** → flood risk assessment requirement.
- **tree-preservation-zone / trees in conservation areas** → arboricultural report /
  separate tree-work consent.

## Output contract (additions)
`policy`: { scheme_type, applicable_policies: [ {id, title, plain_english, cited_excerpt,
source_url, source_version, grounding_status} ], required_documents: [ {name, when, url} ],
abstentions: [str], disclaimer }. Constraint-triggered items appear only when the matching
constraint is present in the site result.

## UI
A "Planning policy — single-storey rear extension" section: required-documents checklist
first, then per-policy cards (title, plain-English, a "what the policy says" verbatim
snippet with source link + **version badge**; ungrounded claims already suppressed).
Abstentions shown plainly. Scheme selector = a dropdown with the one scheme now
(extensible). "Not planning advice / the officer decides" framing kept prominent.
**Also fold in the v2 badge-wrap CSS fix when we touch the frontend.**

## Copyright / provenance
Short verbatim excerpts only; paraphrase for the plain-English; **link to the official
Southwark source** with a version stamp; never reproduce whole policies or the SPD. This is
both the legal line and the cite-or-abstain ethos.

## Decisions to confirm (recommendation in brackets)
1. **Ingestion for slice 1: hand-curated `policy_store` vs. a PDF pipeline?**
   *[Hand-curated — prove the loop on real, verified text; automate ingestion later. The big one.]*
2. **Vector DB?** *[No — keyed lookup by policy ID. Follows from #1.]*
3. **Grounding verifier = LLM entailment, three-way supported/insufficient/contradicted?**
   *[Yes — it is literally your RepoVeritas method; a small/cheap model for both explain + verify.]*
4. **Article 4 → PD-vs-application:** *[Surface as context + abstain on the definitive call.]*
5. **Dimension/threshold checking** (e.g. "your 4 m extension vs a 3 m guideline")?
   *[OUT for slice 1 — needs user-entered dimensions + reliable threshold extraction = high
   hallucination risk. Surface the guidance text; let user/officer apply it. Revisit later, carefully grounded.]*

## Out of scope (deliberately)
Loft/dormer or any second scheme; compliance/approval prediction; setting-harm judgement;
dimension/threshold verdicts; automated PDF ingestion + vector index; multi-borough. And,
architecturally, RAG deciding relevance — excluded, not deferred.

## Test plan (non-brittle + the new grounding/abstention checks)
- **Deterministic map lookup** — unit-test that the scheme resolves to its base policies,
  and that adding a *mocked* constraint (e.g. conservation-area) adds the right triggered
  policies + required docs. No live postcode needed.
- **Grounding-verifier fixtures** (a mini RepoVeritas eval) — (supported claim, passage) →
  supported; (fabricated claim, passage) → insufficient/contradicted. The methodology as a test.
- **Abstention** — a scheme feature deliberately outside the map → abstention emitted, no
  fabricated policy.
- **Pipeline invariant** — every *shown* claim has grounding_status == supported (ungrounded
  suppressed); every policy card has a source_url + version + verbatim excerpt;
  required_documents non-empty.
- **Non-brittle** — do NOT assert exact LLM explanation text or live policy counts; assert
  structure, that curated IDs resolve, and that the grounding gate behaves. LLM
  non-determinism is bounded by the gate.

## Phasing (once you say go)
1. Curate + **verify** the map for single-storey rear extension (titles + short excerpts +
   URLs against the live Plan / SPD / checklist) — the gold-label step.
2. Backend: deterministic map lookup + explain + grounding verifier + abstention, wired to
   the existing constraint result.
3. UI section + scheme selector + the v2 badge-wrap fix.
4. Extend the smoke test with map/grounding/abstention checks + verifier fixtures.

Same flow as before: scope (this) → clean reviewable code → tests. Prove cite-or-abstain
end to end on one scheme, then add loft/dormer.

## Effort
Larger than v2 (the LLM loop + verifier + curation), but bounded by the one-scheme scope:
curation ~a focused session; backend loop ~a day; UI + tests ~half a day.
