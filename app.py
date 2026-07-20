"""Client canopy-monitoring dashboard

    streamlit run app.py
"""
from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from common import (  # noqa: E402
    CLIENTS_DIR,
    copernicus_browser_url,
    load_json,
    polygon_area_ha,
    representative_point,
)
from config import CROP_CALENDARS, INDICES, SCOPE_STATEMENT, SEVERITY_ORDER  # noqa: E402

st.set_page_config(page_title="Canopy monitoring", layout="wide")

T = {
    "language": {"uz": "Til", "ru": "Язык", "en": "Language"},
    "client": {"uz": "Mijoz", "ru": "Клиент", "en": "Client"},
    "actions": {"uz": "Tekshirish kerak", "ru": "К осмотру", "en": "To inspect"},
    "fields": {"uz": "Dalalar", "ru": "Участки", "en": "Parcels"},
    "area": {"uz": "Maydon", "ru": "Площадь", "en": "Area"},
    "observed": {"uz": "Shu hafta kuzatilgan", "ru": "Наблюдений", "en": "Observed this week"},
    "as_of": {"uz": "Sana", "ru": "Дата", "en": "As of"},
    "detail": {"uz": "Dala tafsiloti", "ru": "Детали участка", "en": "Parcel detail"},
    "select": {"uz": "Dalani tanlang", "ru": "Выберите участок", "en": "Select a parcel"},
    "history": {"uz": "Indekslar tarixi", "ru": "История индексов", "en": "Index history"},
    "none": {
        "uz": "Shu hafta belgilangan dala yo'q.",
        "ru": "На этой неделе отмеченных участков нет.",
        "en": "No parcels flagged this week.",
    },
    "stale": {
        "uz": "Oxirgi muvaffaqiyatli hisobot",
        "ru": "Последний успешный отчёт",
        "en": "Last successful report",
    },
    "download": {"uz": "Hisobotni yuklab olish", "ru": "Скачать отчёт", "en": "Download report"},
    "scope": {"uz": "Qamrov va cheklovlar", "ru": "Область и ограничения", "en": "Scope and limits"},
    "imagery": {"uz": "Copernicus tasvirini ochish", "ru": "Открыть снимок Copernicus", "en": "Open Copernicus imagery"},
    "rank": {
        "uz": "guruhda pastdan {p}%",
        "ru": "{p}% снизу в группе",
        "en": "{p}th pct of cohort",
    },
    "no_rank": {
        "uz": "guruh taqqoslashsiz",
        "ru": "без сравнения",
        "en": "no cohort comparison",
    },
    "unchecked": {
        "uz": "dala tekshirilmadi",
        "ru": "участков не проверено",
        "en": "parcels not checked",
    },
    "unchecked_why": {
        "uz": "Bu dalalar uchun qoida ishlamadi — natija 'hammasi joyida' degani emas.",
        "ru": "Правила не применялись к этим участкам — это не означает «всё в порядке».",
        "en": "No rule could run on these parcels. That is not the same as an all-clear.",
    },
    "checked": {"uz": "Tekshirilgan", "ru": "Проверено", "en": "Checked"},
    "map": {"uz": "Dalalar xaritasi", "ru": "Карта участков", "en": "Field map"},
    "crop": {"uz": "Ekin", "ru": "Культура", "en": "Crop"},
    "parcel": {"uz": "Dala", "ru": "Участок", "en": "Parcel"},
    "all_parcels": {"uz": "Barcha dalalar", "ru": "Все участки", "en": "All parcels"},
    "state_flagged": {"uz": "Belgilangan", "ru": "Отмечен", "en": "Flagged"},
    "state_ok": {"uz": "Tekshirildi — normal", "ru": "Проверен — норма", "en": "Checked — normal"},
    "state_unchecked": {"uz": "Tekshirilmadi", "ru": "Не проверен", "en": "Not checked"},
    "season": {"uz": "Mavsum bosqichi", "ru": "Фаза сезона", "en": "Season stage"},
    "stage_pre": {"uz": "Ekishdan oldin", "ru": "До посева", "en": "Before emergence"},
    "stage_emerge": {"uz": "Unib chiqish", "ru": "Всходы", "en": "Emergence"},
    "stage_growth": {"uz": "O'sish", "ru": "Рост", "en": "Growth"},
    "stage_peak": {"uz": "Eng yuqori nuqta", "ru": "Пик", "en": "Peak"},
    "stage_senesce": {"uz": "Qurish / yig'im", "ru": "Созревание / уборка", "en": "Senescence / harvest"},
    "stage_post": {"uz": "Yig'imdan keyin", "ru": "После уборки", "en": "After harvest"},
    "no_obs": {
        "uz": "dalada shu hafta aniq kuzatuv bo'lmadi (bulut).",
        "ru": "участков без чистых наблюдений (облачность).",
        "en": "parcels had no clear observation this week (cloud).",
    },
    "ml_risk": {"uz": "ML noodatiylik xavfi", "ru": "ML-риск аномалии", "en": "ML anomaly risk"},
    "ml_active": {"uz": "ML modeli faol", "ru": "ML-модель активна", "en": "ML model active"},
    "ml_missing": {
        "uz": "ML modeli hali o'rgatilmagan; hozir faqat ekspert qoidalari ishlamoqda.",
        "ru": "ML-модель ещё не обучена; сейчас работают только экспертные правила.",
        "en": "ML model is not trained yet; only expert rules are currently active.",
    },
    "feedback": {"uz": "Joyidagi tekshiruv", "ru": "Полевая проверка", "en": "Field verification"},
    "feedback_label": {"uz": "Natija", "ru": "Результат", "en": "Outcome"},
    "feedback_note": {"uz": "Izoh", "ru": "Примечание", "en": "Notes"},
    "feedback_save": {"uz": "Tekshiruvni saqlash", "ru": "Сохранить проверку", "en": "Save verification"},
    "feedback_saved": {"uz": "Tekshiruv saqlandi.", "ru": "Проверка сохранена.", "en": "Verification saved."},
}
SEVERITY_COLOR = {"high": "#c53030", "medium": "#dd6b20", "low": "#718096"}


def t(key: str, language: str) -> str:
    return T[key].get(language, T[key]["en"])


def season_stage(crop: str, doy: float, language: str) -> str:
    """Where this parcel sits in its crop calendar — the context every number needs."""
    calendar = CROP_CALENDARS.get(crop, CROP_CALENDARS["other"])
    if doy < calendar["emergence"][0]:
        return t("stage_pre", language)
    if doy <= calendar["emergence"][1]:
        return t("stage_emerge", language)
    if doy < calendar["peak"][0]:
        return t("stage_growth", language)
    if doy <= calendar["peak"][1]:
        return t("stage_peak", language)
    if doy <= calendar["senescence"][1]:
        return t("stage_senesce", language)
    return t("stage_post", language)


def crop_label(crop: str, language: str) -> str:
    calendar = CROP_CALENDARS.get(crop, CROP_CALENDARS["other"])
    return calendar.get(f"label_{language}", calendar["label_en"])


def available_clients() -> dict[str, dict]:
    """A client is displayable when its outputs exist.

    last_run.json is written by the orchestrator only. Gating on it hid clients
    whose steps were run by hand, which is exactly what the demo flow does.
    The manifest is metadata about a run, not the run's output — it informs the
    staleness banner when present, and is never required.

    anomaly_summary.json is written atomically at the end of 03_anomalies.py, so
    its presence alongside field_state.csv already means "a run finished".
    """
    clients: dict[str, dict] = {}
    if not CLIENTS_DIR.exists():
        return clients
    for directory in sorted(path for path in CLIENTS_DIR.iterdir() if path.is_dir()):
        required = [
            directory / "field_state.csv",
            directory / "anomaly_summary.json",
            directory / "observations.csv",
            directory / "fields.geojson",
        ]
        if not all(path.exists() for path in required):
            continue
        manifest = load_json(directory / "last_run.json", default={})
        clients[directory.name] = {
            "dir": directory,
            "meta": load_json(directory / "client.json", default={}),
            "manifest": manifest,
        }
    return clients


@st.cache_data(show_spinner=False)
def load_client(directory_text: str, run_key: str):
    """run_key busts the cache when a new weekly run publishes. Do not remove."""
    directory = Path(directory_text)
    state = pd.read_csv(directory / "field_state.csv")
    summary = load_json(directory / "anomaly_summary.json")
    observations = pd.read_csv(directory / "observations.csv", parse_dates=["date"])
    fields = load_json(directory / "fields.geojson")
    try:
        anomalies = pd.read_csv(directory / "anomalies.csv")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        anomalies = pd.DataFrame()

    geometry = []
    for feature in fields.get("features", []):
        properties = feature["properties"]
        field_id = int(properties["field_id"])
        lat, lon = representative_point(feature["geometry"])
        geometry.append({
            "field_id": field_id,
            "name": properties.get("name", str(field_id)),
            "area_ha": float(properties.get("area_ha") or polygon_area_ha(feature["geometry"])),
            "lat": lat,
            "lon": lon,
        })
    return state, anomalies, summary, observations, pd.DataFrame(geometry), fields


clients = available_clients()
if not clients:
    st.error(
        "No client results found.\n\n"
        "Expected `field_state.csv` and `anomaly_summary.json` under `clients/<name>/`.\n\n"
        "Try the synthetic demo (no openEO quota needed):\n"
        "```\n"
        "python scripts/99_make_demo_client.py --client demo\n"
        "python scripts/03_anomalies.py --client demo\n"
        "python scripts/04_report.py --client demo\n"
        "```"
    )
    st.stop()

language = st.sidebar.selectbox("Til / Язык / Language", ["uz", "ru", "en"], index=0)
client_keys = list(clients)
default_client_index = client_keys.index("tashkent") if "tashkent" in client_keys else 0
client_key = st.sidebar.selectbox(
    t("client", language), client_keys, index=default_client_index,
    format_func=lambda key: clients[key]["meta"].get("label", key),
)
entry = clients[client_key]
# Cache key: manifest timestamp when the orchestrator ran, otherwise the mtime
# of the file 03_anomalies.py writes last. Either way a new run busts the cache.
run_key = entry["manifest"].get("completed_at") or str(
    (entry["dir"] / "anomaly_summary.json").stat().st_mtime
)
state, anomalies, summary, observations, geometry, fields = load_client(
    str(entry["dir"]), run_key
)

# Clients prepared before the five-index upgrade contain only NDVI/NDRE/NDMI.
# Keep those clients usable while showing EVI/SAVI automatically wherever the
# newer columns exist.  Never select or chart a configured index merely because
# it is present in config.py: the actual client files are the source of truth.
state_indices = [name for name in INDICES if name in state.columns]
history_indices = [name for name in INDICES if name in observations.columns]

st.title(entry["meta"].get("label", client_key))
st.caption("Sentinel-2 · Copernicus Data Space Ecosystem")
if summary.get("ml_status") == "active":
    st.caption(f"✓ {t('ml_active', language)} · {summary.get('ml_fields_scored', 0)} parcels scored")
else:
    st.warning(t("ml_missing", language))

# Staleness is a first-class fact, not a footnote: an old report that looks
# current is worse than no report.
if entry["manifest"].get("status") == "failed":
    st.error(
        f"The last scheduled run failed on {str(entry['manifest'].get('failed_at', ''))[:10]}: "
        f"{entry['manifest'].get('error', 'unknown error')}. "
        "The figures below are from the previous successful run."
    )

as_of = pd.Timestamp(summary["as_of"])
age_days = (pd.Timestamp.today().normalize() - as_of).days
if age_days > 14:
    st.warning(f"{t('stale', language)}: {summary['as_of']} ({age_days} days ago)")

observed = summary["fields_observed"]
total = summary["fields_total"]
columns = st.columns(4)
evaluated = summary.get("fields_evaluated", observed)
columns[0].metric(t("actions", language), str(summary["anomalies"]))
columns[1].metric(t("fields", language), f"{total}")
columns[2].metric(t("area", language), f"{geometry['area_ha'].sum():,.0f} ha")
columns[3].metric(t("checked", language), f"{evaluated}/{total}")
if observed < total:
    st.caption(f"{total - observed} {t('no_obs', language)}")

# A zero next to unchecked parcels is the most dangerous number this app can
# show: it reads as an all-clear. Say plainly that the rules did not run.
if evaluated < observed:
    unchecked = observed - evaluated
    reasons = summary.get("unevaluated_reasons") or {}
    detail = "; ".join(
        f"{reason.replace('_', ' ')}: {len(names)}" for reason, names in reasons.items()
    )
    st.warning(
        f"**{unchecked} {t('unchecked', language)}.** {t('unchecked_why', language)}"
        + (f"  \n`{detail}`" if detail else "")
    )

st.divider()

# --------------------------------------------------------------------------- #
# action list first — it is the only part anyone acts on
# --------------------------------------------------------------------------- #
st.subheader(t("actions", language))
if anomalies.empty and evaluated == 0:
    st.info(t("unchecked_why", language))
elif anomalies.empty:
    st.success(t("none", language))
else:
    merged = anomalies.merge(geometry[["field_id", "area_ha", "lat", "lon"]], on="field_id", how="left")
    order = sorted(
        merged["field_id"].unique(),
        key=lambda fid: (
            min(SEVERITY_ORDER.get(value, 3) for value in merged.loc[merged["field_id"] == fid, "severity"]),
            -len(merged[merged["field_id"] == fid]),
        ),
    )
    for field_id in order:
        block = merged[merged["field_id"] == field_id]
        row = block.iloc[0]
        severity = min(block["severity"], key=lambda value: SEVERITY_ORDER.get(value, 3))
        colour = SEVERITY_COLOR.get(severity, "#718096")
        with st.container(border=True):
            head, link = st.columns([4, 1])
            head.markdown(
                f"**{row['name']}** · {crop_label(row['crop'], language)} · {row['area_ha']:.1f} ha "
                f"<span style='color:{colour}'>●</span>",
                unsafe_allow_html=True,
            )
            link.link_button(
                t("imagery", language),
                copernicus_browser_url(row["lat"], row["lon"], row["date"]),
            )
            for detail in block.itertuples():
                text = getattr(detail, f"detail_{language}", None) or detail.detail_en
                st.markdown(f"- {text}")

    report = entry["dir"] / "reports" / "latest" / "report.md"
    if report.exists():
        st.download_button(
            t("download", language),
            report.read_bytes(),
            file_name=f"{client_key}_{summary['as_of']}.md",
            mime="text/markdown",
        )

st.divider()

# --------------------------------------------------------------------------- #
# whole-farm view: a monitoring product without a map is not a monitoring
# product. With zero anomalies the alert list is empty and this is all there is.
# --------------------------------------------------------------------------- #
EVALUATED = ("full", "cohort_only", "history_only", "ml_only")
flagged_ids = set(anomalies["field_id"]) if not anomalies.empty else set()


def parcel_state(row) -> str:
    if row["field_id"] in flagged_ids:
        return "flagged"
    return "ok" if row.get("coverage") in EVALUATED else "unchecked"


STATE_COLOR = {"flagged": "#c53030", "ok": "#2f855a", "unchecked": "#718096"}
overview = state.merge(geometry[["field_id", "area_ha", "lat", "lon"]], on="field_id", how="left")
overview["state"] = overview.apply(parcel_state, axis=1)
overview["doy"] = pd.to_datetime(overview["date"]).dt.dayofyear

st.subheader(t("map", language))
try:
    import folium
    from streamlit_folium import st_folium

    state_by_id = overview.set_index("field_id")["state"].to_dict()
    features = []
    for feature in fields.get("features", []):
        field_id = int(feature["properties"]["field_id"])
        if field_id not in state_by_id:
            continue
        item = json.loads(json.dumps(feature))
        row = overview[overview["field_id"] == field_id].iloc[0]
        item["properties"] = {
            "field_id": field_id,
            "name": row["name"] if "name" in row else str(field_id),
            "crop": crop_label(row["crop"], language),
            "ndvi": round(float(row["ndvi"]), 2) if pd.notna(row["ndvi"]) else "—",
            "area_ha": round(float(row["area_ha"]), 1),
            "state": t(f"state_{state_by_id[field_id]}", language),
        }
        features.append(item)

    if features:
        centre_lat = float(overview["lat"].mean())
        centre_lon = float(overview["lon"].mean())
        field_map = folium.Map(location=[centre_lat, centre_lon], zoom_start=13,
                               tiles=None, control_scale=True)
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri", name="Satellite",
        ).add_to(field_map)
        folium.GeoJson(
            {"type": "FeatureCollection", "features": features},
            style_function=lambda feature: {
                "fillColor": STATE_COLOR[state_by_id[int(feature["properties"]["field_id"])]],
                "color": STATE_COLOR[state_by_id[int(feature["properties"]["field_id"])]],
                "weight": 2,
                "fillOpacity": 0.45,
            },
            tooltip=folium.GeoJsonTooltip(fields=["name", "crop", "ndvi", "area_ha", "state"]),
        ).add_to(field_map)
        bounds = [[overview["lat"].min(), overview["lon"].min()],
                  [overview["lat"].max(), overview["lon"].max()]]
        field_map.fit_bounds(bounds)
        st_folium(field_map, height=460, use_container_width=True, returned_objects=[])
        legend = " · ".join(
            f"<span style='color:{STATE_COLOR[key]}'>●</span> {t(f'state_{key}', language)}"
            for key in ("flagged", "ok", "unchecked")
        )
        st.markdown(legend, unsafe_allow_html=True)
except ImportError:
    st.info("Map needs: pip install folium streamlit-folium")

st.subheader(t("all_parcels", language))
table = overview.copy()
table[t("season", language)] = [
    season_stage(row.crop, row.doy, language) for row in table.itertuples()
]
table["crop_label"] = table["crop"].map(lambda c: crop_label(c, language))
table["state_label"] = table["state"].map(lambda s: t(f"state_{s}", language))
display_columns = ["name", "crop_label", "area_ha", "date", *state_indices]
display_names = [t("parcel", language), t("crop", language), t("area", language),
                 t("as_of", language), *[name.upper() for name in state_indices]]
if "ml_risk" in table:
    table["ml_risk_pct"] = 100.0 * pd.to_numeric(table["ml_risk"], errors="coerce")
    display_columns.append("ml_risk_pct")
    display_names.append(t("ml_risk", language) + " [%]")
display_columns += [t("season", language), "state_label"]
display_names += [t("season", language), ""]
show = table[display_columns].round(2)
show.columns = display_names
st.dataframe(show, hide_index=True, use_container_width=True)

st.divider()

# --------------------------------------------------------------------------- #
# parcel detail
# --------------------------------------------------------------------------- #
st.subheader(t("detail", language))
names = geometry.set_index("field_id")["name"].to_dict()
field_ids = sorted(state["field_id"].unique())
default = int(anomalies.iloc[0]["field_id"]) if not anomalies.empty else field_ids[0]
field_id = st.selectbox(
    t("select", language), field_ids,
    index=field_ids.index(default) if default in field_ids else 0,
    format_func=lambda fid: names.get(fid, str(fid)),
)

row = state[state["field_id"] == field_id].iloc[0]
stage = season_stage(row["crop"], pd.Timestamp(row["date"]).dayofyear, language)
st.caption(f"{crop_label(row['crop'], language)} · {t('season', language)}: **{stage}**")
has_ml = "ml_risk" in state.columns and pd.notna(row.get("ml_risk"))
detail_columns = st.columns(len(state_indices) + 1 + int(has_ml))
detail_columns[0].metric(t("as_of", language), str(row["date"]))
for position, name in enumerate(state_indices, 1):
    spec = INDICES[name]
    value = row.get(name)
    percentile = row.get(f"{name}_pct")
    column = detail_columns[position]
    column.metric(
        spec.get(f"label_{language}", spec["label_en"]),
        f"{float(value):.2f}" if pd.notna(value) else "—",
    )
    # Never st.metric(delta=...) here. Streamlit renders any delta not starting
    # with "-" as a green up-arrow, so a parcel in the bottom 14% of its cohort
    # was shown with an up-arrow beside it — good news on the worst field.
    # Rank is stated in words, with direction and colour set by the rank itself.
    if pd.notna(percentile) and row.get("coverage") in ("full", "cohort_only"):
        rank = float(percentile) * 100
        if rank <= 25:
            colour, arrow = "#c53030", "▼"
        elif rank >= 75:
            colour, arrow = "#2f855a", "▲"
        else:
            colour, arrow = "#718096", "="
        column.markdown(
            f"<span style='color:{colour}'>{arrow} {t('rank', language).format(p=f'{rank:.0f}')}</span>",
            unsafe_allow_html=True,
        )
    else:
        column.caption(t("no_rank", language))

if has_ml:
    detail_columns[-1].metric(t("ml_risk", language), f"{100.0 * float(row['ml_risk']):.1f}%")
    detail_columns[-1].caption(str(row.get("ml_explanation") or ""))

history = observations[observations["field_id"] == field_id].copy()
if len(history):
    history["year"] = history["date"].dt.year.astype(str)
    history["doy"] = history["date"].dt.dayofyear
    st.markdown(f"**{t('history', language)}**")
    tabs = st.tabs([
        INDICES[name].get(f"label_{language}", INDICES[name]["label_en"])
        for name in history_indices
    ])
    for tab, name in zip(tabs, history_indices):
        with tab:
            try:
                import altair as alt

                chart = (
                    alt.Chart(history.dropna(subset=[name]))
                    .mark_line(point=alt.OverlayMarkDef(size=18))
                    .encode(
                        x=alt.X("doy:Q", title="Day of year", scale=alt.Scale(domain=[1, 365])),
                        y=alt.Y(f"{name}:Q", title=name.upper(), scale=alt.Scale(domain=[-1, 1])),
                        color=alt.Color("year:N", title=""),
                        tooltip=["date:T", alt.Tooltip(f"{name}:Q", format=".3f"),
                                 alt.Tooltip("valid_frac:Q", format=".0%", title="clear")],
                    )
                    .properties(height=280)
                )
                st.altair_chart(chart, use_container_width=True)
            except ImportError:
                st.line_chart(history.pivot_table(index="doy", columns="year", values=name))
            st.caption(INDICES[name]["means"])

# Human verification is the bridge from an unsupervised MVP to a locally
# validated supervised model. One current label per parcel/date is retained.
with st.expander(t("feedback", language)):
    outcomes = {
        "confirmed_problem": "Confirmed problem",
        "false_alarm": "False alarm",
        "cloud_or_image_issue": "Cloud/image issue",
        "crop_label_wrong": "Crop label incorrect",
        "not_inspected": "Not inspected",
    }
    with st.form(f"feedback_{field_id}"):
        outcome = st.selectbox(
            t("feedback_label", language), list(outcomes),
            format_func=lambda key: outcomes[key],
        )
        notes = st.text_input(t("feedback_note", language))
        submitted = st.form_submit_button(t("feedback_save", language))
    if submitted:
        feedback_path = entry["dir"] / "inspection_feedback.csv"
        columns = ["client", "field_id", "date", "outcome", "notes", "ml_risk",
                   "system_flagged", "recorded_at"]
        try:
            feedback = pd.read_csv(feedback_path)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            feedback = pd.DataFrame(columns=columns)
        record = pd.DataFrame([{
            "client": client_key,
            "field_id": int(field_id),
            "date": str(row["date"]),
            "outcome": outcome,
            "notes": notes,
            "ml_risk": float(row["ml_risk"]) if has_ml else None,
            "system_flagged": bool(field_id in flagged_ids),
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }])
        feedback = pd.concat([feedback, record], ignore_index=True)
        feedback = feedback.drop_duplicates(["client", "field_id", "date"], keep="last")
        temporary = feedback_path.with_suffix(".csv.part")
        feedback.to_csv(temporary, index=False)
        temporary.replace(feedback_path)
        st.success(t("feedback_saved", language))

with st.expander(t("scope", language)):
    st.write(SCOPE_STATEMENT.get(language, SCOPE_STATEMENT["en"]))
    st.json({
        "as_of": summary["as_of"],
        "window_days": summary["window_days"],
        "cohort_sizes": summary.get("cohort_sizes"),
        "crops_without_cohort": summary.get("crops_without_cohort"),
        "by_rule": summary.get("by_rule"),
        "ml_status": summary.get("ml_status"),
        "ml_fields_scored": summary.get("ml_fields_scored"),
        "ml_fields_flagged": summary.get("ml_fields_flagged"),
    })
