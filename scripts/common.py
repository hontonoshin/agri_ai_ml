"""Shared utilities: paths, atomic IO, openEO connection, geometry validation."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
CLIENTS_DIR = ROOT / "clients"
BACKEND = "https://openeo.dataspace.copernicus.eu"


# --------------------------------------------------------------------------- #
# filesystem
# --------------------------------------------------------------------------- #
def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json_atomic(path: str | Path, data: Any) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return path


def load_json(path: str | Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        if default is None:
            raise
        return default


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
def parse_date(value: str | dt.date) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def latest_available_date(latency_days: int = 5) -> dt.date:
    """Conservative date by which L2A is reliably published on CDSE."""
    return dt.date.today() - dt.timedelta(days=latency_days)


def season_year(date: dt.date) -> int:
    return date.year


# --------------------------------------------------------------------------- #
# openEO
# --------------------------------------------------------------------------- #
def connect(backend: str = BACKEND, headless: bool | None = None):
    """Connect to openEO. Headless when CDSE_CLIENT_ID/SECRET are set."""
    import openeo

    connection = openeo.connect(backend)
    client_id = os.environ.get("CDSE_CLIENT_ID")
    client_secret = os.environ.get("CDSE_CLIENT_SECRET")
    use_headless = headless if headless is not None else bool(client_id and client_secret)

    if use_headless:
        if not (client_id and client_secret):
            raise SystemExit(
                "Headless auth requires CDSE_CLIENT_ID and CDSE_CLIENT_SECRET. "
                "Create an OAuth client in the Copernicus Data Space dashboard."
            )
        connection.authenticate_oidc_client_credentials(
            client_id=client_id, client_secret=client_secret
        )
        print("  auth: client credentials (headless)")
    else:
        connection.authenticate_oidc()
        print("  auth: interactive OIDC")
    return connection


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
def iter_positions(coordinates: Any) -> Iterable[tuple[float, float]]:
    if (
        isinstance(coordinates, (list, tuple))
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        yield float(coordinates[0]), float(coordinates[1])
        return
    if isinstance(coordinates, (list, tuple)):
        for item in coordinates:
            yield from iter_positions(item)


def geometry_bbox(features: list[dict], pad: float = 0.005) -> dict:
    lons: list[float] = []
    lats: list[float] = []
    for feature in features:
        for lon, lat in iter_positions(feature["geometry"]["coordinates"]):
            lons.append(lon)
            lats.append(lat)
    if not lons:
        raise ValueError("No coordinates found in features")
    return {
        "west": min(lons) - pad,
        "south": min(lats) - pad,
        "east": max(lons) + pad,
        "north": max(lats) + pad,
    }


def representative_point(geometry: dict) -> tuple[float, float]:
    """Return (lat, lon). Uses shapely when available."""
    try:
        from shapely.geometry import shape

        point = shape(geometry).representative_point()
        return float(point.y), float(point.x)
    except Exception:
        points = list(iter_positions(geometry.get("coordinates", [])))
        if not points:
            return float("nan"), float("nan")
        return (
            sum(point[1] for point in points) / len(points),
            sum(point[0] for point in points) / len(points),
        )


def polygon_area_ha(geometry: dict) -> float:
    """Geodesic polygon area in hectares.

    Falls back to an equirectangular shoelace when pyproj/shapely are absent.
    Never returns a silent 0.0 for a valid polygon: a report telling a client
    their field is zero hectares is worse than a crash.
    """
    try:
        from pyproj import Geod
        from shapely.geometry import shape

        area_m2, _ = Geod(ellps="WGS84").geometry_area_perimeter(shape(geometry))
        return abs(float(area_m2)) / 10_000.0
    except ImportError:
        pass

    import math

    def ring_area(ring: list) -> float:
        if len(ring) < 4:
            return 0.0
        lat0 = math.radians(sum(point[1] for point in ring) / len(ring))
        metres_per_deg_lat = 111_132.0
        metres_per_deg_lon = 111_320.0 * math.cos(lat0)
        xs = [point[0] * metres_per_deg_lon for point in ring]
        ys = [point[1] * metres_per_deg_lat for point in ring]
        total = 0.0
        for index in range(len(ring) - 1):
            total += xs[index] * ys[index + 1] - xs[index + 1] * ys[index]
        return abs(total) / 2.0

    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "Polygon":
        polygons = [coordinates]
    elif kind == "MultiPolygon":
        polygons = coordinates
    else:
        raise ValueError(f"Cannot compute area for geometry type {kind!r}")

    area_m2 = 0.0
    for polygon in polygons:
        if not polygon:
            continue
        area_m2 += ring_area(polygon[0])                    # exterior
        for hole in polygon[1:]:                            # holes
            area_m2 -= ring_area(hole)
    return area_m2 / 10_000.0


def copernicus_browser_url(lat: float, lon: float, date: str) -> str:
    return (
        "https://browser.dataspace.copernicus.eu/?zoom=16"
        f"&lat={lat:.5f}&lng={lon:.5f}&datasetId=S2_L2A_CDAS"
        f"&layerId=1_TRUE_COLOR&fromTime={date}T00:00:00.000Z"
        f"&toTime={date}T23:59:59.999Z"
    )
