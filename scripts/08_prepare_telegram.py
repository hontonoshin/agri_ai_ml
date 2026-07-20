"""Prepare the full field-boundary index used by the Telegram location flow."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from telegram_bot.field_lookup import FieldIndex  # noqa: E402


def _register(bot_data: Path, region_id: str, label: str, client: str, destination: Path) -> None:
    config = bot_data / "regions.json"
    payload = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {"regions": []}
    relative = str(destination.relative_to(bot_data)) if destination.is_relative_to(bot_data) else str(destination)
    entry = {"id": region_id, "label": label, "client": client, "fields": relative}
    payload["regions"] = [item for item in payload.get("regions", []) if item.get("id") != region_id]
    payload["regions"].append(entry)
    config.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main(source_dir: str, client: str, output: str | None = None,
         region_id: str | None = None, label: str | None = None) -> Path:
    source = Path(source_dir).expanduser().resolve()
    fields_source = source / "fields.geojson"
    if not fields_source.exists():
        raise SystemExit(f"Missing full field boundaries: {fields_source}")
    client_dir = ROOT / "clients" / client
    required = [client_dir / "observations.csv", client_dir / "fields.geojson",
                client_dir / "models" / "canopy_isolation_forest.joblib"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Prepare and train the regional client first:\n- " + "\n- ".join(missing))

    region_id = region_id or client
    bot_data = ROOT / "bot_data"
    destination = (Path(output).expanduser().resolve() if output
                   else bot_data / "regions" / region_id / "fields.geojson")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".geojson.part")
    shutil.copyfile(fields_source, temporary)
    temporary.replace(destination)
    index = FieldIndex(destination)
    metadata = {
        "client": client,
        "field_count": len(index.fields),
        "source": str(fields_source),
        "prepared_file": str(destination),
    }
    _register(bot_data, region_id, label or region_id.title(), client, destination)
    (destination.parent / "prepared.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, help="region directory containing full fields.geojson")
    parser.add_argument("--client", default="khorezm")
    parser.add_argument("--output", help="optional destination GeoJSON")
    parser.add_argument("--region-id", help="stable region id; defaults to client")
    parser.add_argument("--label", help="user-facing region name")
    args = parser.parse_args()
    main(args.source_dir, args.client, args.output, args.region_id, args.label)
