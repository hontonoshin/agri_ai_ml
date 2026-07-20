"""Create a synthetic client with known planted anomalies — no openEO, no quota.

Use this to test 03_anomalies.py, 04_report.py and app.py end to end, and as a
regression fixture: the planted anomalies are what the detector should find.

    python scripts/99_make_demo_client.py --client demo
    python scripts/03_anomalies.py --client demo
    python scripts/04_report.py --client demo --language en
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, ensure_dir, write_json_atomic  # noqa: E402
from config import CROP_CALENDARS  # noqa: E402

# field_id -> planted condition. The detector should find exactly these.
PLANTED = {
    1: "low_vigour",       # chronically weak all season
    2: "sudden_drop",      # collapses mid-season
    3: "late_emergence",   # never emerges
    4: "dry_canopy",       # normal NDVI, low NDMI
}


def square(lon: float, lat: float, size: float = 0.012) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon, lat], [lon + size, lat], [lon + size, lat + size],
            [lon, lat + size], [lon, lat],
        ]],
    }


def make_fields(n_fields: int, year: int) -> dict:
    features = []
    for index in range(n_fields):
        field_id = index + 1
        crop = "cotton" if field_id <= 24 else "wheat"
        lon = 60.60 + (index % 6) * 0.015
        lat = 41.55 + (index // 6) * 0.015
        features.append({
            "type": "Feature",
            "properties": {
                "field_id": field_id,
                "name": f"{'Paxta' if crop == 'cotton' else 'Bug''doy'}-{field_id:02d}",
                "seasons": {str(year): crop, str(year - 1): crop},
            },
            "geometry": square(lon, lat),
        })
    return {"type": "FeatureCollection", "features": features}


def curve(doy: float, peak: float, calendar: dict) -> float:
    centre = (calendar["peak"][0] + calendar["peak"][1]) / 2
    width = (calendar["senescence"][1] - calendar["emergence"][0]) / 4.5
    return 0.12 + (peak - 0.12) * float(np.exp(-(((doy - centre) / width) ** 2)))


def make_observations(fields: dict, year: int, as_of: dt.date, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for feature in fields["features"]:
        properties = feature["properties"]
        field_id = properties["field_id"]
        planted = PLANTED.get(field_id)
        for season in (year - 1, year):
            crop = properties["seasons"][str(season)]
            calendar = CROP_CALENDARS[crop]
            base_peak = float(rng.uniform(0.60, 0.75))
            for doy in range(40, 340, 5):
                date = dt.date(season, 1, 1) + dt.timedelta(days=doy - 1)
                if date > as_of:
                    continue
                if rng.random() < 0.35:      # cloud
                    continue

                peak = base_peak
                # planted conditions apply to the current season only
                if season == year and planted == "low_vigour":
                    peak = base_peak * 0.45
                if season == year and planted == "late_emergence":
                    peak = 0.14

                ndvi = curve(doy, peak, calendar) + float(rng.normal(0, 0.02))
                if season == year and planted == "sudden_drop" and doy >= 175:
                    ndvi *= 0.42

                ndre = ndvi * 0.62 + float(rng.normal(0, 0.015))
                ndmi = ndvi * 0.55 + float(rng.normal(0, 0.015))
                evi = ndvi * 0.92 + float(rng.normal(0, 0.018))
                savi = ndvi * 0.82 + float(rng.normal(0, 0.016))
                if season == year and planted == "dry_canopy" and doy >= 170:
                    ndmi *= 0.35

                rows.append({
                    "field_id": field_id,
                    "date": date.isoformat(),
                    "ndvi": round(float(np.clip(ndvi, -1, 1)), 4),
                    "ndre": round(float(np.clip(ndre, -1, 1)), 4),
                    "ndmi": round(float(np.clip(ndmi, -1, 1)), 4),
                    "evi": round(float(np.clip(evi, -1.5, 1.5)), 4),
                    "savi": round(float(np.clip(savi, -1.5, 1.5)), 4),
                    "valid_frac": round(float(rng.uniform(0.6, 1.0)), 4),
                })
    return pd.DataFrame(rows).sort_values(["field_id", "date"])


def main(client: str, n_fields: int, as_of: str | None, seed: int) -> Path:
    as_of_date = dt.date.fromisoformat(as_of) if as_of else dt.date(2026, 7, 15)
    year = as_of_date.year
    directory = ensure_dir(CLIENTS_DIR / client)

    write_json_atomic(directory / "client.json", {
        "label": "Demo Cluster (synthetic)",
        "contact": "demo@example.com",
        "language": "uz",
        "monitoring_start": f"{year - 1}-01-01",
        "note": "Synthetic data for testing. Not real imagery.",
    })
    fields = make_fields(n_fields, year)
    write_json_atomic(directory / "fields.geojson", fields)
    observations = make_observations(fields, year, as_of_date, seed)
    observations.to_csv(directory / "observations.csv", index=False)

    print(f"Client:  {directory}")
    print(f"Fields:  {n_fields} ({sum(1 for f in fields['features'] if f['properties']['seasons'][str(year)] == 'cotton')} cotton)")
    print(f"Records: {len(observations):,} through {as_of_date}")
    print(f"Planted: {PLANTED}")
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default="demo")
    parser.add_argument("--n-fields", type=int, default=36)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    main(args.client, args.n_fields, args.as_of, args.seed)
