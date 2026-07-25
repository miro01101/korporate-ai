import os
from datetime import datetime
from typing import Any

import httpx
import streamlit as st


APP_NAME = os.getenv(
    "APP_NAME",
    "Korporate AI Logistics Platform",
)
APP_VERSION = os.getenv("APP_VERSION", "0.3.0")
APP_ENV = os.getenv("APP_ENV", "production")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")


st.set_page_config(
    page_title="Korporate AI Logistics",
    page_icon="📦",
    layout="wide",
)


@st.cache_data(ttl=60)
def api_get(
    path: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = httpx.get(
        f"{API_BASE_URL}{path}",
        params=parameters,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def money(value: Any) -> str:
    if value is None:
        return "—"
    return (
        f"{float(value):,.2f} €"
        .replace(",", " ")
    )


def number(value: Any, decimals: int = 0) -> str:
    if value is None:
        return "—"
    return (
        f"{float(value):,.{decimals}f}"
        .replace(",", " ")
    )


def percent(value: Any, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{decimals}f} %"


def delta_percent(value: Any) -> str | None:
    if value is None:
        return None
    return f"{float(value):+.1f} %"


def month_label(value: str) -> str:
    year, month, _ = value.split("-")
    return f"{month}/{year}"


st.title(APP_NAME)
st.caption(
    f"Analytics Mart {APP_VERSION} · prostredie: {APP_ENV}"
)

try:
    health = api_get("/health/ready")
    analytics_status = api_get("/api/v1/analytics/status")
    monthly_payload = api_get("/api/v1/analytics/monthly")
    api_ok = True
except Exception as exc:
    st.error(
        "Analytické API momentálne nie je dostupné. "
        f"Typ chyby: {type(exc).__name__}"
    )
    st.stop()


monthly_all = monthly_payload.get("items", [])
sales_months = [
    row["month_start"]
    for row in monthly_all
    if row.get("revenue") is not None
]

if not sales_months:
    st.warning("Mart vrstva neobsahuje žiadne mesiace s predajom.")
    st.stop()


with st.sidebar:
    st.header("Filtre")

    start_month = st.selectbox(
        "Od mesiaca",
        options=sales_months,
        index=0,
        format_func=month_label,
    )

    valid_end_months = [
        month
        for month in sales_months
        if month >= start_month
    ]

    end_month = st.selectbox(
        "Do mesiaca",
        options=valid_end_months,
        index=len(valid_end_months) - 1,
        format_func=month_label,
    )

    top_limit = st.slider(
        "Počet top produktov",
        min_value=5,
        max_value=30,
        value=10,
        step=5,
    )

    st.divider()
    if st.button("Obnoviť dáta"):
        st.cache_data.clear()
        st.rerun()


date_parameters = {
    "date_from": start_month,
    "date_to": end_month,
}

summary = api_get(
    "/api/v1/analytics/summary",
    {"month": end_month},
)

monthly = api_get(
    "/api/v1/analytics/monthly",
    date_parameters,
).get("items", [])

products_payload = api_get(
    "/api/v1/analytics/sales/products",
    {
        **date_parameters,
        "limit": top_limit,
        "sort_by": "revenue",
        "direction": "desc",
    },
)

inventory = api_get(
    "/api/v1/analytics/inventory",
    date_parameters,
).get("items", [])

suppliers = api_get(
    "/api/v1/analytics/procurement/suppliers",
    {
        **date_parameters,
        "limit": 100,
    },
).get("items", [])

expeditions = api_get(
    "/api/v1/analytics/expeditions",
    date_parameters,
).get("items", [])

vehicles = api_get(
    "/api/v1/analytics/vehicles",
    date_parameters,
).get("items", [])


current = summary["current"]
mom = summary["month_over_month_pct"]
yoy = summary["year_over_year_pct"]


st.subheader(
    f"Manažérsky prehľad – {month_label(end_month)}"
)

metric_columns = st.columns(5)

metric_columns[0].metric(
    "Tržby",
    money(current.get("revenue")),
    delta=delta_percent(mom.get("revenue")),
)

metric_columns[1].metric(
    "Hrubý zisk",
    money(current.get("gross_profit")),
    delta=delta_percent(mom.get("gross_profit")),
)

metric_columns[2].metric(
    "Hrubá marža",
    percent(current.get("gross_margin_pct")),
)

metric_columns[3].metric(
    "Objednávky",
    number(current.get("sales_order_count")),
    delta=delta_percent(mom.get("sales_order_count")),
)

metric_columns[4].metric(
    "Zákazníci",
    number(current.get("customer_count")),
    delta=delta_percent(yoy.get("customer_count")),
)


tabs = st.tabs(
    [
        "Prehľad",
        "Predaj",
        "Sklad",
        "Nákup",
        "Logistika",
        "Technický stav",
    ]
)


with tabs[0]:
    left, right = st.columns(2)

    with left:
        st.subheader("Tržby a hrubý zisk")
        st.line_chart(
            monthly,
            x="month_start",
            y=["revenue", "gross_profit"],
        )

    with right:
        st.subheader("Objednávky a zákazníci")
        st.line_chart(
            monthly,
            x="month_start",
            y=["sales_order_count", "customer_count"],
        )

    overview_columns = st.columns(4)

    overview_columns[0].metric(
        "Hodnota zásob",
        money(current.get("inventory_cost_value")),
    )
    overview_columns[1].metric(
        "Nákupný fill rate",
        percent(current.get("procurement_fill_rate_pct")),
    )
    overview_columns[2].metric(
        "Priemerné vychystanie",
        (
            f"{number(current.get('average_picking_hours'), 2)} h"
            if current.get("average_picking_hours") is not None
            else "—"
        ),
    )
    overview_columns[3].metric(
        "Expedície",
        number(current.get("expedition_count")),
    )

    st.subheader("Mesačný manažérsky dataset")
    st.dataframe(
        monthly,
        use_container_width=True,
        hide_index=True,
    )


with tabs[1]:
    st.subheader("Top produkty podľa tržieb")

    products = products_payload.get("items", [])
    st.bar_chart(
        products,
        x="product_name",
        y="revenue",
    )

    st.dataframe(
        products,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "product_id",
            "product_name",
            "category",
            "supplier_name",
            "units_sold",
            "revenue",
            "gross_profit",
            "gross_margin_pct",
        ],
    )

    st.caption(
        "Hrubá marža používa poslednú známu nákupnú cenu "
        "k dátumu predaja; pri chýbajúcej histórii sa použije "
        "nákupná cena z kmeňa produktu."
    )


with tabs[2]:
    latest_inventory = (
        inventory[-1] if inventory else {}
    )

    inventory_columns = st.columns(5)
    inventory_columns[0].metric(
        "Hodnota zásob",
        money(latest_inventory.get("inventory_cost_value")),
    )
    inventory_columns[1].metric(
        "Stockout produkty",
        number(latest_inventory.get("stockout_products")),
    )
    inventory_columns[2].metric(
        "Pod minimom",
        number(latest_inventory.get("below_min_products")),
    )
    inventory_columns[3].metric(
        "Nad maximom",
        number(latest_inventory.get("above_max_products")),
    )
    inventory_columns[4].metric(
        "Priemerné days of cover",
        number(latest_inventory.get("average_days_of_cover"), 1),
    )

    st.subheader("Vývoj skladových rizík")
    st.line_chart(
        inventory,
        x="month_start",
        y=[
            "stockout_products",
            "below_min_products",
            "above_max_products",
        ],
    )

    st.subheader("Hodnota zásob")
    st.line_chart(
        inventory,
        x="month_start",
        y=[
            "inventory_cost_value",
            "inventory_sales_value",
        ],
    )

    st.dataframe(
        inventory,
        use_container_width=True,
        hide_index=True,
    )


with tabs[3]:
    st.subheader("Výkonnosť dodávateľov")

    st.bar_chart(
        suppliers,
        x="supplier_name",
        y="delivered_value",
    )

    st.dataframe(
        suppliers,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "supplier_name",
            "purchase_order_count",
            "ordered_units",
            "delivered_units",
            "undelivered_units",
            "delivered_value",
            "fill_rate_pct",
            "average_actual_lead_time_days",
            "average_standard_lead_time_days",
            "within_standard_lead_time_pct",
            "late_line_count",
        ],
    )

    st.caption(
        "Within standard lead time porovnáva skutočný čas "
        "dodania s lead_time_days v kmeni produktu."
    )


with tabs[4]:
    logistics_columns = st.columns(4)

    logistics_columns[0].metric(
        "Expedície",
        number(current.get("expedition_count")),
    )
    logistics_columns[1].metric(
        "Vlastná doprava",
        number(current.get("own_delivery_expeditions")),
    )
    logistics_columns[2].metric(
        "Externá doprava",
        number(current.get("external_delivery_expeditions")),
    )
    logistics_columns[3].metric(
        "Osobný odber",
        number(current.get("pickup_expeditions")),
    )

    st.subheader("Expedície podľa spôsobu doručenia")
    st.line_chart(
        expeditions,
        x="month_start",
        y=[
            "own_delivery_expeditions",
            "external_delivery_expeditions",
            "pickup_expeditions",
        ],
    )

    st.subheader("Využitie vozidiel")
    st.dataframe(
        vehicles,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "vehicle_id",
            "driver",
            "trip_count",
            "active_day_count",
            "transported_weight_kg",
            "transported_volume_m3",
            "average_weight_utilization_pct",
            "average_volume_utilization_pct",
            "maximum_weight_utilization_pct",
            "maximum_volume_utilization_pct",
            "overloaded_trips",
        ],
    )

    st.info(
        "Dopravné náklady zatiaľ nie sú vypočítané, "
        "pretože zdrojové dáta neobsahujú kilometre ani vzdialenosti."
    )


with tabs[5]:
    latest_refresh = analytics_status.get("latest_refresh") or {}

    status_columns = st.columns(4)
    status_columns[0].metric(
        "API",
        "ONLINE" if api_ok else "OFFLINE",
    )
    status_columns[1].metric(
        "PostgreSQL",
        (
            "ONLINE"
            if health.get("database", {}).get("status") == "ok"
            else "OFFLINE"
        ),
    )
    status_columns[2].metric(
        "Mart refresh",
        str(latest_refresh.get("status", "unknown")).upper(),
    )
    status_columns[3].metric(
        "Verzia",
        APP_VERSION,
    )

    st.subheader("Stav analytickej vrstvy")
    st.json(analytics_status)

    st.subheader("Stav API")
    st.json(health)


st.caption(
    "Dashboard načítaný: "
    f"{datetime.now().isoformat(timespec='seconds')}"
)
