"""Relabel a client's crops from observed NDVI phenology.

This is a TRIAGE aid for a test client with guessed labels, not a product
feature. It reads the day-of-year at which each parcel peaked and assigns the
crop whose calendar peak window is nearest.

Do not use this on a paying client. Crop type is the client's declaration, not
something to infer and then feed back into rules that assume it is ground truth
— that is circular, and the circularity is invisible in the output. Its only
legitimate use is what it is doing here: showing that guessed labels were wrong.

    python scripts/97_relabel_from_phenology.py --client test --year 2026
    python scripts/97_relabel_from_phenology.py --client test --year 2026 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, load_json  # noqa: E402
from config import CROP_CALENDARS, MIN_COHORT  # noqa: E402

MIN_PEAK_NDVI = 0.35  # below this the parcel never had a canopy to date a peak from


def peak_table(observations: pd.DataFrame, year: int) -> pd.DataFrame:
    frame = observations.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"].dt.year == year].dropna(subset=["ndvi"])
    frame["doy"] = frame["date"].dt.dayofyear

    rows = []
    for field_id, block in frame.groupby("field_id"):
        peak_row = block.loc[block["ndvi"].idxmax()]
        latest = block.sort_values("date").iloc[-1]
        rows.append({
            "field_id": int(field_id),
            "peak_ndvi": round(float(peak_row["ndvi"]), 3),
            "peak_doy": int(peak_row["doy"]),
            "latest_ndvi": round(float(latest["ndvi"]), 3),
            "n_obs": len(block),
        })
    return pd.DataFrame(rows)


def nearest_crop(peak_doy: int, peak_ndvi: float) -> tuple[str, float]:
    """Crop whose peak window centre is nearest. Returns (crop, distance_days)."""
    if peak_ndvi < MIN_PEAK_NDVI:
        return "other", float("inf")
    best, best_distance = "other", float("inf")
    for crop, calendar in CROP_CALENDARS.items():
        if crop == "other":
            continue
        low, high = calendar["peak"]
        if low <= peak_doy <= high:
            distance = 0.0
        else:
            distance = min(abs(peak_doy - low), abs(peak_doy - high))
        if distance < best_distance:
            best, best_distance = crop, distance
    return best, best_distance


def main(client: str, year: int, apply: bool, max_distance: int) -> None:
    directory = CLIENTS_DIR / client
    observations = pd.read_csv(directory / "observations.csv")
    fields = load_json(directory / "fields.geojson")

    declared = {
        int(feature["properties"]["field_id"]): (feature["properties"].get("seasons") or {}).get(str(year))
        for feature in fields["features"]
    }
    peaks = peak_table(observations, year)
    if peaks.empty:
        raise SystemExit(f"No {year} observations with usable NDVI")

    inferred: dict[int, str] = {}
    rows = []
    for row in peaks.itertuples():
        crop, distance = nearest_crop(row.peak_doy, row.peak_ndvi)
        confident = distance <= max_distance
        if not confident:
            crop = "other"
        inferred[row.field_id] = crop
        rows.append({
            "field": row.field_id,
            "peak_doy": row.peak_doy,
            "peak_ndvi": row.peak_ndvi,
            "now": row.latest_ndvi,
            "declared": declared.get(row.field_id),
            "inferred": crop,
            "gap_days": "in window" if distance == 0 else f"{distance:.0f}",
            "changed": "CHANGE" if declared.get(row.field_id) != crop else "",
        })

    table = pd.DataFrame(rows).sort_values("peak_doy")
    print(table.to_string(index=False))

    counts = pd.Series(list(inferred.values())).value_counts().to_dict()
    print(f"\nInferred cohorts: {counts}")
    thin = [crop for crop, count in counts.items() if count < MIN_COHORT]
    if thin:
        print(f"  below MIN_COHORT ({MIN_COHORT}), cohort rules will be suppressed: {', '.join(thin)}")
    changed = int((table["changed"] == "CHANGE").sum())
    print(f"Would change {changed}/{len(table)} labels")

    if not apply:
        print("\nDry run. Re-run with --apply to write these labels into fields.geojson.")
        return

    for feature in fields["features"]:
        field_id = int(feature["properties"]["field_id"])
        if field_id in inferred:
            feature["properties"].setdefault("seasons", {})[str(year)] = inferred[field_id]
            feature["properties"]["crop_source"] = "inferred_from_phenology"
    with open(directory / "fields.geojson", "w", encoding="utf-8") as handle:
        json.dump(fields, handle)
    print(f"\n  -> {directory / 'fields.geojson'} updated")
    print("     Labels marked crop_source=inferred_from_phenology. A real client declares these.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-distance", type=int, default=30, help="days from a peak window")
    args = parser.parse_args()
    main(args.client, args.year, args.apply, args.max_distance)
