import os
from datetime import datetime

import httpx
import streamlit as st


APP_NAME = os.getenv(
    "APP_NAME",
    "Korporate AI Logistics Platform",
)
APP_VERSION = os.getenv("APP_VERSION", "0.2.0")
APP_ENV = os.getenv("APP_ENV", "production")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")


st.set_page_config(
    page_title="Korporate AI Logistics",
    page_icon="📦",
    layout="wide",
)

st.title("Korporate AI Logistics Platform")
st.caption(
    f"XLSX Import MVP {APP_VERSION} · prostredie: {APP_ENV}"
)

try:
    response = httpx.get(
        f"{API_BASE_URL}/health/ready",
        timeout=5.0,
    )
    response.raise_for_status()
    health = response.json()
    api_ok = True
except Exception as exc:
    health = {
        "status": "unavailable",
        "error_type": type(exc).__name__,
    }
    api_ok = False


col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Aplikácia",
    "ONLINE",
)

col2.metric(
    "API",
    "ONLINE" if api_ok else "OFFLINE",
)

database_status = (
    health.get("database", {}).get("status", "unknown")
    if isinstance(health, dict)
    else "unknown"
)

col3.metric(
    "PostgreSQL",
    "ONLINE" if database_status == "ok" else "OFFLINE",
)

col4.metric(
    "Verzia",
    APP_VERSION,
)

st.divider()

if api_ok:
    st.success(
        "Platform skeleton je funkčný a databáza je dostupná."
    )
else:
    st.error(
        "API alebo databáza nie sú pripravené."
    )

st.subheader("Technický stav")

st.json(health)

st.subheader("Najbližší modul")

st.info(
    "Nasleduje import XLSX: raw → staging → core "
    "a prvý Data Quality Report."
)

st.caption(
    f"Dashboard načítaný: {datetime.now().isoformat(timespec='seconds')}"
)
