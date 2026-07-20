"""Import an existing UZ-Agri-Copernicus region into the client layout.

Expected source files:
  field_timeseries.csv       date, field_id, ndvi (other indices optional)
  sampled_fields.geojson     matching field_id polygons
  meta.json                  optional region metadata

No spectral values are invented. Missing NDRE, NDMI and valid_frac are written
as empty columns so downstream code can state their absence explicitly.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, load_json, write_json_atomic  # noqa: E402
from config import CROP_CALENDARS  # noqa: E402


def main(source_dir: str, client: str, label: str | None,
         crop: str, force: bool) -> Path:
    source = Path(source_dir).expanduser().resolve()
    timeseries_path = source / "field_timeseries.csv"
    geometry_path = source / "sampled_fields.geojson"
    if not timeseries_path.exists():
        raise SystemExit(f"Missing {timeseries_path}")
    if not geometry_path.exists():
        raise SystemExit(f"Missing {geometry_path}")
    if crop not in CROP_CALENDARS:
        raise SystemExit(f"Unknown crop {crop!r}; choose from {sorted(CROP_CALENDARS)}")

    observations = pd.read_csv(timeseries_path)
    required = {"date", "field_id", "ndvi"}
    missing = required - set(observations.columns)
    if missing:
        raise SystemExit(f"Time-series file lacks columns: {sorted(missing)}")
    observations["date"] = pd.to_datetime(observations["date"], errors="coerce")
    observations["field_id"] = pd.to_numeric(observations["field_id"], errors="coerce")
    observations["ndvi"] = pd.to_numeric(observations["ndvi"], errors="coerce")
    observations = observations.dropna(subset=["date", "field_id", "ndvi"]).copy()
    observations["field_id"] = observations["field_id"].astype(int)
    observations["date"] = observations["date"].dt.date.astype(str)
    observations = observations.sort_values(["field_id", "date"])
    observations = observations.drop_duplicates(["field_id", "date"], keep="last")
    for optional in ("ndre", "ndmi", "evi", "savi", "valid_frac"):
        if optional not in observations:
            observations[optional] = float("nan")

    fields = load_json(geometry_path)
    features = fields.get("features", [])
    csv_ids = set(observations["field_id"].unique())
    geo_ids = {
        int(feature.get("properties", {}).get("field_id"))
        for feature in features
        if feature.get("properties", {}).get("field_id") is not None
    }
    if csv_ids != geo_ids:
        raise SystemExit(
            f"Field-ID mismatch: CSV={len(csv_ids)}, GeoJSON={len(geo_ids)}, "
            f"overlap={len(csv_ids & geo_ids)}"
        )

    years = sorted(pd.to_datetime(observations["date"]).dt.year.unique())
    for feature in features:
        properties = feature.setdefault("properties", {})
        field_id = int(properties["field_id"])
        properties.setdefault("name", f"Khorezm field {field_id}")
        properties["seasons"] = {str(year): crop for year in years}
        properties["crop_source"] = (
            "unspecified_default" if crop == "other" else "import_argument"
        )

    target = CLIENTS_DIR / client
    if target.exists() and any(target.iterdir()) and not force:
        raise SystemExit(
            f"{target} already contains files; pass --force to replace imported inputs"
        )
    target.mkdir(parents=True, exist_ok=True)
    observations[["date", "field_id", "ndvi", "ndre", "ndmi", "evi", "savi", "valid_frac"]].to_csv(
        target / "observations.csv", index=False
    )
    write_json_atomic(target / "fields.geojson", fields)

    meta = load_json(source / "meta.json", default={})
    client_meta = {
        "label": label or meta.get("label") or client.title(),
        "language": "en",
        "source_region": meta.get("region", client),
        "source_run_id": meta.get("run_id"),
        "source_directory": str(source),
        "imported_rows": int(len(observations)),
        "imported_fields": int(len(csv_ids)),
        "available_indices": [
            name for name in ("ndvi", "ndre", "ndmi", "evi", "savi")
            if observations[name].notna().any()
        ],
        "declared_crop": crop,
        "crop_warning": (
            "Crop labels were unavailable in the source. All seasons use 'other'; "
            "replace them with verified labels before making crop-specific claims."
            if crop == "other" else
            "Crop was supplied during import and was not inferred from Sentinel data."
        ),
    }
    write_json_atomic(target / "client.json", client_meta)
    print(f"Imported {len(observations):,} observations and {len(csv_ids)} fields")
    print(f"Available indices: {', '.join(client_meta['available_indices'])}")
    print(f"Client directory: {target}")
    if crop == "other":
        print("NOTE: crop is unspecified; generic seasonal/cohort monitoring will be used.")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--label", default=None)
    parser.add_argument("--crop", default="other", choices=sorted(CROP_CALENDARS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    main(args.source_dir, args.client, args.label, args.crop, args.force)
