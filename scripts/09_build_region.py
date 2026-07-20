"""Register a region, fetch its Sentinel history and train its own ML baseline.

This script needs a full field-boundary GeoJSON produced by a cadastral source
or a separately validated field-segmentation pipeline. A land-cover raster is
not silently converted into cadastral fields because connected cropland pixels
can merge several farms and produce misleading boundaries.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from common import polygon_area_ha, representative_point  # noqa: E402
from telegram_bot.settings import load_env  # noqa: E402


def _prepare_feature(feature: dict, fallback_id: int, years: range) -> dict:
    copy = json.loads(json.dumps(feature))
    props = copy.setdefault("properties", {})
    props["field_id"] = int(props.get("field_id", fallback_id))
    props["area_ha"] = round(float(props.get("area_ha") or polygon_area_ha(copy["geometry"])), 4)
    props.setdefault("name", f"Field {props['field_id']}")
    props["seasons"] = {str(year): str((props.get("seasons") or {}).get(str(year), "other")) for year in years}
    props.setdefault("crop_source", "user_selected_or_unverified")
    return copy


def _register(bot_data: Path, region_id: str, label: str, client: str, fields_path: Path) -> None:
    config = bot_data / "regions.json"
    payload = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {"regions": []}
    entry = {"id": region_id, "label": label, "client": client,
             "fields": str(fields_path.relative_to(bot_data))}
    payload["regions"] = [item for item in payload.get("regions", []) if item.get("id") != region_id]
    payload["regions"].append(entry)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(fields_file: str, region_id: str, client: str, label: str, start: str,
         reference_size: int, fetch: bool, chunk_size: int) -> None:
    source = Path(fields_file).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    raw = payload.get("features", [])
    if not raw:
        raise SystemExit("The field GeoJSON contains no features")
    start_date = dt.date.fromisoformat(start)
    years = range(start_date.year, dt.date.today().year + 1)
    features = [_prepare_feature(feature, number, years) for number, feature in enumerate(raw, 1)]
    ids = [feature["properties"]["field_id"] for feature in features]
    if len(ids) != len(set(ids)):
        raise SystemExit("field_id values must be unique")
    usable = [feature for feature in features if feature["properties"]["area_ha"] >= 0.3]
    if not usable:
        raise SystemExit("No fields of at least 0.3 ha were found")

    # Spatial ordering followed by even selection is deterministic and avoids
    # taking the first fields from only one district.
    usable.sort(key=lambda feature: representative_point(feature["geometry"]))
    if reference_size == 1:
        reference = [usable[len(usable) // 2]]
    elif reference_size > 1 and reference_size < len(usable):
        reference = [usable[round(i * (len(usable)-1) / (reference_size-1))] for i in range(reference_size)]
    else:
        reference = usable

    client_dir = ROOT / "clients" / client
    client_dir.mkdir(parents=True, exist_ok=True)
    (client_dir / "fields.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": reference}, ensure_ascii=False), encoding="utf-8")
    (client_dir / "client.json").write_text(json.dumps({
        "label": label, "language": "uz", "monitoring_start": start,
        "field_count": len(reference), "full_boundary_count": len(features),
        "available_indices": ["ndvi", "ndre", "ndmi", "evi", "savi"],
        "crop_warning": "Crop labels are user-provided or unverified; ML uses a regional fallback until labels are validated.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    bot_data = ROOT / "bot_data"
    registered = bot_data / "regions" / region_id / "fields.geojson"
    registered.parent.mkdir(parents=True, exist_ok=True)
    registered.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                     ensure_ascii=False), encoding="utf-8")
    _register(bot_data, region_id, label, client, registered)
    print(f"Registered {len(features):,} boundaries; reference/cache set: {len(reference):,} fields")

    if fetch:
        load_env(ROOT / ".env")
        if not (os.environ.get("CDSE_CLIENT_ID") and os.environ.get("CDSE_CLIENT_SECRET")):
            raise SystemExit("Add CDSE_CLIENT_ID and CDSE_CLIENT_SECRET to .env before --fetch")
        subprocess.run([sys.executable, str(HERE / "02_fetch_indices.py"), "--client", client,
                        "--start", start, "--chunk-size", str(chunk_size), "--headless"], check=True)
        subprocess.run([sys.executable, str(HERE / "06_train_ml.py"), "--client", client], check=True)
        print("Region history cached and model trained.")
    else:
        print("Boundaries prepared only. Re-run with --fetch to cache Sentinel history and train the model.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fields", required=True, help="validated full field-boundary GeoJSON")
    parser.add_argument("--region-id", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--start", default=f"{dt.date.today().year-3}-01-01")
    parser.add_argument("--reference-size", type=int, default=0,
                        help="0 caches all fields; a positive value uses a spatial sample")
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    main(args.fields, args.region_id, args.client, args.label, args.start,
         args.reference_size, args.fetch, args.chunk_size)
