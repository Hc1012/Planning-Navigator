"""Offline tests for policy_engine (no API key needed; MockLLM stands in for Anthropic)."""
import policy_engine as pe

MAP = pe.load_map("single_storey_rear_extension.yaml")


class MockLLM:
    """behaviour: excerpt-substring -> (explanation, verdict). Defaults: generic explanation + SUPPORTED."""
    def __init__(self, behaviour=None):
        self.behaviour = behaviour or {}

    def explain(self, excerpt, scheme_type):
        for key, (expl, _) in self.behaviour.items():
            if key in excerpt:
                return expl
        return "In plain terms, this policy shapes how your extension is assessed."

    def verify(self, excerpt, claim):
        for key, (_, verdict) in self.behaviour.items():
            if key in excerpt:
                return verdict
        return pe.SUPPORTED


def ids(policies):
    return sorted(p["id"] for p in policies)


def test_resolve_base_only():
    r = pe.resolve(MAP, set())
    assert ids(r["policies"]) == ["P13", "P14", "P15", "P56"]
    assert all("base policy" in p["applies_because"] for p in r["policies"])
    # no constraint-triggered docs; design-dependent docs surfaced; always docs present
    assert r["required_documents"]["triggered_by_constraints"] == []
    assert any(d["name"].startswith("Application form") for d in r["required_documents"]["always"])
    print("[PASS] resolve base-only -> P13/P14/P15/P56, no triggered docs")


def test_resolve_conservation_area_adds_p20_and_das():
    r = pe.resolve(MAP, {"conservation-area"})
    assert "P20" in ids(r["policies"])
    trig_names = [d["name"] for d in r["required_documents"]["triggered_by_constraints"]]
    assert any("Design & access statement" in n for n in trig_names), trig_names
    # D&A statement must NOT be double-listed under design_dependent
    dd_names = [d["name"] for d in r["required_documents"]["design_dependent"]]
    assert not any(n.startswith("Design & access statement") for n in dd_names), dd_names
    print("[PASS] conservation-area -> adds P20 + D&A statement (no double-listing)")


def test_p21_dedupes_across_constraints():
    r = pe.resolve(MAP, {"tree-preservation-zone", "scheduled-monument"})
    p21 = [p for p in r["policies"] if p["id"] == "P21"]
    assert len(p21) == 1, "P21 must appear once even if two constraints trigger it"
    because = p21[0]["applies_because"]
    assert "scheduled-monument" in because and "tree-preservation-zone" in because, because
    print("[PASS] P21 deduped; applies_because lists both triggering constraints")


def test_unknown_constraint_ignored():
    r = pe.resolve(MAP, {"some-future-constraint"})
    assert ids(r["policies"]) == ["P13", "P14", "P15", "P56"]  # base only; unknown ignored gracefully
    print("[PASS] constraint with no mapping is ignored (base policies still resolve)")


def test_active_constraints_from_site():
    site = {
        "constraints": [{"dataset": "conservation-area"}, {"dataset": "article-4-direction-area"}],
        "listed_buildings": {"site_listing": {"overlaps": False}, "count": 2},  # nearby -> listed-building active
    }
    ac = pe.active_constraints_from_site(site)
    assert ac == {"conservation-area", "article-4-direction-area", "listed-building"}, ac
    # overlap-only also triggers listed-building
    site2 = {"constraints": [], "listed_buildings": {"site_listing": {"overlaps": True}, "count": 0}}
    assert pe.active_constraints_from_site(site2) == {"listed-building"}
    print("[PASS] active_constraints_from_site: datasets + listed-building (overlap OR nearby)")


def test_full_loop_all_supported():
    resp = pe.build_policy_response(MAP, {"conservation-area"}, MockLLM())
    assert resp["scheme_type"] == "single-storey-rear-extension"
    for p in resp["applicable_policies"]:
        assert p["grounding_status"] == pe.SUPPORTED
        assert p["plain_english"] is not None          # supported -> shown
        assert p["cited_excerpt"]                       # verbatim always present
        assert p["source_url"] and p["source_version"]
    # abstentions here = standing (map) only, no dynamic ones
    assert resp["abstentions"] == list(MAP["abstention_notes"])
    assert resp["disclaimer"] and resp["attribution"]
    print("[PASS] full loop, all SUPPORTED -> explanations shown, only standing abstentions")


def test_full_loop_suppresses_ungrounded():
    # P56 excerpt -> verify returns INSUFFICIENT; P13 excerpt -> explain itself returns INSUFFICIENT
    behaviour = {
        "unacceptable loss of amenity": ("Some plausible but unsupported claim.", pe.INSUFFICIENT),  # P56
        "respond positively to the existing townscape": ("INSUFFICIENT", pe.SUPPORTED),               # P13 explain abstains
    }
    resp = pe.build_policy_response(MAP, set(), MockLLM(behaviour))
    by_id = {p["id"]: p for p in resp["applicable_policies"]}
    # suppressed: plain_english None, but excerpt still shown
    assert by_id["P56"]["plain_english"] is None and by_id["P56"]["grounding_status"] == pe.INSUFFICIENT
    assert by_id["P56"]["cited_excerpt"]
    assert by_id["P13"]["plain_english"] is None and by_id["P13"]["grounding_status"] == pe.INSUFFICIENT
    # supported ones still shown
    assert by_id["P14"]["plain_english"] is not None and by_id["P14"]["grounding_status"] == pe.SUPPORTED
    # dynamic abstentions added for P13 and P56, on top of standing ones
    joined = " ".join(resp["abstentions"])
    assert "P56" in joined and "P13" in joined
    assert len(resp["abstentions"]) > len(MAP["abstention_notes"])
    print("[PASS] ungrounded claims suppressed + abstained; verbatim excerpt still shown")


def test_parse_verdict_failsafe():
    assert pe.parse_verdict("SUPPORTED - looks fine") == pe.SUPPORTED
    assert pe.parse_verdict("contradicted: nope") == pe.CONTRADICTED
    assert pe.parse_verdict("") == pe.INSUFFICIENT           # empty -> abstain
    assert pe.parse_verdict("hmm, maybe?") == pe.INSUFFICIENT  # unparseable -> abstain
    print("[PASS] parse_verdict fail-safe (unparseable -> INSUFFICIENT)")


def test_full_loop_contradicted_is_suppressed():
    # P20 excerpt -> the model produces a claim the excerpt CONTRADICTS
    behaviour = {
        "preserves or enhances the character or appearance": ("This policy lets you do whatever you like.", pe.CONTRADICTED),  # P20
    }
    resp = pe.build_policy_response(MAP, {"conservation-area"}, MockLLM(behaviour))
    by_id = {p["id"]: p for p in resp["applicable_policies"]}
    assert by_id["P20"]["grounding_status"] == pe.CONTRADICTED
    assert by_id["P20"]["plain_english"] is None            # contradicted -> suppressed, same as insufficient
    assert by_id["P20"]["cited_excerpt"]                    # verbatim still shown
    assert any("P20" in a for a in resp["abstentions"])     # dynamic abstention added
    # a sibling supported policy is unaffected
    assert by_id["P13"]["plain_english"] is not None and by_id["P13"]["grounding_status"] == pe.SUPPORTED
    print("[PASS] CONTRADICTED explanation suppressed + abstained (like INSUFFICIENT)")


def _minimal_map(excerpt="a valid clause", doc_policies=("P13",)):
    return {
        "scheme_type": "single-storey-rear-extension",
        "base_policies": [{"id": "P13", "title": "Design of places", "excerpt": excerpt, "status": "verified"}],
        "constraint_triggered_policies": {},
        "required_documents": {"always": [{"name": "Application form"}],
                               "conditional": [{"name": "Some doc", "policies": list(doc_policies)}]},
    }


def test_map_integrity_valid_passes():
    pe.check_map_integrity(_minimal_map())  # should not raise
    print("[PASS] map integrity: a valid minimal map passes")


def test_map_integrity_catches_dangling_ref():
    try:
        pe.check_map_integrity(_minimal_map(doc_policies=("P99",)))  # P99 not curated
        raise AssertionError("expected ValueError for dangling policy ref")
    except ValueError as e:
        assert "P99" in str(e) and "uncurated" in str(e)
    print("[PASS] map integrity: dangling required_documents policy ref -> ValueError")


def test_map_integrity_catches_to_curate_and_empty():
    try:
        pe.check_map_integrity(_minimal_map(excerpt="TO_CURATE - pull from PDF"))
        raise AssertionError("expected ValueError for TO_CURATE excerpt")
    except ValueError as e:
        assert "TO_CURATE" in str(e)
    try:
        pe.check_map_integrity(_minimal_map(excerpt="   "))
        raise AssertionError("expected ValueError for empty excerpt")
    except ValueError as e:
        assert "empty excerpt" in str(e)
    print("[PASS] map integrity: TO_CURATE / empty excerpt -> ValueError")


def test_real_map_passes_integrity():
    pe.check_map_integrity(MAP)  # the shipped gold-label map must be clean
    print("[PASS] map integrity: the real single-storey-rear-extension map is clean")


def test_deterministic_fallback_grounds():
    resp = pe.build_policy_response(MAP, set(), pe.DeterministicLLM())
    for p in resp["applicable_policies"]:
        assert p["grounding_status"] == pe.SUPPORTED           # faithful restatement -> grounded
        assert p["plain_english"] and p["cited_excerpt"] in p["plain_english"]  # excerpt embedded verbatim
    print("[PASS] deterministic fallback: every policy explained + grounded (excerpt embedded)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} POLICY-ENGINE TESTS PASSED")
