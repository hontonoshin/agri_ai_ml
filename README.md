# UzAgriAI Developer Guide

UzAgriAI is a Telegram-first agricultural screening system for Uzbekistan. A
user shares a GPS location, confirms a detected agricultural field on a
Sentinel-2 preview, selects the crop, and receives:

- NDVI, NDRE, NDMI, EVI and SAVI histories;
- an unsupervised anomaly percentile;
- a recent Sentinel-2 field image with the selected boundary;
- regional CAMS air-quality context;
- a multilingual PDF report in Uzbek, Russian or English.

The system prioritizes fields for inspection. It does not diagnose disease,
measure soil properties, determine irrigation requirements, estimate legal
ownership or replace field inspection.

## 1. Architecture

```text
Telegram location
  -> multi-region polygon lookup
  -> Sentinel-2 preview and boundary confirmation
  -> crop selection
  -> cached history or on-demand openEO retrieval
  -> five vegetation indices
  -> regional Isolation Forest scoring
  -> CAMS/Open-Meteo air-quality context
  -> Telegram summary, chart and PDF
```

Tashkent currently uses:

- 68,005 predicted field boundaries for location matching;
- 1,000 spatially distributed fields for the regional ML reference;
- on-demand retrieval for other matched fields.

The field boundaries are public Fields of the World ML predictions. They are
not cadastral or ownership records.

## 2. Main directories

```text
agri_ai_ml/
├── app.py                         Streamlit regional dashboard
├── bot_admin.py                   Bot operations dashboard
├── run_bot.py                     Telegram entry point
├── requirements.txt
├── .env                           secrets; never commit
├── scripts/
│   ├── 00_import_legacy_region.py
│   ├── 01_setup_check.py
│   ├── 02_fetch_indices.py
│   ├── 03_anomalies.py
│   ├── 04_report.py
│   ├── 05_run_weekly.py
│   ├── 06_train_ml.py
│   ├── 07_evaluate_feedback.py
│   ├── 08_prepare_telegram.py
│   ├── 09_build_region.py
│   ├── 10_cache_satellite_previews.py
│   └── 11_download_ftw_region.py
├── telegram_bot/
│   ├── bot.py
│   ├── analysis.py
│   ├── air_quality.py
│   ├── copernicus.py
│   ├── field_lookup.py
│   ├── regions.py
│   ├── reporting.py
│   ├── satellite.py
│   ├── settings.py
│   ├── storage.py
│   └── texts.py
├── clients/<region>/              reference observations and model
├── data/regions/<region>/         source regional boundaries
└── bot_data/                      runtime caches, reports and SQLite
```

## 3. Requirements

- Linux is recommended. Development was tested on Fedora.
- Python 3.11 or 3.12 is recommended. Python 3.13 may work but can produce
  dependency deprecation warnings.
- A Telegram bot token from `@BotFather`.
- Copernicus Data Space OAuth client ID and secret.
- Network access to Telegram, Copernicus Data Space and Open-Meteo.

External services:

- Copernicus Data Space: <https://dataspace.copernicus.eu/>
- openEO backend: <https://openeo.dataspace.copernicus.eu/>
- Open-Meteo air-quality API: <https://open-meteo.com/en/docs/air-quality-api>
- python-telegram-bot: <https://docs.python-telegram-bot.org/>

## 4. Installation

Run commands from the project root.

```bash
cd ~/Downloads/agri_ai_ml

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify that the environment belongs to this project:

```bash
which python
```

Expected:

```text
/home/USER/Downloads/agri_ai_ml/.venv/bin/python
```

If the project directory was moved, recreate `.venv`. Python virtual
environments contain absolute paths and should not be moved between folders.

## 5. Configuration

Create the environment file:

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

Example configuration:

```env
TELEGRAM_BOT_TOKEN=replace_with_new_bot_token
DEFAULT_CLIENT=khorezm

CDSE_CLIENT_ID=replace_with_copernicus_client_id
CDSE_CLIENT_SECRET=replace_with_copernicus_secret

ON_DEMAND_YEARS=3
FIELD_CACHE_DAYS=3
SENTINEL_LATENCY_DAYS=2
ML_ALERT_PERCENTILE=0.97
MIN_RELIABLE_AREA_HA=0.5
NEAREST_FIELD_KM=5

AIR_QUALITY_ENABLED=true
AIR_QUALITY_API_URL=https://air-quality-api.open-meteo.com/v1/air-quality
AIR_QUALITY_API_KEY=
AIR_QUALITY_CACHE_HOURS=3
```

Important:

- Define each variable only once.
- Never paste real tokens into issues, chat messages, screenshots or commits.
- Revoke and rotate a credential immediately if it is exposed.
- `.env` must remain in `.gitignore`.

Several data scripts read credentials from the process environment. Load `.env`
before running them:

```bash
set -a
source .env
set +a
```

Safe credential check:

```bash
python - <<'PY'
import os
print("Telegram:", bool(os.getenv("TELEGRAM_BOT_TOKEN")))
print("Copernicus ID:", bool(os.getenv("CDSE_CLIENT_ID")))
print("Copernicus secret:", bool(os.getenv("CDSE_CLIENT_SECRET")))
PY
```

Do not print the actual values.

## 6. Quick start with prepared data

Use this path when `clients/`, `data/` and `bot_data/regions.json` have already
been prepared.

```bash
cd ~/Downloads/agri_ai_ml
source .venv/bin/activate

python -m compileall -q scripts telegram_bot app.py run_bot.py
python run_bot.py
```

Telegram test sequence:

1. Send `/start`.
2. Select Uzbek, Russian or English.
3. Share a location in a registered region.
4. Confirm the satellite image and red field boundary.
5. Select the crop.
6. Wait for the chart and PDF.

Bot commands:

- `/start` or `/help` - begin a location request;
- `/language` - change language;
- `/status` - show recent request status;
- `/delete_my_data` - remove the user's requests and reports.

## 7. Build Tashkent Region from scratch

### 7.1 Download full predicted field boundaries

```bash
mkdir -p data/regions/tashkent

python scripts/11_download_ftw_region.py \
  --place "Tashkent Region, Uzbekistan" \
  --output data/regions/tashkent/fields.geojson \
  --year 2025 \
  --min-area-ha 0.3 \
  --max-area-ha 500
```

The remote GeoParquet query can take 30-90 minutes. The downloader uses HTTP
timeouts, retries, exponential backoff and metadata caching.

Verify the boundary file:

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("data/regions/tashkent/fields.geojson")
data = json.loads(path.read_text())
print("Fields:", len(data.get("features", [])))
print("Size MB:", round(path.stat().st_size / 1024 / 1024, 1))
PY
```

### 7.2 Register full coverage and create a reference client

Load `.env`, then run:

```bash
set -a
source .env
set +a

python scripts/09_build_region.py \
  --fields data/regions/tashkent/fields.geojson \
  --region-id tashkent \
  --client tashkent \
  --label "Tashkent Region" \
  --start 2023-01-01 \
  --reference-size 1000 \
  --chunk-size 100 \
  --fetch
```

This command:

1. Registers every boundary for location matching.
2. Selects 1,000 spatially distributed reference fields.
3. Fetches Sentinel-2 observations.
4. Calculates NDVI, NDRE, NDMI, EVI and SAVI.
5. Trains the regional Isolation Forest model.

The first historical fetch creates up to 40 Copernicus jobs. Cached job results
under `clients/tashkent/_cache/` make reruns resumable.

Do not change `--chunk-size` while reusing an old cache. Current cache filenames
include the five-index schema and chunk size to prevent alignment collisions.

### 7.3 Verify the observations

```bash
python - <<'PY'
import pandas as pd

frame = pd.read_csv("clients/tashkent/observations.csv")
indices = ["ndvi", "ndre", "ndmi", "evi", "savi"]

print("Columns:", list(frame.columns))
print(frame[indices].notna().sum())
print("Rows:", len(frame))
print("Fields:", frame["field_id"].nunique())
print("Dates:", frame["date"].min(), "to", frame["date"].max())
PY
```

All five index columns must exist and have nonzero counts.

### 7.4 Train, score and publish the regional report

```bash
python scripts/06_train_ml.py --client tashkent
python scripts/03_anomalies.py --client tashkent
python scripts/04_report.py --client tashkent --language en
```

Expected model metadata includes:

```text
model_version: 2.0
algorithm: IsolationForest
features: 28
```

## 8. Import an existing Khorezm dataset

The importer expects `field_timeseries.csv` and `sampled_fields.geojson` in the
legacy source directory.

```bash
python scripts/00_import_legacy_region.py \
  --source-dir ~/Downloads/UZ-Agri-Copernicus/data/regions/khorezm \
  --client khorezm \
  --label "Khorezm"

python scripts/01_setup_check.py --client khorezm
python scripts/06_train_ml.py --client khorezm
python scripts/03_anomalies.py --client khorezm
python scripts/04_report.py --client khorezm --language en
```

Prepare its full field registry for Telegram:

```bash
python scripts/08_prepare_telegram.py \
  --source-dir ~/Downloads/UZ-Agri-Copernicus/data/regions/khorezm \
  --client khorezm \
  --region-id khorezm \
  --label "Khorezm"
```

Legacy NDVI-only data can be scored because missing optional features are
imputed neutrally. To obtain real five-index values, run a full Sentinel refresh.

## 9. Incremental updates

For Tashkent, the initial historical download should not be repeated. Fetch only
new dates:

```bash
set -a
source .env
set +a

python scripts/02_fetch_indices.py \
  --client tashkent \
  --chunk-size 100 \
  --latency-days 2 \
  --headless
```

Then update results:

```bash
python scripts/03_anomalies.py --client tashkent
python scripts/04_report.py --client tashkent --language en
```

Retrain weekly or after a substantial amount of new data:

```bash
python scripts/06_train_ml.py --client tashkent
```

The complete weekly orchestrator is also available:

```bash
python scripts/05_run_weekly.py --client tashkent --headless
```

Check its help before automation:

```bash
python scripts/05_run_weekly.py --help
```

## 10. Cache behavior and performance

There are several independent caches:

| Cache | Location | Purpose |
|---|---|---|
| Regional observations | `clients/<client>/observations.csv` | Five-index history for reference fields |
| openEO job cache | `clients/<client>/_cache/` | Resume regional batch jobs |
| On-demand field cache | `bot_data/field_cache/` | History for fields outside the reference set |
| Satellite preview cache | `bot_data/satellite_cache/` | RGB images and preview bounds |
| Air-quality cache | `bot_data/air_quality_cache/` | Reuse coarse regional CAMS values |
| Generated reports | `bot_data/reports/` | Per-request PNG and PDF files |

For Tashkent, all 68,005 boundaries are cached for immediate location matching,
but only the 1,000 reference histories are precomputed. A first request for one
of the other fields creates an on-demand Copernicus job and may take several
minutes. Later requests use the field cache.

Do not attempt to precompute all 68,005 histories without a quota and cost
assessment. It would require thousands of processing jobs.

Cache a small preview set:

```bash
python scripts/10_cache_satellite_previews.py \
  --region tashkent \
  --limit 20
```

Expand cautiously:

```bash
python scripts/10_cache_satellite_previews.py \
  --region tashkent \
  --workers 4
```

For a conference demonstration, warm likely test locations through the bot in
advance and use `FIELD_CACHE_DAYS=3` or `7`.

## 11. Observation freshness

Sentinel-2's nominal revisit interval does not guarantee a clear field
observation every five days. A measurement is accepted only when:

- Sentinel-2 L2A is available;
- the field has an acquisition in the requested interval;
- the Scene Classification Layer marks vegetation or bare soil;
- at least 30% of the field has valid clear pixels.

The report date is the latest accepted clear observation, not simply the most
recent satellite overpass. Persistent cloud can make the accepted date older.

Recommended demo configuration:

```env
SENTINEL_LATENCY_DAYS=2
FIELD_CACHE_DAYS=3
```

Use a larger latency in production if the backend frequently lacks very recent
L2A products.

## 12. Air quality

The system requests these variables from Open-Meteo's CAMS global domain:

- AQI using the European scale;
- PM2.5 and PM10;
- CO, NO2, SO2 and O3;
- dust and aerosol optical depth.

“AQI (European scale)” describes the scoring convention, not the geographic
origin of the air. The query coordinates are the user's field coordinates.

CAMS global air quality is approximately 45 km resolution. It is regional
context, not a field-level measurement, and it is not used by the vegetation
anomaly model. Air-quality failure does not prevent the Sentinel report.

## 13. PDF report structure

The generated PDF has three pages:

1. Confirmed satellite field image and current values.
2. Five-index history, ML contributors, air-quality context and limitations.
3. Static interpretation appendix with approximate index ranges.

The appendix is educational guidance. Absolute index values depend on crop,
growth stage, soil background and image quality. Persistent change, same-crop
cohort position and agreement between several indices are stronger evidence
than a single universal threshold.

## 14. Dashboards

Regional monitoring dashboard:

```bash
streamlit run app.py
```

Bot administration dashboard:

```bash
streamlit run bot_admin.py --server.port 8502
```

Protect the administration dashboard before exposing it to a network.

## 15. Development checks

Compile all Python files:

```bash
python -m compileall -q app.py bot_admin.py run_bot.py scripts telegram_bot
```

Create a synthetic client without Copernicus quota:

```bash
python scripts/99_make_demo_client.py \
  --client demo \
  --n-fields 20 \
  --as-of 2026-07-15

python scripts/06_train_ml.py --client demo --holdout-days 15
python scripts/03_anomalies.py --client demo --as-of 2026-07-15
python scripts/04_report.py --client demo --language en
```

Inspect output files under:

```text
clients/demo/
clients/demo/reports/
```

## 16. Common errors

### Headless authentication variables are missing

Symptom:

```text
Headless auth requires CDSE_CLIENT_ID and CDSE_CLIENT_SECRET
```

Fix in the same terminal:

```bash
set -a
source .env
set +a
```

### `evi` or `savi` is missing

The data was produced by the older three-index code or an old cache. Confirm:

```bash
grep -n '"evi"\|"savi"' scripts/config.py
grep -n "FETCH_SCHEMA_VERSION" scripts/02_fetch_indices.py
```

Preserve the old cache, create a clean cache and run `--full-refresh` once.

### Geometry count mismatch

This usually means a cached payload was created with a different chunk size.
Do not truncate results or guess the field alignment. Preserve the cache under a
different name and rerun with a clean `_cache` directory.

### Model feature schema differs

The code uses model schema 2.0. Retrain:

```bash
python scripts/06_train_ml.py --client REGION
```

### A Telegram location takes several minutes

The field is probably outside the regional reference sample. Its first request
downloads and caches an on-demand history. This is expected.

### The latest observation is older than expected

The latest acquisitions may be cloudy or below the 30% valid-pixel threshold.
Reduce `SENTINEL_LATENCY_DAYS` cautiously and run an incremental update, but do
not represent a cloudy overpass as a valid observation.

### Virtual environment still uses another directory

If `which python` points to an old path, recreate `.venv` in the active project.

### Pandas `FutureWarning` or NumPy `DeprecationWarning`

Warnings observed during a successful run do not invalidate saved results. They
should be addressed during dependency maintenance, but are not data-processing
failures.

## 17. Production checklist

- Rotate any credential that has ever been exposed.
- Restrict `.env` permissions to the service account.
- Run under a process manager such as systemd or a container supervisor.
- Restart the bot after changing `regions.json`.
- Back up `clients/`, `data/`, `bot_data/regions.json` and SQLite.
- Monitor Copernicus job quota and failures.
- Limit preview concurrency.
- Protect the admin dashboard.
- Add log rotation and disk-usage monitoring for reports and caches.
- Record independent field-verification outcomes.
- Keep all output claims within screening scope.

## 18. Safe backup before code upgrades

The data directories are more valuable than the source archive. Before an
upgrade:

```bash
mkdir -p ~/Downloads/agri_backup

cp -a .env clients data bot_data \
  ~/Downloads/agri_backup/
```

Code-only upgrades should replace only the changed source files. Do not replace
or delete `clients/`, `data/`, `bot_data/` or `.env`.
