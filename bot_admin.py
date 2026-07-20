"""Private operational dashboard for Telegram requests."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from telegram_bot.settings import Settings
from telegram_bot.storage import Store

st.set_page_config(page_title="Field service operations", layout="wide")
st.title("Field service operations")
settings = Settings.load(require_token=False)
settings.ensure_directories()
rows = Store(settings.database_path).recent(1000)
if not rows:
    st.info("No Telegram requests yet.")
    st.stop()

frame = pd.DataFrame(rows)
completed = frame[frame["status"] == "complete"]
failed = frame[frame["status"] == "failed"]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Requests", len(frame))
c2.metric("Completed", len(completed))
c3.metric("Processing", int(frame["status"].isin(["queued", "processing"]).sum()))
c4.metric("Failed", len(failed))

statuses = sorted(frame["status"].dropna().unique())
selected = st.multiselect("Status", statuses, default=statuses)
view = frame[frame["status"].isin(selected)].copy()
columns = ["created_at", "request_id", "region_id", "field_id", "crop", "status", "latest_date",
           "latest_ndvi", "anomaly_percentile", "confidence", "data_source", "error"]
st.dataframe(view[[name for name in columns if name in view]], use_container_width=True, hide_index=True)

if len(completed):
    st.subheader("Completed request indicators")
    chart = completed.copy()
    chart["created_at"] = pd.to_datetime(chart["created_at"], errors="coerce")
    chart["anomaly_percentile"] = pd.to_numeric(chart["anomaly_percentile"], errors="coerce")
    st.bar_chart(chart.set_index("created_at")["anomaly_percentile"])
