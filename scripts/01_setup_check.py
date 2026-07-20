"""Check openEO access and validate a client's field definitions.

Run this before onboarding a client and whenever the pipeline breaks. It fails
loudly on the things that silently corrupt everything downstream: duplicate
field ids, unknown crop names, invalid geometry, missing seasons.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import BACKEND, CLIENTS_DIR, connect, load_json, polygon_area_ha  # noqa: E402
from config import CROP_CALENDARS  # noqa: E402


def check_backend(headless: bool | None) -> None:
    connection = connect(headless=headless)
    collections = set(connection.list_collection_ids())
    print(f"Backend: {BACKEND}")
    print(f"openEO API: {connection.capabilities().api_version()}")
    ok = "SENTINEL2_L2A" in collections
    print(f"Sentinel-2 L2A: {'OK' if ok else 'MISSING'}")
    if not ok:
        raise SystemExit("SENTINEL2_L2A is unavailable on this backend")


def check_client(client: str) -> None:
    directory = CLIENTS_DIR / client
    if not directory.exists():
        raise SystemExit(f"No such client directory: {directory}")

    meta = load_json(directory / "client.json")
    fields = load_json(directory / "fields.geojson")
    features = fields.get("features", [])
    if not features:
        raise SystemExit("fields.geojson contains no features")

    print(f"\nClient: {meta.get('label', client)}")
    print(f"Contact: {meta.get('contact', '—')}")
    print(f"Language: {meta.get('language', 'uz')}")
    print(f"Fields: {len(features)}")

    problems: list[str] = []
    seen: set[int] = set()
    total_area = 0.0
    crops: dict[str, int] = {}

    for index, feature in enumerate(features):
        properties = feature.get("properties", {})
        label = f"feature[{index}]"

        raw_id = properties.get("field_id")
        if raw_id is None:
            problems.append(f"{label}: missing field_id")
            continue
        try:
            field_id = int(raw_id)
        except (TypeError, ValueError):
            problems.append(f"{label}: field_id {raw_id!r} is not an integer")
            continue
        if field_id in seen:
            problems.append(f"field_id {field_id}: duplicated")
        seen.add(field_id)

        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            problems.append(f"field {field_id}: geometry must be Polygon/MultiPolygon")
            continue

        area = polygon_area_ha(geometry)
        total_area += area
        if area <= 0:
            problems.append(f"field {field_id}: zero/invalid area")
        elif area < 0.5:
            problems.append(f"field {field_id}: {area:.2f} ha is below reliable S2 size (~0.5 ha)")

        seasons = properties.get("seasons") or {}
        if not seasons:
            problems.append(f"field {field_id}: no seasons declared (need {{'2026': 'cotton'}})")
        for year, crop in seasons.items():
            if crop not in CROP_CALENDARS:
                problems.append(
                    f"field {field_id}, {year}: unknown crop {crop!r} "
                    f"(known: {', '.join(sorted(CROP_CALENDARS))})"
                )
            crops[crop] = crops.get(crop, 0) + 1

    print(f"Total area: {total_area:,.1f} ha")
    print("Crop-seasons declared:")
    for crop, count in sorted(crops.items(), key=lambda item: -item[1]):
        print(f"  {crop:10s} {count}")

    from config import MIN_COHORT

    thin = {crop: count for crop, count in crops.items() if count < MIN_COHORT}
    if thin:
        print(
            f"\nNOTE: cohort rules need >= {MIN_COHORT} parcels of a crop in a season. "
            f"These will fall back to own-history rules only: {', '.join(thin)}"
        )

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("\nClient definition OK.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default=None, help="validate this client's field file")
    parser.add_argument("--no-backend", action="store_true", help="skip the openEO check")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    if not args.no_backend:
        check_backend(True if args.headless else None)
    if args.client:
        check_client(args.client)
