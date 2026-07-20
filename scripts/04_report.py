"""Generate the weekly client report.

The report leads with the action list, because that is the only part anyone acts
on. Everything else is evidence for it. Every flagged parcel carries a Copernicus
Browser link so the client can look at the imagery themselves — which is both an
honesty mechanism and the thing that builds trust in the first month.

Outputs (under clients/<client>/reports/<date>/):
    report.md          the readable report
    actions.csv        ranked parcels to inspect
    field_state.csv    snapshot of every parcel this week
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import (  # noqa: E402
    CLIENTS_DIR,
    copernicus_browser_url,
    ensure_dir,
    load_json,
    polygon_area_ha,
    representative_point,
)
from config import CROP_CALENDARS, INDICES, MIN_COHORT, SCOPE_STATEMENT, SEVERITY_ORDER  # noqa: E402

TEXT = {
    "title": {
        "en": "Canopy monitoring report",
        "uz": "O'simlik qoplami monitoringi hisoboti",
        "ru": "Отчёт мониторинга растительного покрова",
    },
    "week_of": {"en": "Week of", "uz": "Hafta", "ru": "Неделя"},
    "actions": {
        "en": "Parcels to inspect this week",
        "uz": "Shu hafta tekshirish kerak bo'lgan dalalar",
        "ru": "Участки для осмотра на этой неделе",
    },
    "none": {
        "en": "No parcels were flagged this week. Every observed parcel is within the normal range for its crop and its own history.",
        "uz": "Shu hafta hech qanday dala belgilanmadi. Kuzatilgan barcha dalalar o'z ekini va o'z tarixi uchun normal oraliqda.",
        "ru": "На этой неделе участки не отмечены. Все наблюдаемые участки в норме для своей культуры и своей истории.",
    },
    "overview": {"en": "Overview", "uz": "Umumiy ko'rinish", "ru": "Обзор"},
    "coverage": {"en": "Observation coverage", "uz": "Kuzatuv qamrovi", "ru": "Покрытие наблюдений"},
    "by_crop": {"en": "By crop", "uz": "Ekin bo'yicha", "ru": "По культурам"},
    "scope": {"en": "Scope and limits", "uz": "Qamrov va cheklovlar", "ru": "Область применения и ограничения"},
    "field": {"en": "Parcel", "uz": "Dala", "ru": "Участок"},
    "crop": {"en": "Crop", "uz": "Ekin", "ru": "Культура"},
    "area": {"en": "Area", "uz": "Maydon", "ru": "Площадь"},
    "imagery": {"en": "Imagery", "uz": "Tasvir", "ru": "Снимок"},
    "view": {"en": "view", "uz": "ko'rish", "ru": "смотреть"},
    "observed": {"en": "Observed", "uz": "Kuzatilgan", "ru": "Наблюдение"},
    "ml_active": {"en": "ML model", "uz": "ML modeli", "ru": "ML-модель"},
    "not_observed": {
        "en": "parcels had no clear observation this week (cloud). They are unchanged from the previous report, not verified.",
        "uz": "dalada shu hafta aniq kuzatuv bo'lmadi (bulut). Ular oldingi hisobotdagidek qoladi, tasdiqlanmagan.",
        "ru": "участков не имели чистых наблюдений на этой неделе (облачность). Данные по ним не обновлены.",
    },
    "no_cohort": {
        "en": "Cohort comparison is suppressed for these crops (fewer than {n} parcels): {crops}. Only own-history rules applied.",
        "uz": "Bu ekinlar uchun guruh bilan taqqoslash o'chirilgan ({n} tadan kam dala): {crops}. Faqat o'z tarixi qoidalari qo'llanildi.",
        "ru": "Сравнение с группой отключено для этих культур (менее {n} участков): {crops}. Применены только правила по собственной истории.",
    },
}


def label(key: str, language: str) -> str:
    return TEXT[key].get(language, TEXT[key]["en"])


def crop_label(crop: str, language: str) -> str:
    calendar = CROP_CALENDARS.get(crop, CROP_CALENDARS["other"])
    return calendar.get(f"label_{language}", calendar["label_en"])


def build_actions(anomalies: pd.DataFrame, state: pd.DataFrame, geometry: dict) -> pd.DataFrame:
    if anomalies.empty:
        return pd.DataFrame()
    grouped = (
        anomalies.groupby(["field_id", "name", "crop", "date"])
        .agg(
            rules=("rule", lambda values: ", ".join(sorted(set(values)))),
            n_rules=("rule", "count"),
            severity=("severity", lambda values: min(values, key=lambda v: SEVERITY_ORDER.get(v, 3))),
        )
        .reset_index()
    )
    grouped["area_ha"] = grouped["field_id"].map(geometry["area"])
    grouped["lat"] = grouped["field_id"].map(geometry["lat"])
    grouped["lon"] = grouped["field_id"].map(geometry["lon"])
    grouped["imagery_url"] = [
        copernicus_browser_url(row.lat, row.lon, row.date) for row in grouped.itertuples()
    ]
    ndvi = state.set_index("field_id")["ndvi"].to_dict()
    grouped["ndvi"] = grouped["field_id"].map(ndvi)
    grouped["_rank"] = grouped["severity"].map(SEVERITY_ORDER).fillna(3)
    # Rank by severity, then by how many independent rules agree, then by area at risk.
    return (
        grouped.sort_values(["_rank", "n_rules", "area_ha"], ascending=[True, False, False])
        .drop(columns="_rank")
        .reset_index(drop=True)
    )


def render(client: str, meta: dict, state: pd.DataFrame, anomalies: pd.DataFrame,
           actions: pd.DataFrame, summary: dict, geometry: dict, language: str) -> str:
    as_of = summary["as_of"]
    lines: list[str] = []
    lines.append(f"# {label('title', language)} — {meta.get('label', client)}")
    lines.append("")
    lines.append(f"**{label('week_of', language)} {as_of}** · Sentinel-2 · Copernicus Data Space Ecosystem")
    lines.append("")

    # --- actions first ------------------------------------------------------
    lines.append(f"## {label('actions', language)}")
    lines.append("")
    if actions.empty:
        lines.append(label("none", language))
        lines.append("")
    else:
        header = (
            f"| # | {label('field', language)} | {label('crop', language)} | "
            f"{label('area', language)} | NDVI | {label('imagery', language)} |"
        )
        lines.append(header)
        lines.append("|---|---|---|---|---|---|")
        for position, row in enumerate(actions.itertuples(), 1):
            ndvi = f"{row.ndvi:.2f}" if pd.notna(row.ndvi) else "—"
            lines.append(
                f"| {position} | {row.name} | {crop_label(row.crop, language)} | "
                f"{row.area_ha:.1f} ha | {ndvi} | [{label('view', language)}]({row.imagery_url}) |"
            )
        lines.append("")
        for position, row in enumerate(actions.itertuples(), 1):
            lines.append(f"### {position}. {row.name} — {crop_label(row.crop, language)}, {row.area_ha:.1f} ha")
            lines.append("")
            details = anomalies[anomalies["field_id"] == row.field_id]
            for detail in details.itertuples():
                text = getattr(detail, f"detail_{language}", None) or detail.detail_en
                lines.append(f"- {text}")
            lines.append("")

    # --- overview -----------------------------------------------------------
    lines.append(f"## {label('overview', language)}")
    lines.append("")
    observed = summary["fields_observed"]
    total = summary["fields_total"]
    total_area = sum(geometry["area"].values())
    lines.append(f"- {label('coverage', language)}: **{observed}/{total}** ({observed / total * 100:.0f}%), "
                 f"{total_area:,.0f} ha")
    if observed < total:
        lines.append(f"- {total - observed} {label('not_observed', language)}")
    if summary.get("ml_status") == "active":
        lines.append(
            f"- {label('ml_active', language)}: **active**, "
            f"{summary.get('ml_fields_scored', 0)} scored, "
            f"{summary.get('ml_fields_flagged', 0)} ML-flagged"
        )
    lines.append("")

    if not state.empty:
        lines.append(f"### {label('by_crop', language)}")
        lines.append("")
        lines.append(f"| {label('crop', language)} | n | NDVI | NDRE | NDMI |")
        lines.append("|---|---|---|---|---|")
        for crop, block in state.groupby("crop"):
            values = [f"{block[name].median():.2f}" if block[name].notna().any() else "—" for name in INDICES]
            lines.append(f"| {crop_label(crop, language)} | {len(block)} | " + " | ".join(values) + " |")
        lines.append("")

    thin = summary.get("crops_without_cohort") or []
    if thin:
        lines.append("> " + label("no_cohort", language).format(
            n=MIN_COHORT, crops=", ".join(crop_label(crop, language) for crop in thin)))
        lines.append("")

    # --- scope --------------------------------------------------------------
    lines.append(f"## {label('scope', language)}")
    lines.append("")
    lines.append(SCOPE_STATEMENT.get(language, SCOPE_STATEMENT["en"]))
    lines.append("")
    lines.append(f"*{label('observed', language)}: {as_of} · window {summary['window_days']}d · "
                 f"generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC*")
    return "\n".join(lines)


def main(client: str, language: str | None) -> Path:
    directory = CLIENTS_DIR / client
    meta = load_json(directory / "client.json")
    language = language or meta.get("language", "uz")
    if language not in SCOPE_STATEMENT:
        raise SystemExit(f"Unsupported language {language!r} (have: {', '.join(SCOPE_STATEMENT)})")

    state = pd.read_csv(directory / "field_state.csv")
    anomalies = pd.read_csv(directory / "anomalies.csv") if (directory / "anomalies.csv").stat().st_size > 1 else pd.DataFrame()
    summary = load_json(directory / "anomaly_summary.json")
    fields = load_json(directory / "fields.geojson")

    geometry = {"area": {}, "lat": {}, "lon": {}}
    for feature in fields.get("features", []):
        field_id = int(feature["properties"]["field_id"])
        area = feature["properties"].get("area_ha") or polygon_area_ha(feature["geometry"])
        lat, lon = representative_point(feature["geometry"])
        geometry["area"][field_id] = float(area)
        geometry["lat"][field_id] = lat
        geometry["lon"][field_id] = lon

    actions = build_actions(anomalies, state, geometry) if not anomalies.empty else pd.DataFrame()

    report_dir = ensure_dir(directory / "reports" / summary["as_of"])
    text = render(client, meta, state, anomalies, actions, summary, geometry, language)
    (report_dir / "report.md").write_text(text, encoding="utf-8")
    if not actions.empty:
        actions.to_csv(report_dir / "actions.csv", index=False)
    shutil.copy2(directory / "field_state.csv", report_dir / "field_state.csv")

    # latest/ always points at the most recent report
    latest = directory / "reports" / "latest"
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(report_dir, latest)

    print(f"  -> {report_dir / 'report.md'}  ({len(actions)} parcels to inspect)")
    return report_dir / "report.md"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--language", default=None, choices=["uz", "ru", "en"])
    args = parser.parse_args()
    main(args.client, args.language)
