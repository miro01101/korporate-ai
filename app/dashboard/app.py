import os
from datetime import datetime
from typing import Any

import httpx
import streamlit as st

from ml_dashboard import render_ml_dashboard

import hmac
import json
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


APP_NAME = os.getenv(
    "APP_NAME",
    "Korporate AI Logistics Platform",
)
APP_VERSION = os.getenv("APP_VERSION", "0.5.0")
APP_ENV = os.getenv("APP_ENV", "production")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")


DASHBOARD_AUTH_FILE = Path(
    os.getenv(
        "DASHBOARD_AUTH_FILE",
        "/run/secrets/dashboard_auth.json",
    )
)

PASSWORD_HASHER = PasswordHasher()


def load_dashboard_auth_config() -> tuple[str, str]:
    try:
        payload = json.loads(
            DASHBOARD_AUTH_FILE.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Dashboard authentication configuration is unavailable."
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Dashboard authentication configuration is invalid."
        )

    email = str(payload.get("email", "")).strip().lower()
    password_hash = str(
        payload.get("password_hash", "")
    ).strip()

    if not email or not password_hash.startswith("$argon2"):
        raise RuntimeError(
            "Dashboard authentication configuration is invalid."
        )

    return email, password_hash


def verify_dashboard_credentials(
    submitted_email: str,
    submitted_password: str,
    expected_email: str,
    password_hash: str,
) -> bool:
    normalized_email = submitted_email.strip().lower()

    email_matches = hmac.compare_digest(
        normalized_email,
        expected_email,
    )

    try:
        password_matches = PASSWORD_HASHER.verify(
            password_hash,
            submitted_password,
        )
    except (VerificationError, InvalidHashError):
        password_matches = False

    return email_matches and password_matches


def require_dashboard_auth() -> None:
    if st.session_state.get(
        "dashboard_authenticated"
    ) is True:
        authenticated_email = st.session_state.get(
            "dashboard_email",
            "",
        )

        with st.sidebar:
            st.caption(
                f"Prihlásený používateľ: {authenticated_email}"
            )

            if st.button(
                "Odhlásiť",
                key="dashboard_logout",
                use_container_width=True,
            ):
                st.session_state.pop(
                    "dashboard_authenticated",
                    None,
                )
                st.session_state.pop(
                    "dashboard_email",
                    None,
                )
                st.rerun()

        return

    try:
        expected_email, password_hash = (
            load_dashboard_auth_config()
        )
    except RuntimeError:
        st.error(
            "Prihlasovanie nie je správne nakonfigurované."
        )
        st.stop()

    st.title("Prihlásenie")
    st.caption(APP_NAME)

    with st.form(
        "dashboard_login",
        clear_on_submit=True,
    ):
        submitted_email = st.text_input(
            "Email",
            key="dashboard_login_email",
        )

        submitted_password = st.text_input(
            "Heslo",
            type="password",
            key="dashboard_login_password",
        )

        submitted = st.form_submit_button(
            "Prihlásiť",
            use_container_width=True,
        )

    if submitted:
        if verify_dashboard_credentials(
            submitted_email,
            submitted_password,
            expected_email,
            password_hash,
        ):
            st.session_state[
                "dashboard_authenticated"
            ] = True

            st.session_state[
                "dashboard_email"
            ] = expected_email

            st.rerun()

        st.error("Nesprávny email alebo heslo.")

    st.stop()


st.set_page_config(
    page_title="Korporate AI – Manažérsky dashboard",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_dashboard_auth()

st.markdown(
    """
    <style>
    [data-testid="stToolbar"],
    [data-testid="stDecoration"] {
        display: none !important;
    }

    #MainMenu,
    footer {
        visibility: hidden !important;
    }

    .block-container {
        max-width: 1680px;
        padding-top: 2rem;
        padding-bottom: 2rem;
    }

    [data-testid="stMetric"] {
        border: 1px solid rgba(250, 250, 250, 0.08);
        border-radius: 0.75rem;
        padding: 0.8rem 1rem;
        background: rgba(255, 255, 255, 0.025);
    }

    [data-testid="stMetricLabel"] {
        min-height: 2.2rem;
    }

    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(250, 250, 250, 0.08);
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid rgba(250, 250, 250, 0.08);
        border-radius: 0.6rem;
        overflow: hidden;
    }

    .dashboard-note {
        color: rgba(250, 250, 250, 0.66);
        font-size: 0.86rem;
        margin-top: -0.25rem;
        margin-bottom: 1.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
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
    return f"{float(value):,.2f} €".replace(",", " ")


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


def translated_rows(
    rows: list[dict[str, Any]],
    mapping: dict[str, str],
    month_field: str | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for row in rows:
        translated: dict[str, Any] = {}

        for source, target in mapping.items():
            value = row.get(source)
            if source == month_field and value is not None:
                value = month_label(value)
            translated[target] = value

        output.append(translated)

    return output


def line_chart(
    rows: list[dict[str, Any]],
    series: dict[str, str],
    y_title: str,
    tooltip_format: str = ",.2f",
) -> None:
    values: list[dict[str, Any]] = []

    for row in rows:
        for field, label in series.items():
            value = row.get(field)
            if value is not None:
                values.append(
                    {
                        "month": row["month_start"],
                        "series": label,
                        "value": float(value),
                    }
                )

    if not values:
        st.info("Pre zvolené obdobie nie sú dostupné dáta.")
        return

    st.vega_lite_chart(
        {
            "data": {"values": values},
            "mark": {
                "type": "line",
                "strokeWidth": 2.2,
            },
            "height": 300,
            "encoding": {
                "x": {
                    "field": "month",
                    "type": "temporal",
                    "title": "Mesiac",
                    "axis": {
                        "format": "%m/%Y",
                        "labelAngle": -40,
                        "tickCount": 10,
                        "grid": False,
                    },
                },
                "y": {
                    "field": "value",
                    "type": "quantitative",
                    "title": y_title,
                    "scale": {"zero": True},
                },
                "color": {
                    "field": "series",
                    "type": "nominal",
                    "title": None,
                },
                "tooltip": [
                    {
                        "field": "month",
                        "type": "temporal",
                        "title": "Mesiac",
                        "format": "%m/%Y",
                    },
                    {
                        "field": "series",
                        "type": "nominal",
                        "title": "Ukazovateľ",
                    },
                    {
                        "field": "value",
                        "type": "quantitative",
                        "title": "Hodnota",
                        "format": tooltip_format,
                    },
                ],
            },
        },
        use_container_width=True,
    )


def horizontal_bar_chart(
    rows: list[dict[str, Any]],
    category_field: str,
    value_field: str,
    category_title: str,
    value_title: str,
    height: int,
) -> None:
    values = [
        {
            "category": row.get(category_field),
            "value": float(row[value_field]),
        }
        for row in rows
        if (
            row.get(category_field) is not None
            and row.get(value_field) is not None
        )
    ]

    if not values:
        st.info("Pre zvolené obdobie nie sú dostupné dáta.")
        return

    st.vega_lite_chart(
        {
            "data": {"values": values},
            "mark": {
                "type": "bar",
                "cornerRadiusEnd": 3,
            },
            "height": height,
            "encoding": {
                "y": {
                    "field": "category",
                    "type": "nominal",
                    "title": category_title,
                    "sort": "-x",
                    "axis": {"labelLimit": 340},
                },
                "x": {
                    "field": "value",
                    "type": "quantitative",
                    "title": value_title,
                    "scale": {"zero": True},
                },
                "tooltip": [
                    {
                        "field": "category",
                        "type": "nominal",
                        "title": category_title,
                    },
                    {
                        "field": "value",
                        "type": "quantitative",
                        "title": value_title,
                        "format": ",.2f",
                    },
                ],
            },
        },
        use_container_width=True,
    )


st.title(APP_NAME)
st.caption(
    f"Manažérsky dashboard {APP_VERSION} · "
    f"analytické a ML jadro 0.5.0 · prostredie: {APP_ENV}"
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
    st.warning(
        "Mart vrstva neobsahuje žiadne mesiace s predajom."
    )
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

    if st.button(
        "Obnoviť dáta",
        use_container_width=True,
    ):
        st.cache_data.clear()
        st.rerun()

    st.caption(
        "Dáta sa automaticky ukladajú do cache na 60 sekúnd."
    )


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
    "Tržby · vs. predchádzajúci mesiac",
    money(current.get("revenue")),
    delta=delta_percent(mom.get("revenue")),
)

metric_columns[1].metric(
    "Hrubý zisk · vs. predchádzajúci mesiac",
    money(current.get("gross_profit")),
    delta=delta_percent(mom.get("gross_profit")),
)

metric_columns[2].metric(
    "Hrubá marža",
    percent(current.get("gross_margin_pct")),
)

metric_columns[3].metric(
    "Objednávky · vs. predchádzajúci mesiac",
    number(current.get("sales_order_count")),
    delta=delta_percent(mom.get("sales_order_count")),
)

metric_columns[4].metric(
    "Zákazníci · vs. rovnaký mesiac minulého roka",
    number(current.get("customer_count")),
    delta=delta_percent(yoy.get("customer_count")),
)

st.markdown(
    '<div class="dashboard-note">'
    "Zelená alebo červená delta vyjadruje zmenu podľa porovnania "
    "uvedeného v názve KPI."
    "</div>",
    unsafe_allow_html=True,
)


tabs = st.tabs(
    [
        "Prehľad",
        "Predaj",
        "Sklad",
        "Nákup",
        "Logistika",
        "Technický stav",
        "ML jadro",
    ]
)


with tabs[0]:
    left, right = st.columns(2)

    with left:
        st.subheader("Tržby a hrubý zisk")
        line_chart(
            monthly,
            {
                "revenue": "Tržby",
                "gross_profit": "Hrubý zisk",
            },
            "EUR",
        )

    with right:
        st.subheader("Objednávky a zákazníci")
        line_chart(
            monthly,
            {
                "sales_order_count": "Objednávky",
                "customer_count": "Zákazníci",
            },
            "Počet",
            tooltip_format=",.0f",
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

    st.subheader("Mesačný manažérsky prehľad")

    management_mapping = {
        "month_start": "Mesiac",
        "revenue": "Tržby",
        "gross_profit": "Hrubý zisk",
        "gross_margin_pct": "Hrubá marža",
        "sales_order_count": "Objednávky",
        "customer_count": "Zákazníci",
        "inventory_cost_value": "Hodnota zásob",
        "stockout_products": "Stockout",
        "below_min_products": "Pod minimom",
        "above_max_products": "Nad maximom",
        "average_days_of_cover": "Days of cover",
        "procurement_fill_rate_pct": "Fill rate",
        "average_procurement_lead_time_days": "Lead time",
        "expedition_count": "Expedície",
    }

    st.dataframe(
        translated_rows(
            monthly,
            management_mapping,
            month_field="month_start",
        ),
        use_container_width=True,
        hide_index=True,
        height=430,
        column_config={
            "Mesiac": st.column_config.TextColumn(
                "Mesiac",
                pinned=True,
            ),
            "Tržby": st.column_config.NumberColumn(
                "Tržby",
                format="%,.2f €",
            ),
            "Hrubý zisk": st.column_config.NumberColumn(
                "Hrubý zisk",
                format="%,.2f €",
            ),
            "Hrubá marža": st.column_config.NumberColumn(
                "Hrubá marža",
                format="%.1f %%",
            ),
            "Hodnota zásob": st.column_config.NumberColumn(
                "Hodnota zásob",
                format="%,.2f €",
            ),
            "Fill rate": st.column_config.NumberColumn(
                "Fill rate",
                format="%.1f %%",
            ),
            "Lead time": st.column_config.NumberColumn(
                "Lead time",
                format="%.1f dňa",
            ),
        },
    )


with tabs[1]:
    st.subheader("Top produkty podľa tržieb")

    products = products_payload.get("items", [])

    horizontal_bar_chart(
        products,
        "product_name",
        "revenue",
        "Produkt",
        "Tržby (EUR)",
        height=max(300, top_limit * 28),
    )

    product_mapping = {
        "product_id": "ID produktu",
        "product_name": "Produkt",
        "category": "Kategória",
        "supplier_name": "Dodávateľ",
        "units_sold": "Predané množstvo",
        "revenue": "Tržby",
        "gross_profit": "Hrubý zisk",
        "gross_margin_pct": "Hrubá marža",
    }

    st.dataframe(
        translated_rows(products, product_mapping),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Produkt": st.column_config.TextColumn(
                "Produkt",
                width="large",
            ),
            "Dodávateľ": st.column_config.TextColumn(
                "Dodávateľ",
                width="large",
            ),
            "Tržby": st.column_config.NumberColumn(
                "Tržby",
                format="%,.2f €",
            ),
            "Hrubý zisk": st.column_config.NumberColumn(
                "Hrubý zisk",
                format="%,.2f €",
            ),
            "Hrubá marža": st.column_config.NumberColumn(
                "Hrubá marža",
                format="%.1f %%",
            ),
        },
    )

    st.caption(
        "Hrubá marža používa poslednú známu nákupnú cenu "
        "k dátumu predaja. Ak historická cena chýba, použije sa "
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
    line_chart(
        inventory,
        {
            "stockout_products": "Stockout",
            "below_min_products": "Pod minimom",
            "above_max_products": "Nad maximom",
        },
        "Počet produktov",
        tooltip_format=",.0f",
    )

    st.subheader("Hodnota zásob")
    line_chart(
        inventory,
        {
            "inventory_cost_value": "Nákupná hodnota",
            "inventory_sales_value": "Predajná hodnota",
        },
        "EUR",
    )

    inventory_mapping = {
        "month_start": "Mesiac",
        "inventory_cost_value": "Nákupná hodnota zásob",
        "inventory_sales_value": "Predajná hodnota zásob",
        "stockout_products": "Stockout",
        "below_min_products": "Pod minimom",
        "above_max_products": "Nad maximom",
        "healthy_products": "V zdravom pásme",
        "average_days_of_cover": "Days of cover",
        "days_cover_coverage_pct": "Pokrytie výpočtu",
    }

    st.dataframe(
        translated_rows(
            inventory,
            inventory_mapping,
            month_field="month_start",
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Mesiac": st.column_config.TextColumn(
                "Mesiac",
                pinned=True,
            ),
            "Nákupná hodnota zásob":
                st.column_config.NumberColumn(
                    "Nákupná hodnota zásob",
                    format="%,.2f €",
                ),
            "Predajná hodnota zásob":
                st.column_config.NumberColumn(
                    "Predajná hodnota zásob",
                    format="%,.2f €",
                ),
            "Pokrytie výpočtu":
                st.column_config.NumberColumn(
                    "Pokrytie výpočtu",
                    format="%.1f %%",
                ),
        },
    )


with tabs[3]:
    st.subheader("Výkonnosť dodávateľov")

    horizontal_bar_chart(
        suppliers,
        "supplier_name",
        "delivered_value",
        "Dodávateľ",
        "Dodaná hodnota (EUR)",
        height=max(300, len(suppliers) * 42),
    )

    supplier_mapping = {
        "supplier_name": "Dodávateľ",
        "purchase_order_count": "Objednávky",
        "ordered_units": "Objednané množstvo",
        "delivered_units": "Dodané množstvo",
        "undelivered_units": "Nedodané množstvo",
        "delivered_value": "Dodaná hodnota",
        "fill_rate_pct": "Fill rate",
        "average_actual_lead_time_days": "Skutočný lead time",
        "average_standard_lead_time_days":
            "Štandardný lead time",
        "within_standard_lead_time_pct":
            "V štandardnom lead time",
        "late_line_count": "Oneskorené položky",
    }

    st.dataframe(
        translated_rows(suppliers, supplier_mapping),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Dodávateľ": st.column_config.TextColumn(
                "Dodávateľ",
                pinned=True,
                width="large",
            ),
            "Dodaná hodnota": st.column_config.NumberColumn(
                "Dodaná hodnota",
                format="%,.2f €",
            ),
            "Fill rate": st.column_config.NumberColumn(
                "Fill rate",
                format="%.1f %%",
            ),
            "Skutočný lead time":
                st.column_config.NumberColumn(
                    "Skutočný lead time",
                    format="%.1f dňa",
                ),
            "Štandardný lead time":
                st.column_config.NumberColumn(
                    "Štandardný lead time",
                    format="%.1f dňa",
                ),
            "V štandardnom lead time":
                st.column_config.NumberColumn(
                    "V štandardnom lead time",
                    format="%.1f %%",
                ),
        },
    )

    st.caption(
        "Podiel v štandardnom lead time porovnáva skutočný čas "
        "dodania s hodnotou lead_time_days v kmeni produktu."
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
    line_chart(
        expeditions,
        {
            "own_delivery_expeditions": "Vlastná doprava",
            "external_delivery_expeditions": "Externá doprava",
            "pickup_expeditions": "Osobný odber",
        },
        "Počet expedícií",
        tooltip_format=",.0f",
    )

    vehicle_mapping = {
        "vehicle_id": "Vozidlo",
        "driver": "Vodič",
        "trip_count": "Jazdy",
        "active_day_count": "Aktívne dni",
        "transported_weight_kg": "Prepravená hmotnosť",
        "transported_volume_m3": "Prepravený objem",
        "average_weight_utilization_pct":
            "Priemerné využitie hmotnosti",
        "average_volume_utilization_pct":
            "Priemerné využitie objemu",
        "maximum_weight_utilization_pct":
            "Max. využitie hmotnosti",
        "maximum_volume_utilization_pct":
            "Max. využitie objemu",
        "overloaded_trips": "Preťažené jazdy",
    }

    st.subheader("Využitie vozidiel")
    st.dataframe(
        translated_rows(vehicles, vehicle_mapping),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Vozidlo": st.column_config.TextColumn(
                "Vozidlo",
                pinned=True,
            ),
            "Vodič": st.column_config.TextColumn(
                "Vodič",
                width="large",
            ),
            "Prepravená hmotnosť":
                st.column_config.NumberColumn(
                    "Prepravená hmotnosť",
                    format="%,.2f kg",
                ),
            "Prepravený objem":
                st.column_config.NumberColumn(
                    "Prepravený objem",
                    format="%,.3f m³",
                ),
            "Priemerné využitie hmotnosti":
                st.column_config.NumberColumn(
                    "Priemerné využitie hmotnosti",
                    format="%.1f %%",
                ),
            "Priemerné využitie objemu":
                st.column_config.NumberColumn(
                    "Priemerné využitie objemu",
                    format="%.1f %%",
                ),
        },
    )

    st.info(
        "Dopravné náklady zatiaľ nie sú vypočítané, "
        "pretože zdrojové dáta neobsahujú kilometre ani vzdialenosti."
    )


with tabs[5]:
    latest_refresh = analytics_status.get("latest_refresh") or {}
    table_counts = analytics_status.get("table_counts") or {}

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
        "Dashboard",
        APP_VERSION,
    )

    st.subheader("Počty analytických záznamov")
    st.dataframe(
        [
            {
                "Dataset": "Mesačný predaj",
                "Počet riadkov": table_counts.get("sales_monthly"),
            },
            {
                "Dataset": "Predaj produktov",
                "Počet riadkov": table_counts.get(
                    "product_sales_monthly"
                ),
            },
            {
                "Dataset": "Zdravie skladu",
                "Počet riadkov": table_counts.get(
                    "inventory_health_monthly"
                ),
            },
            {
                "Dataset": "Výkonnosť dodávateľov",
                "Počet riadkov": table_counts.get(
                    "procurement_supplier_monthly"
                ),
            },
            {
                "Dataset": "Expedície",
                "Počet riadkov": table_counts.get(
                    "expedition_monthly"
                ),
            },
            {
                "Dataset": "Využitie vozidiel",
                "Počet riadkov": table_counts.get(
                    "vehicle_utilization_monthly"
                ),
            },
            {
                "Dataset": "Manažérske KPI",
                "Počet riadkov": table_counts.get(
                    "management_kpis_monthly"
                ),
            },
        ],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Detail posledného mart refreshu"):
        st.json(latest_refresh)

    with st.expander("Detail API a databázy"):
        st.json(health)


with tabs[6]:
    render_ml_dashboard(api_get)


st.caption(
    "Dashboard načítaný: "
    f"{datetime.now().isoformat(timespec='seconds')}"
)
