"""Regional air-quality context from CAMS through the Open-Meteo API.

Air-quality cells are roughly 45 km for the global CAMS model. These values are
regional context for a user-selected coordinate; they are never treated as a
measurement of an individual field or as evidence used by the canopy ML model.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .settings import Settings


VARIABLES = (
    "european_aqi", "us_aqi", "pm10", "pm2_5", "carbon_monoxide",
    "nitrogen_dioxide", "sulphur_dioxide", "ozone", "dust",
    "aerosol_optical_depth",
)


@dataclass
class AirQualityResult:
    time: str
    latitude: float
    longitude: float
    european_aqi: float | None = None
    us_aqi: float | None = None
    pm10: float | None = None
    pm2_5: float | None = None
    carbon_monoxide: float | None = None
    nitrogen_dioxide: float | None = None
    sulphur_dioxide: float | None = None
    ozone: float | None = None
    dust: float | None = None
    aerosol_optical_depth: float | None = None
    source: str = "CAMS global forecast via Open-Meteo"
    resolution_km: int = 45


def _number(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def parse_air_quality(payload: dict) -> AirQualityResult:
    current = payload.get("current") or {}
    return AirQualityResult(
        time=str(current.get("time") or "unknown"),
        latitude=float(payload.get("latitude")),
        longitude=float(payload.get("longitude")),
        **{name: _number(current.get(name)) for name in VARIABLES},
    )


def european_aqi_category(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value <= 20:
        return "good"
    if value <= 40:
        return "fair"
    if value <= 60:
        return "moderate"
    if value <= 80:
        return "poor"
    if value <= 100:
        return "very poor"
    return "extremely poor"


class AirQualityProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _cache_path(self, latitude: float, longitude: float) -> Path:
        # CAMS global cells are coarse, so rounding also makes nearby requests
        # share a cache entry rather than repeatedly calling the public API.
        lat = round(latitude / 0.4) * 0.4
        lon = round(longitude / 0.4) * 0.4
        return self.settings.air_quality_cache_dir / f"cams_{lat:+06.1f}_{lon:+06.1f}.json"

    def get(self, latitude: float, longitude: float) -> AirQualityResult | None:
        if not self.settings.air_quality_enabled:
            return None
        cache = self._cache_path(latitude, longitude)
        if cache.exists():
            age = dt.datetime.now().timestamp() - cache.stat().st_mtime
            if age <= self.settings.air_quality_cache_hours * 3600:
                return AirQualityResult(**json.loads(cache.read_text(encoding="utf-8")))

        query = {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "current": ",".join(VARIABLES),
            "domains": "cams_global",
            "timezone": "auto",
            "forecast_days": "1",
            "cell_selection": "nearest",
        }
        if self.settings.air_quality_api_key:
            query["apikey"] = self.settings.air_quality_api_key
        url = self.settings.air_quality_api_url.rstrip("/") + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"User-Agent": "UzAgriAI/2.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            result = parse_air_quality(json.load(response))
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".part")
        temporary.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache)
        return result
