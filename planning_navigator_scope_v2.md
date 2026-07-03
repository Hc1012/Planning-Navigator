# Planning Navigator — Scope v2: Listed-building heritage slice (Week 1.5)

## Why this, now
Week 1 ships area/polygon constraints. Listed buildings were deliberately excluded
because they are **point** geometry and a point-in-polygon query never matches them.
But for a homeowner, *"is my building listed?"* and *"what's listed next door?"* are
among the most consequential heritage questions. Leaving them as "not checked" is too
big a gap to carry into the policy layer. So this is a focused heritage slice **before**
policy RAG — an early Week 2 / Week 1.5 item.

## What it adds (two parts)
1. **Nearby listed buildings.** Query listed-building *points* in a bounding box around
   the site, compute distance + bearing, filter to a radius, sort nearest-first, attach grade.
2. **"Is this building itself listed?"** Query `listed-building-outline` *polygons* with the
   existing point-in-polygon (`within`) query; if the site point falls inside an outline,
   flag it — **indicatively** (partial coverage).

## API mechanism (confirmed against planning.data.gov.uk/docs)
- **Nearby points:** `GET /entity.geojson?dataset=listed-building&geometry=<bbox WKT>&geometry_relation=intersects&limit=100`.
  The `geometry` parameter takes a WKT polygon in WGS84 (lng lat order); `intersects`
  returns everything inside the polygon. This is the mechanism the lat/long `within`
  query could not provide for points.
- **Outline:** add `listed-building-outline` to the existing lat/long `within` request —
  no extra call. It is polygon data, so `within` matches when the site point is inside.
- **Two requests total:** A = existing within-query (now also returns outlines),
  B = new bbox points query.

## Decisions (locked)
- Radius default: **100 m** (immediate street context; distance shown so the user judges;
  there is no statutory fixed distance for "setting", so this is a sensible tool default).
- Include the outline "is it listed" check in this slice: **yes** (rides the existing query).
- Radius circle on the map: **yes**.

## Wording guardrails (non-negotiable)
- **Outline overlap →** "A listed-building outline appears to overlap this point —
  indicative only; confirm with Historic England / Southwark." Never "this building is listed."
- **Nearby distances →** approximate: "≈42 m NE". Never exact/survey-style.
- **No overlap →** "No listed-building outline overlaps this point. Coverage is partial,
  so this does not confirm the building is unlisted." Never "not listed."
- Everything is surfaced **context, not advice**: whether works need consent, or affect a
  listed building's setting, is for the council to decide. Say so.

## Response contract (additions)
```
listed_buildings: {
  radius_m: 100,
  available: bool,                       # false if the bbox call failed (degrades gracefully)
  site_listing: {
    overlaps: bool,
    matches: [ { name, grade, entity, source_url } ]   # outline(s) the point falls inside
  },
  nearby: [ { name, grade, distance_m, bearing, entity, source_url } ],  # sorted, <= radius
  count: int,
  geojson: FeatureCollection             # the within-radius points, for the map
}
```
- `constraints` **excludes** outlines (they are handled as listings, not constraints).
- top-level `geojson` (request A) **includes** outlines so the footprint can draw on the map.

## UI
- Replace the Week-1 "not checked yet" note with a **"Listed buildings (within ≈100 m)"**
  section: the "this building" line first, then the nearby list with grade badges,
  "≈X m {bearing}" distances, and source links.
- Map: listed-building **points** as markers, listed **outlines** as polygons, and a faint
  **100 m radius circle** around the site.

## Honesty / abstention
- Outline absence ≠ unlisted (partial coverage). Nearby-empty ≠ none (coverage varies).
- NHLE points are listing **grid references**, which can sit slightly off the building, so
  distances are **approximate** ("≈"). All stated in the UI.

## Out of scope (deliberately)
- Any consent/permission interpretation or setting-harm judgement — that is the Southwark
  SPD / policy-RAG layer, which is next, not now.
- Pagination of very dense bbox results (limit=100 for now), variable-radius slider, full
  setting/sightline analysis.

## Test plan (non-brittle, same discipline as Week 1)
Assert **structure**, not counts:
- `listed_buildings` shape present; `radius_m == 100`; `available` true.
- every `nearby` item: `0 <= distance_m <= 100`, `bearing` in the 8-point set, well-formed
  `source_url`, `grade` is a string or null.
- `count == len(nearby) == len(listed_buildings.geojson.features)`; all those features are Points.
- `site_listing.overlaps` is bool; if true, `matches` is non-empty.
- guardrail wording present in the shipped frontend.
- **Do NOT** assert specific listed counts for the three postcodes (coverage/centroid-dependent).

## Effort
~1 evening. Backend ~50 lines (bbox + haversine + bearing + request B + split).
Frontend ~40 lines (section + markers + circle). Smoke test extended.
