"""Multi-region field registry with backward compatibility for one fields file."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .field_lookup import FieldIndex, FieldMatch
from .settings import Settings


@dataclass(frozen=True)
class LocatedField:
    region_id: str
    region_label: str
    client: str
    field: FieldMatch
    index: FieldIndex


class RegionRegistry:
    def __init__(self, settings: Settings):
        config = settings.bot_data / "regions.json"
        if config.exists():
            entries = json.loads(config.read_text(encoding="utf-8")).get("regions", [])
        else:
            entries = [{"id": settings.client, "label": settings.client,
                        "client": settings.client, "fields": str(settings.fields_path)}]
        self.regions: list[tuple[dict, FieldIndex]] = []
        for entry in entries:
            path = Path(entry["fields"])
            if not path.is_absolute():
                path = settings.bot_data / path
            self.regions.append((entry, FieldIndex(path)))
        if not self.regions:
            raise RuntimeError("No regions are configured")

    def find(self, lat: float, lon: float) -> LocatedField | None:
        matches = []
        for entry, index in self.regions:
            field = index.find(lat, lon)
            if field:
                matches.append(self._located(entry, index, field))
        return min(matches, key=lambda item: item.field.area_ha or float("inf")) if matches else None

    def nearest(self, lat: float, lon: float, max_distance_km: float = 5.0) -> tuple[LocatedField, float] | None:
        matches = []
        for entry, index in self.regions:
            nearest = index.nearest(lat, lon, max_distance_km)
            if nearest:
                field, distance = nearest
                matches.append((self._located(entry, index, field), distance))
        return min(matches, key=lambda item: item[1]) if matches else None

    def get(self, region_id: str, field_id: int) -> LocatedField | None:
        for entry, index in self.regions:
            if str(entry["id"]) == str(region_id):
                field = index.get(field_id)
                return self._located(entry, index, field) if field else None
        return None

    @staticmethod
    def _located(entry: dict, index: FieldIndex, field: FieldMatch) -> LocatedField:
        return LocatedField(str(entry["id"]), str(entry.get("label", entry["id"])),
                            str(entry["client"]), field, index)
