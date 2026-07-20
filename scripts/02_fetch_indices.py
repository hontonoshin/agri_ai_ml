"""Fetch per-field NDVI/NDRE/NDMI/EVI/SAVI via openEO aggregate_spatial.

Every field is measured — there is no sampling. Zonal statistics are computed on
the Copernicus backend; only a small table is downloaded.

Incremental by design: each weekly run fetches only dates after the last stored
observation, so the steady-state cost is one small job per chunk per week.

Outputs (under clients/<client>/):
    observations.csv   field_id, date, ndvi, ndre, ndmi, evi, savi, valid_frac
    fetch_log.json     provenance for every job ever run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import (  # noqa: E402
    CLIENTS_DIR,
    connect,
    ensure_dir,
    geometry_bbox,
    iter_positions,
    latest_available_date,
    load_json,
    parse_date,
    write_json_atomic,
)
from config import (  # noqa: E402
    BANDS,
    GOOD_SCL,
    INDICES,
    MAX_CLOUD_COVER,
    MIN_VALID_FRAC,
    RESAMPLE_RESOLUTION,
    REFLECTANCE_SCALE,
)

OBSERVATION_COLUMNS = ["field_id", "date", *INDICES, "valid_frac"]
FETCH_SCHEMA_VERSION = "v2_five_indices"


# --------------------------------------------------------------------------- #
# openEO graph
# --------------------------------------------------------------------------- #
def build_cube(connection, bbox: dict, start: str, end: str):
    """One cube carrying every index plus the clear-pixel fraction.

    Band order is fixed by INDICES, then 'valid'. parse_result depends on it.
    """
    cube = connection.load_collection(
        "SENTINEL2_L2A",
        spatial_extent=bbox,
        temporal_extent=[start, end],
        bands=BANDS,
        max_cloud_cover=MAX_CLOUD_COVER,
    )
    # B05/B11 are native 20 m; put every band on one grid before band maths.
    cube = cube.resample_spatial(resolution=RESAMPLE_RESOLUTION)

    scl = cube.band("SCL")
    good = (scl == GOOD_SCL[0]) | (scl == GOOD_SCL[1])

    merged = None
    for name, spec in INDICES.items():
        bands = spec["bands"]
        nir = cube.band(bands[0])
        red_or_other = cube.band(bands[1])
        if spec["formula"] == "normalized_difference":
            index = (nir - red_or_other) / (nir + red_or_other)
        elif spec["formula"] == "evi":
            blue = cube.band(bands[2])
            index = 2.5 * (nir - red_or_other) / (
                nir + 6.0 * red_or_other - 7.5 * blue + REFLECTANCE_SCALE
            )
        elif spec["formula"] == "savi":
            index = 1.5 * (nir - red_or_other) / (
                nir + red_or_other + 0.5 * REFLECTANCE_SCALE
            )
        else:
            raise ValueError(f"Unsupported index formula: {spec['formula']}")
        index = index.mask(~good).add_dimension(name="bands", label=name, type="bands")
        merged = index if merged is None else merged.merge_cubes(index)

    valid = good.add_dimension(name="bands", label="valid", type="bands")
    return merged.merge_cubes(valid)


def parse_result(payload, field_ids: list[int]) -> list[dict]:
    """Parse aggregate_spatial JSON: {timestamp: [[b0, b1, ...], ...per geometry]}."""
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected payload type: {type(payload).__name__}")

    names = [*INDICES, "valid"]
    rows: list[dict] = []
    for timestamp, per_geometry in payload.items():
        try:
            date = pd.Timestamp(timestamp).date().isoformat()
        except Exception:
            continue
        if not isinstance(per_geometry, list):
            continue
        if len(per_geometry) != len(field_ids):
            raise ValueError(
                f"Geometry count mismatch at {timestamp}: backend returned "
                f"{len(per_geometry)} entries for {len(field_ids)} fields. "
                "Refusing to guess the field_id alignment."
            )
        for field_id, values in zip(field_ids, per_geometry):
            if values is None:
                continue
            if not isinstance(values, list):
                values = [values]
            record: dict = {"field_id": int(field_id), "date": date}
            usable = False
            for position, name in enumerate(names):
                value = values[position] if position < len(values) else None
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    record[name] = None
                    continue
                low, high = INDICES[name]["range"] if name in INDICES else (0.0, 1.0)
                if not (low <= value <= high):
                    record[name] = None
                    continue
                record[name] = round(value, 4)
                if name in INDICES:
                    usable = True
            if not usable:
                continue  # fully cloud-masked over this parcel on this date
            record["valid_frac"] = record.pop("valid", None)
            rows.append(record)
    return rows


def run_job(
    connection,
    chunk: list[dict],
    start: str,
    end: str,
    cache_path: Path,
    retries: int,
    keep_jobs: bool,
) -> dict:
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError:
            cache_path.unlink()

    bbox = geometry_bbox(chunk)
    cube = build_cube(connection, bbox, start, end)
    geometries = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": feature["geometry"]}
            for feature in chunk
        ],
    }
    aggregated = cube.aggregate_spatial(geometries=geometries, reducer="mean")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        job = None
        try:
            job = aggregated.execute_batch(out_format="JSON", title=f"agri_{cache_path.stem}")
            tmp = cache_path.with_suffix(".part")
            job.get_results().download_file(str(tmp))
            with open(tmp, encoding="utf-8") as handle:
                payload = json.load(handle)
            os.replace(tmp, cache_path)
            if not keep_jobs:
                try:
                    job.delete_job()
                except Exception as exc:
                    print(f"    job cleanup failed ({type(exc).__name__}); quota may leak")
            return payload
        except Exception as exc:
            last_error = exc
            if job is not None and not keep_jobs:
                try:
                    job.delete_job()
                except Exception:
                    pass
            if attempt == retries:
                break
            delay = 15 * attempt
            print(f"    attempt {attempt}/{retries}: {type(exc).__name__}: {exc}; retry in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"aggregate_spatial failed for {cache_path.stem}") from last_error


# --------------------------------------------------------------------------- #
# chunking
# --------------------------------------------------------------------------- #
def chunk_fields(features: list[dict], size: int) -> list[list[dict]]:
    """Chunk in rough spatial order so each job covers a compact bbox."""
    def key(feature: dict) -> tuple[float, float]:
        points = list(iter_positions(feature["geometry"]["coordinates"]))
        lats = [point[1] for point in points]
        lons = [point[0] for point in points]
        return (round(sum(lats) / len(lats), 2), sum(lons) / len(lons))

    ordered = sorted(features, key=key)
    return [ordered[index : index + size] for index in range(0, len(ordered), size)]


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(
    client: str,
    start: str | None,
    end: str | None,
    chunk_size: int,
    latency_days: int,
    retries: int,
    headless: bool | None,
    keep_jobs: bool,
    full_refresh: bool,
) -> Path:
    directory = CLIENTS_DIR / client
    if not directory.exists():
        raise SystemExit(f"No such client: {directory}")
    cache_dir = ensure_dir(directory / "_cache")
    observations_path = directory / "observations.csv"

    meta = load_json(directory / "client.json")
    fields = load_json(directory / "fields.geojson")
    features = fields.get("features", [])
    for feature in features:
        feature["properties"]["field_id"] = int(feature["properties"]["field_id"])
    field_ids = [feature["properties"]["field_id"] for feature in features]
    if len(set(field_ids)) != len(field_ids):
        raise SystemExit("Duplicate field_id in fields.geojson — run 01_setup_check.py")

    latest = latest_available_date(latency_days)
    end_date = min(parse_date(end), latest) if end else latest

    existing = pd.DataFrame(columns=OBSERVATION_COLUMNS)
    if observations_path.exists() and not full_refresh:
        existing = pd.read_csv(observations_path)

    if start:
        start_date = parse_date(start)
    elif len(existing) and not full_refresh:
        # Incremental: resume the day after the last stored observation.
        start_date = parse_date(str(existing["date"].max())) + dt.timedelta(days=1)
    else:
        start_date = parse_date(meta.get("monitoring_start", f"{end_date.year}-01-01"))

    if start_date > end_date:
        print(f"Up to date: last observation {start_date - dt.timedelta(days=1)}, latest available {end_date}")
        return observations_path

    chunks = chunk_fields(features, chunk_size)
    years = list(range(start_date.year, end_date.year + 1))
    print(f"Client: {meta.get('label', client)}")
    print(f"Fields: {len(features)} (all measured, no sampling)")
    print(f"Range:  {start_date} .. {end_date}")
    print(f"Jobs:   up to {len(chunks) * len(years)}")

    connection = connect(headless=headless)
    rows: list[dict] = []
    job_log: list[dict] = []

    for chunk_index, chunk in enumerate(chunks):
        chunk_ids = [feature["properties"]["field_id"] for feature in chunk]
        for year in years:
            period_start = max(dt.date(year, 1, 1), start_date)
            period_end = min(dt.date(year, 12, 31), end_date)
            if period_start > period_end:
                continue
            tag = f"{FETCH_SCHEMA_VERSION}_{chunk_index:03d}_{period_start:%Y%m%d}_{period_end:%Y%m%d}"
            cache_path = cache_dir / f"agg_{tag}.json"
            print(f"  chunk {chunk_index + 1}/{len(chunks)} ({len(chunk)} fields) {period_start}..{period_end}")
            started = time.time()
            payload = run_job(connection, chunk, period_start.isoformat(),
                              period_end.isoformat(), cache_path, retries, keep_jobs)
            parsed = parse_result(payload, chunk_ids)
            rows.extend(parsed)
            job_log.append({
                "chunk": chunk_index, "fields": len(chunk),
                "start": period_start.isoformat(), "end": period_end.isoformat(),
                "observations": len(parsed), "seconds": round(time.time() - started, 1),
            })

    fetched = pd.DataFrame(rows, columns=OBSERVATION_COLUMNS) if rows else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    if len(fetched):
        before = len(fetched)
        fetched = fetched[fetched["valid_frac"].fillna(1.0) >= MIN_VALID_FRAC]
        dropped = before - len(fetched)
        if dropped:
            print(f"  dropped {dropped} observations below {MIN_VALID_FRAC:.0%} clear coverage")

    combined = pd.concat([existing, fetched], ignore_index=True)
    combined = (
        combined.drop_duplicates(subset=["field_id", "date"], keep="last")
        .sort_values(["field_id", "date"])
        .reset_index(drop=True)
    )
    tmp = observations_path.with_suffix(".csv.part")
    combined.to_csv(tmp, index=False)
    os.replace(tmp, observations_path)

    history = load_json(directory / "fetch_log.json", default={"runs": []})
    history["runs"].append({
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "start": start_date.isoformat(), "end": end_date.isoformat(),
        "new_observations": int(len(fetched)),
        "total_observations": int(len(combined)),
        "indices": list(INDICES),
        "jobs": job_log,
    })
    history["runs"] = history["runs"][-50:]
    write_json_atomic(directory / "fetch_log.json", history)

    print(f"\n  -> {observations_path}  (+{len(fetched):,} new, {len(combined):,} total)")
    return observations_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--start", default=None, help="default: day after last stored observation")
    parser.add_argument("--end", default=None, help="default: latest available")
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--latency-days", type=int, default=5)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-jobs", action="store_true")
    parser.add_argument("--full-refresh", action="store_true", help="refetch history from scratch")
    args = parser.parse_args()
    main(args.client, args.start, args.end, args.chunk_size, args.latency_days,
         args.retries, True if args.headless else None, args.keep_jobs, args.full_refresh)
