"""Environment and path configuration for the Telegram application."""
from __future__ import annotations

import os
import json
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """Small .env reader; existing environment variables always win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    root: Path
    token: str
    client: str
    bot_data: Path
    fields_path: Path
    database_path: Path
    reports_dir: Path
    field_cache_dir: Path
    air_quality_cache_dir: Path
    client_dir: Path
    model_path: Path
    on_demand_years: int
    cache_days: int
    latency_days: int
    alert_percentile: float
    min_reliable_area_ha: float
    nearest_field_km: float
    cdse_client_id: str | None
    cdse_client_secret: str | None
    air_quality_enabled: bool
    air_quality_api_url: str
    air_quality_api_key: str | None
    air_quality_cache_hours: int

    @classmethod
    def load(cls, require_token: bool = True) -> "Settings":
        load_env(ROOT / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if require_token and not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add the BotFather token."
            )
        client = os.getenv("DEFAULT_CLIENT", "khorezm").strip()
        bot_data = Path(os.getenv("BOT_DATA_DIR", str(ROOT / "bot_data"))).expanduser().resolve()
        client_dir = ROOT / "clients" / client
        return cls(
            root=ROOT,
            token=token,
            client=client,
            bot_data=bot_data,
            fields_path=Path(os.getenv("FULL_FIELDS_PATH", str(bot_data / "fields.geojson"))).expanduser().resolve(),
            database_path=bot_data / "requests.sqlite3",
            reports_dir=bot_data / "reports",
            field_cache_dir=bot_data / "field_cache",
            air_quality_cache_dir=bot_data / "air_quality_cache",
            client_dir=client_dir,
            model_path=client_dir / "models" / "canopy_isolation_forest.joblib",
            on_demand_years=int(os.getenv("ON_DEMAND_YEARS", "3")),
            cache_days=int(os.getenv("FIELD_CACHE_DAYS", "7")),
            latency_days=int(os.getenv("SENTINEL_LATENCY_DAYS", "5")),
            alert_percentile=float(os.getenv("ML_ALERT_PERCENTILE", "0.97")),
            min_reliable_area_ha=float(os.getenv("MIN_RELIABLE_AREA_HA", "0.5")),
            nearest_field_km=float(os.getenv("NEAREST_FIELD_KM", "5")),
            cdse_client_id=os.getenv("CDSE_CLIENT_ID") or None,
            cdse_client_secret=os.getenv("CDSE_CLIENT_SECRET") or None,
            air_quality_enabled=os.getenv("AIR_QUALITY_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            air_quality_api_url=os.getenv(
                "AIR_QUALITY_API_URL", "https://air-quality-api.open-meteo.com/v1/air-quality"
            ),
            air_quality_api_key=os.getenv("AIR_QUALITY_API_KEY") or None,
            air_quality_cache_hours=int(os.getenv("AIR_QUALITY_CACHE_HOURS", "3")),
        )

    def ensure_directories(self) -> None:
        for directory in (self.bot_data, self.reports_dir, self.field_cache_dir,
                          self.air_quality_cache_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def for_client(self, client: str) -> "Settings":
        directory = self.root / "clients" / client
        return replace(self, client=client, client_dir=directory,
                       model_path=directory / "models" / "canopy_isolation_forest.joblib")

    def validate_runtime(self) -> None:
        config = self.bot_data / "regions.json"
        if config.exists():
            payload = json.loads(config.read_text(encoding="utf-8"))
            required = []
            for region in payload.get("regions", []):
                region_settings = self.for_client(str(region["client"]))
                fields = Path(region["fields"])
                if not fields.is_absolute():
                    fields = self.bot_data / fields
                required.extend([fields, region_settings.client_dir / "observations.csv", region_settings.model_path])
        else:
            required = [self.fields_path, self.client_dir / "observations.csv", self.model_path]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError("Required prepared files are missing:\n- " + "\n- ".join(missing))
