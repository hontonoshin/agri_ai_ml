"""Dependency-free spatial lookup for Telegram location points."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def rings(geometry: dict) -> Iterable[list[list[float]]]:
    if geometry.get("type") == "Polygon":
        yield from geometry.get("coordinates", [])
    elif geometry.get("type") == "MultiPolygon":
        for polygon in geometry.get("coordinates", []):
            yield from polygon


def outer_rings(geometry: dict) -> Iterable[list[list[float]]]:
    if geometry.get("type") == "Polygon":
        coordinates = geometry.get("coordinates", [])
        if coordinates:
            yield coordinates[0]
    elif geometry.get("type") == "MultiPolygon":
        for polygon in geometry.get("coordinates", []):
            if polygon:
                yield polygon[0]


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        crosses = (y1 > lat) != (y2 > lat)
        if crosses:
            intersection = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-15) + x1
            if lon < intersection:
                inside = not inside
        previous = current
    return inside


def contains(geometry: dict, lon: float, lat: float) -> bool:
    if geometry.get("type") == "Polygon":
        polygons = [geometry.get("coordinates", [])]
    elif geometry.get("type") == "MultiPolygon":
        polygons = geometry.get("coordinates", [])
    else:
        return False
    for polygon in polygons:
        if not polygon or not point_in_ring(lon, lat, polygon[0]):
            continue
        if not any(point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False


def bbox(geometry: dict) -> tuple[float, float, float, float]:
    points = [point for ring in rings(geometry) for point in ring]
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _segment_distance_km(lon: float, lat: float, a: list[float], b: list[float]) -> float:
    """Approximate point-to-segment distance in a local metric projection."""
    scale_x = 111.320 * math.cos(math.radians(lat))
    ax, ay = (float(a[0]) - lon) * scale_x, (float(a[1]) - lat) * 111.132
    bx, by = (float(b[0]) - lon) * scale_x, (float(b[1]) - lat) * 111.132
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    return math.hypot(ax + t * dx, ay + t * dy)


def distance_km(geometry: dict, lon: float, lat: float) -> float:
    if contains(geometry, lon, lat):
        return 0.0
    best = float("inf")
    for ring in rings(geometry):
        if len(ring) < 2:
            continue
        previous = ring[-1]
        for current in ring:
            best = min(best, _segment_distance_km(lon, lat, previous, current))
            previous = current
    return best


@dataclass(frozen=True)
class FieldMatch:
    field_id: int
    area_ha: float
    geometry: dict
    properties: dict[str, Any]
    bounds: tuple[float, float, float, float]


class FieldIndex:
    """Uniform-grid bbox index followed by exact ray-casting containment."""
    def __init__(self, path: str | Path, cell_size: float = 0.02):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.cell_size = cell_size
        self.fields: list[FieldMatch] = []
        self.by_id: dict[int, FieldMatch] = {}
        self.grid: dict[tuple[int, int], list[int]] = {}
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            if properties.get("field_id") is None:
                continue
            geometry = feature.get("geometry") or {}
            if geometry.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            bounds = bbox(geometry)
            field = FieldMatch(
                field_id=int(properties["field_id"]),
                area_ha=float(properties.get("area_ha") or 0.0),
                geometry=geometry,
                properties=properties,
                bounds=bounds,
            )
            index = len(self.fields)
            self.fields.append(field)
            self.by_id[field.field_id] = field
            x0, y0 = self._cell(bounds[0], bounds[1])
            x1, y1 = self._cell(bounds[2], bounds[3])
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    self.grid.setdefault((x, y), []).append(index)
        if not self.fields:
            raise ValueError(f"No polygon fields found in {path}")

    def _cell(self, lon: float, lat: float) -> tuple[int, int]:
        return math.floor(lon / self.cell_size), math.floor(lat / self.cell_size)

    def find(self, lat: float, lon: float) -> FieldMatch | None:
        candidates = self.grid.get(self._cell(lon, lat), [])
        matches = [
            self.fields[index] for index in candidates
            if self.fields[index].bounds[0] <= lon <= self.fields[index].bounds[2]
            and self.fields[index].bounds[1] <= lat <= self.fields[index].bounds[3]
            and contains(self.fields[index].geometry, lon, lat)
        ]
        if not matches:
            return None
        return min(matches, key=lambda field: field.area_ha or float("inf"))

    def get(self, field_id: int) -> FieldMatch | None:
        return self.by_id.get(int(field_id))

    def nearest(self, lat: float, lon: float, max_distance_km: float = 5.0) -> tuple[FieldMatch, float] | None:
        """Return the closest mapped field within the configured search radius."""
        # Cheap bbox-distance shortlist avoids full polygon distance for large registries.
        scale_x = 111.320 * math.cos(math.radians(lat))
        shortlist: list[tuple[float, FieldMatch]] = []
        for field in self.fields:
            west, south, east, north = field.bounds
            dx = max(west - lon, 0.0, lon - east) * scale_x
            dy = max(south - lat, 0.0, lat - north) * 111.132
            rough = math.hypot(dx, dy)
            if rough <= max_distance_km:
                shortlist.append((rough, field))
        shortlist.sort(key=lambda item: item[0])
        best: tuple[FieldMatch, float] | None = None
        for _, field in shortlist[:80]:
            exact = distance_km(field.geometry, lon, lat)
            if exact <= max_distance_km and (best is None or exact < best[1]):
                best = (field, exact)
        return best

    def nearby(self, field: FieldMatch, margin: float = 0.01) -> list[FieldMatch]:
        west, south, east, north = field.bounds
        return [
            candidate for candidate in self.fields
            if candidate.field_id != field.field_id
            and candidate.bounds[2] >= west - margin and candidate.bounds[0] <= east + margin
            and candidate.bounds[3] >= south - margin and candidate.bounds[1] <= north + margin
        ][:80]
