"""Convert a geojson.io export into a valid client fields.geojson.

geojson.io happily exports Points from stray clicks and LineStrings from the
line tool. Both are useless here, and a converter that silently drops them
leaves you staring at an empty run wondering why. This one reports every
feature it saw, what it did with it, and refuses to write a file it does not
trust.

    python scripts/98_convert_drawn_fields.py --client test
    python scripts/98_convert_drawn_fields.py --client test --crop cotton --year 2026
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, iter_positions, polygon_area_ha  # noqa: E402
from config import CROP_CALENDARS  # noqa: E402

CLOSE_TOLERANCE_DEG = 0.0005  # ~50 m; a ring this close was meant to be closed


def close_ring(ring: list) -> list | None:
    """Close a nearly-closed ring. Return None if it is not a ring at all."""
    if len(ring) < 3:
        return None
    first, last = ring[0], ring[-1]
    if first == last:
        return ring if len(ring) >= 4 else None
    gap = max(abs(first[0] - last[0]), abs(first[1] - last[1]))
    if gap > CLOSE_TOLERANCE_DEG:
        return None
    return [*ring, first]


def to_polygon(feature: dict) -> tuple[dict | None, str]:
    geometry = feature.get("geometry") or {}
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if kind in ("Polygon", "MultiPolygon"):
        return geometry, "already a polygon"
    if kind == "LineString":
        ring = close_ring(coordinates)
        if ring is None:
            return None, "LineString is not a closed ring — redraw with the polygon tool"
        return {"type": "Polygon", "coordinates": [ring]}, "closed LineString -> Polygon"
    if kind == "Point":
        return None, "Point (stray click)"
    return None, f"unsupported geometry type {kind!r}"


def plausible(geometry: dict) -> str | None:
    """Catch swapped lat/lon before it becomes a job in the wrong hemisphere."""
    for lon, lat in iter_positions(geometry["coordinates"]):
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return f"coordinate out of range: {lon}, {lat}"
        # Uzbekistan sanity box. Widen if you work elsewhere.
        if not (55 <= lon <= 74 and 37 <= lat <= 46):
            return f"({lon:.4f}, {lat:.4f}) is outside Uzbekistan — lat/lon swapped?"
    return None


def main(client: str, source: str | None, crop: str, year: int, min_area: float) -> Path:
    directory = CLIENTS_DIR / client
    source_path = Path(source) if source else directory / "raw_fields.geojson"
    if not source_path.exists():
        raise SystemExit(f"No such file: {source_path}\nPaste your geojson.io export there first.")
    if crop not in CROP_CALENDARS:
        raise SystemExit(f"Unknown crop {crop!r}. Known: {', '.join(sorted(CROP_CALENDARS))}")

    collection = json.load(open(source_path, encoding="utf-8"))
    features = collection.get("features", [])
    if not features:
        raise SystemExit(f"{source_path} has no features")

    kept: list[dict] = []
    skipped: list[str] = []
    print(f"Read {len(features)} feature(s) from {source_path}\n")

    for index, feature in enumerate(features):
        geometry, note = to_polygon(feature)
        if geometry is None:
            skipped.append(f"feature[{index}]: {note}")
            print(f"  feature[{index}]  SKIP  {note}")
            continue
        problem = plausible(geometry)
        if problem:
            skipped.append(f"feature[{index}]: {problem}")
            print(f"  feature[{index}]  SKIP  {problem}")
            continue

        area = polygon_area_ha(geometry)
        field_id = len(kept) + 1
        if area < min_area:
            skipped.append(f"feature[{index}]: {area:.2f} ha is below --min-area {min_area}")
            print(f"  feature[{index}]  SKIP  {area:.2f} ha — too small for 20 m pixels")
            continue

        kept.append({
            "type": "Feature",
            "properties": {
                "field_id": field_id,
                "name": f"F-{field_id:02d}",
                "area_ha": round(area, 2),
                "seasons": {str(year): crop},
            },
            "geometry": geometry,
        })
        print(f"  feature[{index}]  KEEP  F-{field_id:02d}  {area:7.1f} ha  ({note})")

    if not kept:
        raise SystemExit(
            "\nNo usable polygons. Draw with the POLYGON tool (the pentagon icon), "
            "not the line tool, and close each shape by clicking the first vertex again."
        )

    output = directory / "fields.geojson"
    with open(output, "w", encoding="utf-8") as handle:
        json.dump({"type": "FeatureCollection", "features": kept}, handle)

    total = sum(feature["properties"]["area_ha"] for feature in kept)
    print(f"\n  -> {output}")
    print(f"     {len(kept)} parcels, {total:,.0f} ha, all declared {crop} for {year}")
    if skipped:
        print(f"     {len(skipped)} feature(s) skipped")

    from config import MIN_COHORT

    if len(kept) < MIN_COHORT:
        print(
            f"\nNOTE: {len(kept)} parcels is below MIN_COHORT ({MIN_COHORT}). Cohort rules "
            "will be suppressed and only own-history rules will run. Draw more parcels "
            "for a meaningful test."
        )
    print(
        f"\nEvery parcel is declared '{crop}'. That is a guess unless you know the crop. "
        "The detector will trust it, and mislabelled parcels will produce confident nonsense."
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--source", default=None, help="default: clients/<client>/raw_fields.geojson")
    parser.add_argument("--crop", default="cotton", choices=sorted(CROP_CALENDARS))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--min-area", type=float, default=0.5, help="hectares")
    args = parser.parse_args()
    main(args.client, args.source, args.crop, args.year, args.min_area)
