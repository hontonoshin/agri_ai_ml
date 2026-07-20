"""Fast Sentinel Hub true-colour previews with field-boundary overlays."""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from .field_lookup import FieldMatch, outer_rings
from .settings import Settings

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

EVALSCRIPT = """
//VERSION=3
function setup() {
  return {input: ["B02", "B03", "B04", "dataMask"], output: {bands: 4}};
}
function evaluatePixel(s) {
  let gain = 2.8;
  return [gain*s.B04, gain*s.B03, gain*s.B02, s.dataMask];
}
"""

_TOKEN: str | None = None
_TOKEN_EXPIRES = 0.0
_TOKEN_LOCK = threading.Lock()


def _request(url: str, data: bytes, headers: dict[str, str], timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _access_token(settings: Settings) -> str:
    global _TOKEN, _TOKEN_EXPIRES
    if not settings.cdse_client_id or not settings.cdse_client_secret:
        raise RuntimeError("CDSE_CLIENT_ID and CDSE_CLIENT_SECRET are required for satellite previews")
    with _TOKEN_LOCK:
        if _TOKEN and time.time() < _TOKEN_EXPIRES:
            return _TOKEN
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": settings.cdse_client_id,
            "client_secret": settings.cdse_client_secret,
        }).encode()
        payload = json.loads(_request(TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"}))
        _TOKEN = str(payload["access_token"])
        _TOKEN_EXPIRES = time.time() + min(int(payload.get("expires_in", 600)) - 30, 3000)
        return _TOKEN


def satellite_preview(settings: Settings, field: FieldMatch, lat: float, lon: float, path: Path) -> Path:
    """Download a recent low-cloud RGB mosaic and draw the candidate boundary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = settings.bot_data / "satellite_cache" / settings.client / f"field_{field.field_id}.png"
    cache.parent.mkdir(parents=True, exist_ok=True)
    fresh = cache.exists() and dt.datetime.now().timestamp() - cache.stat().st_mtime < settings.cache_days * 86400
    if not fresh:
        west, south, east, north = field.bounds
        width = max(east - west, 0.002)
        height = max(north - south, 0.002)
        pad = max(width, height) * 0.65
        bounds = [west - pad, south - pad, east + pad, north + pad]
        end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=settings.latency_days)
        start = end - dt.timedelta(days=120)
        payload = {
            "input": {
                "bounds": {"bbox": bounds, "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
                "data": [{"type": "sentinel-2-l2a", "dataFilter": {
                    "timeRange": {"from": start.isoformat().replace("+00:00", "Z"),
                                  "to": end.isoformat().replace("+00:00", "Z")},
                    "mosaickingOrder": "leastCC", "maxCloudCoverage": 60,
                }}],
            },
            "output": {"width": 720, "height": 720, "responses": [
                {"identifier": "default", "format": {"type": "image/png"}}
            ]},
            "evalscript": EVALSCRIPT,
        }
        token = _access_token(settings)
        content = _request(PROCESS_URL, json.dumps(payload).encode(), {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/png",
        })
        cache.write_bytes(content)
        cache.with_suffix(".json").write_text(json.dumps({"bbox": bounds}), encoding="utf-8")

    metadata = json.loads(cache.with_suffix(".json").read_text(encoding="utf-8"))
    west, south, east, north = metadata["bbox"]
    image = Image.open(cache).convert("RGB")
    draw = ImageDraw.Draw(image)
    sx = image.width / (east - west)
    sy = image.height / (north - south)
    def pixel(point):
        return ((float(point[0]) - west) * sx, (north - float(point[1])) * sy)
    for ring in outer_rings(field.geometry):
        draw.line([pixel(point) for point in ring], fill=(255, 35, 35), width=5, joint="curve")
    x, y = pixel([lon, lat])
    radius = 7
    draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(25, 95, 255), outline="white", width=2)
    image.save(path, format="PNG", optimize=True)
    return path
