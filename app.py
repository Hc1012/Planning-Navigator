"""
Planning Navigator - Week 1 + heritage slice (backend).

  postcode -> lat/lng (postcodes.io) -> planning.data.gov.uk -> GeoJSON + list.

Two planning requests:
  A) lat/long "within" query  -> area constraints (polygons) + listed-building OUTLINES
  B) bbox "intersects" query  -> nearby listed-building POINTS (distance + bearing + grade)

No policy RAG, no PDF export yet. Listed-building info is surfaced as CONTEXT, not advice.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload
    open http://localhost:8000
"""
import math
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

import policy_engine as pe

BASE = Path(__file__).parent
app = FastAPI(title="Planning Navigator")

POSTCODES_API = "https://api.postcodes.io/postcodes/"
PLANNING_API = "https://www.planning.data.gov.uk/entity.geojson"

# Policy layer (single-storey rear extension). The curated gold-label map decides which policies
# apply; the LLM only explains and is grounding-checked. Deterministic fallback by default (no key);
# set PLANNING_USE_ANTHROPIC=1 + ANTHROPIC_API_KEY for the live model. Map integrity is checked on
# load - if it fails we log and disable the policy brief rather than break the constraints response.
POLICY_SCHEME = "single-storey-rear-extension"
try:
    POLICY_MAP = pe.load_map(BASE / f"{POLICY_SCHEME.replace('-', '_')}.yaml")
    POLICY_LLM = pe.default_llm()
except Exception as exc:  # pragma: no cover
    print(f"[app] policy map unavailable ({exc}); /api/site will omit the policy brief")
    POLICY_MAP, POLICY_LLM = None, None

# Area (polygon) constraints answered by the lat/long point-in-polygon query.
# (listed-building POINTS are handled separately by the bbox query - see below.)
CONSTRAINT_DATASETS = [
    "conservation-area",
    "article-4-direction-area",
    "tree-preservation-zone",
    "flood-risk-zone",
    "scheduled-monument",
]

# Listed-building footprints. Polygons, so they DO match the lat/long "within" query:
# if the site point is inside an outline, the building is (indicatively) listed.
# NB: outline coverage is PARTIAL - absence of an outline does NOT mean "not listed".
OUTLINE_DATASET = "listed-building-outline"

# Listed-building POINTS (Historic England NHLE). Queried via a bbox "intersects" query.
LISTED_POINT_DATASET = "listed-building"

# Default search radius (metres) for nearby listed buildings.
LISTED_RADIUS_M = 100

IMPLICATIONS = {
    "conservation-area": "In a conservation area: design, materials and rooflines are assessed closely; a design & access statement is usually required.",
    "article-4-direction-area": "An Article 4 direction may remove permitted-development rights here, so a full application can be required for works that would otherwise be permitted.",
    "tree-preservation-zone": "Protected trees may be present: an arboricultural report can be required and works to trees may need separate consent.",
    "flood-risk-zone": "In a mapped flood risk zone: a flood risk assessment may be required, depending on the zone.",
    "scheduled-monument": "At or near a scheduled monument: separate scheduled monument consent may be required.",
}

WARNING = (
    "This shows constraints found in open data. An absence of results here does "
    "NOT mean a site is unconstrained - coverage varies by area. Always confirm "
    "with the London Borough of Southwark."
)

_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return _COMPASS[round(deg / 45) % 8]


def _bbox_wkt(lat, lng, radius_m):
    """A square bbox (WKT POLYGON, lng-lat order, WGS84) ~radius_m around the point."""
    dlat = radius_m / 111320.0
    dlng = radius_m / (111320.0 * max(0.01, math.cos(math.radians(lat))))
    w, e, s, n = lng - dlng, lng + dlng, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _grade(p):
    return p.get("listed_building_grade") or p.get("listed-building-grade")


def _src(entity):
    return f"https://www.planning.data.gov.uk/entity/{entity}" if entity else None


def summarise(features: list[dict]) -> list[dict]:
    """Flatten constraint features into a display-ready list (outlines handled elsewhere)."""
    items: list[dict] = []
    for feature in features or []:
        p = feature.get("properties", {}) or {}
        dataset = p.get("dataset", "") or ""
        entity = p.get("entity")
        items.append({
            "name": p.get("name") or p.get("reference") or "(unnamed feature)",
            "dataset": dataset,
            "dataset_label": dataset.replace("-", " ").title(),
            "reference": p.get("reference"),
            "entity": entity,
            "grade": _grade(p),
            "implication": IMPLICATIONS.get(dataset, ""),
            "source_url": _src(entity),
        })
    items.sort(key=lambda x: (x["dataset"], str(x["name"])))
    return items


def nearby_listed(listed_fc: dict, lat: float, lng: float, radius_m: int):
    """Distance/bearing-filtered nearby listed buildings, plus a map FeatureCollection."""
    rows, kept_features = [], []
    for f in (listed_fc.get("features") or []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue
        plng, plat = coords[0], coords[1]
        dist = _haversine_m(lat, lng, plat, plng)
        if dist > radius_m:
            continue
        p = f.get("properties") or {}
        entity = p.get("entity")
        rows.append({
            "name": p.get("name") or p.get("reference") or "(listed building)",
            "grade": _grade(p),
            "distance_m": round(dist),
            "bearing": _bearing(lat, lng, plat, plng),
            "entity": entity,
            "source_url": _src(entity),
        })
        kept_features.append(f)
    rows.sort(key=lambda x: x["distance_m"])
    return rows, {"type": "FeatureCollection", "features": kept_features}


@app.get("/api/site")
async def site(postcode: str = Query(..., min_length=4, description="UK postcode, e.g. SE21 7BG")):
    pc = postcode.strip()
    pc_lookup = pc.replace(" ", "")

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "planning-navigator"}) as client:
        # 1) Geocode
        try:
            geo = await client.get(POSTCODES_API + pc_lookup)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach the geocoder: {exc}")
        if geo.status_code != 200:
            raise HTTPException(status_code=404, detail=f"'{pc}' is not a valid UK postcode. Try e.g. SE21 7BG.")
        result = geo.json().get("result") or {}
        lat, lng = result.get("latitude"), result.get("longitude")
        district = result.get("admin_district")
        if lat is None or lng is None:
            raise HTTPException(status_code=404, detail=f"No coordinates found for '{pc}'.")

        # 2) Request A: area constraints + listed-building outlines (point-in-polygon "within")
        a_params = [("latitude", lat), ("longitude", lng), ("limit", 100)]
        a_params += [("dataset", d) for d in CONSTRAINT_DATASETS]
        a_params += [("dataset", OUTLINE_DATASET)]
        try:
            pa = await client.get(PLANNING_API, params=a_params)
            pa.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach the planning data API: {exc}")
        geojson = pa.json()

        # 3) Request B: nearby listed-building points (bbox "intersects"); non-fatal if it fails
        listed_available = True
        try:
            b_params = [
                ("dataset", LISTED_POINT_DATASET),
                ("geometry", _bbox_wkt(lat, lng, LISTED_RADIUS_M)),
                ("geometry_relation", "intersects"),
                ("limit", 100),
            ]
            pb = await client.get(PLANNING_API, params=b_params)
            pb.raise_for_status()
            listed_fc = pb.json()
        except httpx.HTTPError:
            listed_available = False
            listed_fc = {"type": "FeatureCollection", "features": []}

    # Split request A: outlines (listings) vs the rest (constraints)
    feats_a = geojson.get("features", []) or []
    outline_feats = [f for f in feats_a if (f.get("properties") or {}).get("dataset") == OUTLINE_DATASET]
    constraint_feats = [f for f in feats_a if (f.get("properties") or {}).get("dataset") != OUTLINE_DATASET]
    constraints = summarise(constraint_feats)

    site_matches = []
    for f in outline_feats:
        p = f.get("properties") or {}
        entity = p.get("entity")
        site_matches.append({
            "name": p.get("name") or p.get("reference") or "(listed building)",
            "grade": _grade(p),
            "entity": entity,
            "source_url": _src(entity),
        })

    nearby, nearby_fc = nearby_listed(listed_fc, lat, lng, LISTED_RADIUS_M)

    warnings = [WARNING]
    if district and district != "Southwark":
        warnings.append(
            f"Heads up: this postcode is in {district}, not Southwark. Constraints still show, "
            f"but the (later) policy layer is Southwark-only."
        )

    payload = {
        "site": {
            "postcode": result.get("postcode", pc),
            "latitude": lat,
            "longitude": lng,
            "admin_district": district,
            "in_southwark": district == "Southwark",
        },
        "count": len(constraints),
        "constraints": constraints,
        "geojson": geojson,  # request A (constraints + outlines) for the map
        "listed_buildings": {
            "radius_m": LISTED_RADIUS_M,
            "available": listed_available,
            "site_listing": {"overlaps": bool(site_matches), "matches": site_matches},
            "nearby": nearby,
            "count": len(nearby),
            "geojson": nearby_fc,  # within-radius points for the map
        },
        "warnings": warnings,
    }

    # Policy brief for the single-storey rear extension scheme, driven by the site's constraints.
    if POLICY_MAP is not None:
        try:
            active = pe.active_constraints_from_site(payload)
            payload["policy"] = pe.build_policy_response(POLICY_MAP, active, POLICY_LLM)
        except Exception as exc:  # never let the policy layer break the constraints response
            payload["policy"] = {"error": f"policy brief unavailable: {exc}"}

    return payload


@app.get("/")
async def index():
    return FileResponse(BASE / "index.html")
