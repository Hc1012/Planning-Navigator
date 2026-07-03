#!/usr/bin/env python3
"""
Smoke test for Planning Navigator (Week 1 plumbing + heritage slice).

Run the server first, then this from the project root:
    uvicorn app:app --port 8000
    python smoke_test_week1.py

Philosophy (unchanged): assert INVARIANTS that hold regardless of data coverage.
Never assert specific constraint/listed counts for a postcode - that is centroid/
coverage-dependent and would false-fail a working pipeline. Counts are printed as INFO.
"""
import sys
import time

import httpx

BASE = "http://localhost:8000"
POSTCODES = ["SE21 7BG", "SE1 9TG", "SE15 5JR"]
EXPECTED_FIVE = {
    "conservation-area", "article-4-direction-area", "tree-preservation-zone",
    "flood-risk-zone", "scheduled-monument",
}
COMPASS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
ENTITY_PREFIX = "https://www.planning.data.gov.uk/entity/"
LONDON_BBOX = (51.2, 51.8, -0.6, 0.4)

VERDICTS = {"SUPPORTED", "INSUFFICIENT", "CONTRADICTED"}
BASE_POLICY_IDS = {"P13", "P14", "P15", "P56"}
PROVENANCE = {"verified", "verified-curator"}

try:
    import app
    import policy_engine as pe
    ALLOWED = set(app.CONSTRAINT_DATASETS)
    RADIUS = app.LISTED_RADIUS_M
    HAVE_APP = True
except Exception:
    ALLOWED = set(EXPECTED_FIVE)
    RADIUS = 100
    HAVE_APP = False

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""))


def config_check():
    print("Config:")
    if not HAVE_APP:
        print("  [SKIP] couldn't import app - run from the project root to enable this check")
        return
    # listed-building POINTS must NOT be in the within/point query (they'd never match);
    # they are handled by the bbox path instead. This is still the correct invariant.
    check("listed-building NOT in the within/point query", "listed-building" not in ALLOWED, f"queried={sorted(ALLOWED)}")
    check("the five expected polygon datasets are queried", EXPECTED_FIVE.issubset(ALLOWED), f"queried={sorted(ALLOWED)}")
    check("listed-building radius is 100 m", RADIUS == 100, f"radius={RADIUS}")


def ui_check():
    print("\nUI (GET /):")
    try:
        html = httpx.get(BASE + "/", timeout=15).text
    except Exception as e:
        check("GET / reachable", False, str(e)); return
    check("coverage warning present", 'id="warn"' in html)
    # guardrail wording must be present in the shipped frontend
    check("outline guardrail wording present", "appears to overlap this point" in html and "indicative only" in html)
    check("partial-coverage guardrail wording present", "coverage is partial" in html.lower())
    check("context-not-advice wording present", "for the council to decide" in html)


def site_check(pc):
    print(f"\n/api/site for {pc!r}:")
    try:
        r = httpx.get(BASE + "/api/site", params={"postcode": pc}, timeout=30)
    except Exception as e:
        check(f"{pc}: request reached server", False, str(e)); return
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:120]
        check(f"{pc}: HTTP 200", False, f"HTTP {r.status_code} - {detail}  (a 404 may be a transient postcodes.io issue)")
        return
    check(f"{pc}: HTTP 200", True)
    data = r.json()

    # ---- Week 1 invariants ----
    check(f"{pc}: response shape", all(k in data for k in ("site", "count", "constraints", "geojson", "warnings", "listed_buildings")))
    site = data.get("site", {})
    lat, lng = site.get("latitude"), site.get("longitude")
    in_box = (lat is not None and lng is not None
              and LONDON_BBOX[0] <= lat <= LONDON_BBOX[1] and LONDON_BBOX[2] <= lng <= LONDON_BBOX[3])
    check(f"{pc}: geocoded inside Greater London", in_box, f"lat={lat}, lng={lng}")

    cons = data.get("constraints", [])
    datasets = sorted({c.get("dataset") for c in cons})
    check(f"{pc}: only queried datasets in constraints", all(c.get("dataset") in ALLOWED for c in cons), f"datasets={datasets}")
    check(f"{pc}: NO listed-building in constraints list", all(c.get("dataset") not in ("listed-building", "listed-building-outline") for c in cons))
    feats = (data.get("geojson") or {}).get("features", []) or []
    # geojson (request A) may include outline features that aren't in the constraints list
    check(f"{pc}: geojson feature count >= constraint count", len(feats) >= data.get("count"), f"features={len(feats)}, count={data.get('count')}")
    warnings = data.get("warnings") or []
    check(f"{pc}: coverage warning in response", bool(warnings) and "NOT mean" in warnings[0])

    # ---- Heritage-slice invariants (structure only, no count assertions) ----
    lb = data.get("listed_buildings") or {}
    check(f"{pc}: listed_buildings shape", all(k in lb for k in ("radius_m", "available", "site_listing", "nearby", "count", "geojson")))
    check(f"{pc}: radius_m == 100", lb.get("radius_m") == 100, f"radius_m={lb.get('radius_m')}")
    check(f"{pc}: bbox path available", lb.get("available") is True)

    sl = lb.get("site_listing") or {}
    check(f"{pc}: site_listing.overlaps is bool", isinstance(sl.get("overlaps"), bool))
    check(f"{pc}: if overlaps, matches non-empty", (not sl.get("overlaps")) or bool(sl.get("matches")))

    nearby = lb.get("nearby") or []
    dist_ok = all(isinstance(n.get("distance_m"), (int, float)) and 0 <= n["distance_m"] <= lb.get("radius_m", 100) for n in nearby)
    check(f"{pc}: nearby distances within radius", dist_ok, f"n={len(nearby)}")
    check(f"{pc}: nearby bearings valid", all(n.get("bearing") in COMPASS for n in nearby))
    check(f"{pc}: nearby grades str-or-null", all(n.get("grade") is None or isinstance(n.get("grade"), str) for n in nearby))
    check(f"{pc}: nearby source_urls well-formed", all(str(n.get("source_url", "")).startswith(ENTITY_PREFIX) for n in nearby))
    check(f"{pc}: nearby sorted by distance", [n["distance_m"] for n in nearby] == sorted(n["distance_m"] for n in nearby))
    lb_feats = (lb.get("geojson") or {}).get("features", []) or []
    check(f"{pc}: nearby count == list == geojson features", lb.get("count") == len(nearby) == len(lb_feats))
    check(f"{pc}: nearby geojson are all Points", all((f.get("geometry") or {}).get("type") == "Point" for f in lb_feats))

    # ---- Policy-slice invariants (structure only, no count assertions) ----
    policy_check(pc, data)

    print(f"  INFO   constraints={data.get('count')} {datasets or '[]'}  |  listed: this_site_overlaps={sl.get('overlaps')}, nearby={lb.get('count')}  |  district={site.get('admin_district')}")


def policy_check(pc, data):
    """Cite-or-abstain invariants for the policy brief. Same philosophy as above: assert
    STRUCTURE and CONTRACTS, never data-coverage-dependent counts. The one set-equality check
    (map-consistency) is safe because resolve() is deterministic given the response's own
    constraints - it is derived from the same payload, not from external expectations."""
    pol = data.get("policy")
    check(f"{pc}: policy block present (no error)", isinstance(pol, dict) and "error" not in pol,
          str(pol)[:100] if not isinstance(pol, dict) or "error" in (pol or {}) else "")
    if not isinstance(pol, dict) or "error" in pol:
        return

    scheme_expected = app.POLICY_SCHEME if HAVE_APP else "single-storey-rear-extension"
    check(f"{pc}: policy scheme_type correct", pol.get("scheme_type") == scheme_expected, f"got={pol.get('scheme_type')}")

    plist = pol.get("applicable_policies") or []
    check(f"{pc}: applicable_policies non-empty", bool(plist))

    keys_needed = ("id", "title", "applies_because", "plain_english", "cited_excerpt",
                   "source_url", "source_version", "excerpt_provenance", "grounding_status")
    check(f"{pc}: every policy has required fields", all(all(k in p for k in keys_needed) for p in plist))
    check(f"{pc}: every grounding_status is a known verdict", all(p.get("grounding_status") in VERDICTS for p in plist),
          f"statuses={sorted({p.get('grounding_status') for p in plist})}")
    check(f"{pc}: every cited excerpt is a non-empty string",
          all(isinstance(p.get("cited_excerpt"), str) and p["cited_excerpt"].strip() for p in plist))
    # THE cite-or-abstain contract at the API boundary:
    #   SUPPORTED  -> plain_english is a non-empty string (shown)
    #   otherwise  -> plain_english is None (suppressed; excerpt still shown)
    check(f"{pc}: suppression contract holds (SUPPORTED <-> explanation shown)",
          all((isinstance(p.get("plain_english"), str) and p["plain_english"].strip())
              if p.get("grounding_status") == "SUPPORTED" else p.get("plain_english") is None
              for p in plist))
    check(f"{pc}: every policy cites the Plan source",
          all(str(p.get("source_url", "")).startswith("https://") and p.get("source_version") for p in plist))
    check(f"{pc}: every excerpt provenance is known", all(p.get("excerpt_provenance") in PROVENANCE for p in plist),
          f"provenance={sorted({p.get('excerpt_provenance') for p in plist})}")

    ids = {p.get("id") for p in plist}
    check(f"{pc}: base policies always present", BASE_POLICY_IDS.issubset(ids), f"ids={sorted(ids)}")

    if HAVE_APP and getattr(app, "POLICY_MAP", None):
        expected = {p["id"] for p in pe.resolve(app.POLICY_MAP, pe.active_constraints_from_site(data))["policies"]}
        check(f"{pc}: policy set consistent with this response's constraints", ids == expected,
              f"got={sorted(ids)}, expected={sorted(expected)}")
    else:
        print("  [SKIP] map-consistency check - couldn't import app/policy map (run from the project root)")

    rd = pol.get("required_documents") or {}
    check(f"{pc}: required_documents shape", all(k in rd for k in ("always", "triggered_by_constraints", "design_dependent")))
    check(f"{pc}: 'always' documents non-empty and named",
          bool(rd.get("always")) and all(isinstance(d, dict) and d.get("name") for d in rd.get("always", [])))
    check(f"{pc}: abstentions present", bool(pol.get("abstentions")) and all(isinstance(a, str) and a for a in pol["abstentions"]))
    check(f"{pc}: notes is a list", isinstance(pol.get("notes"), list))
    check(f"{pc}: disclaimer + OGL attribution present", bool(pol.get("disclaimer")) and bool(pol.get("attribution")))


def main():
    print("=== Planning Navigator - smoke test (Week 1 + heritage + policy slice) ===")
    print("(server must be running: uvicorn app:app --port 8000)\n")
    config_check()
    ui_check()
    for pc in POSTCODES:
        site_check(pc)
        time.sleep(1)

    print("\nINFO: counts above are coverage-dependent and are NOT pass/fail - eyeball them.")
    fails = results.count(False)
    print(f"\n=== {'ALL PASS' if fails == 0 else str(fails) + ' FAIL(S)'}  ({results.count(True)}/{len(results)} checks) ===")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
