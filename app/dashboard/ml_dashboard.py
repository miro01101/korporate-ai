"""Streamlit presentation layer for read-only ML API data."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import streamlit as st


ApiGetter = Callable[
    [str, dict[str, Any] | None],
    dict[str, Any],
]

ML_ENDPOINTS = (
    "/api/v1/ml/status",
    "/api/v1/ml/model-runs",
    "/api/v1/ml/forecast",
    "/api/v1/ml/inventory-risk",
    "/api/v1/ml/recommendations",
    "/api/v1/ml/products/{product_id}",
)

RECOMMENDATION_LABELS = {
    "EXPEDITE": "Urgovať dodanie",
    "PURCHASE": "Objednať",
    "REDUCE/DEFER": "Znížiť alebo odložiť",
    "REVIEW": "Manuálne preveriť",
    "HOLD": "Bez zmeny",
}


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    if value is None:
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def probability_percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{100.0 * safe_float(value):.1f} %"


def recommendation_label(value: str) -> str:
    return RECOMMENDATION_LABELS.get(value, value)


def product_ids_from_payloads(
    *collections: list[dict[str, Any]],
) -> list[str]:
    values = {
        str(row["product_id"])
        for rows in collections
        for row in rows
        if row.get("product_id")
    }
    return sorted(values)


def build_forecast_chart_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for row in rows:
        month = row.get("forecast_month")

        for field, label in (
            ("forecast_p10", "P10"),
            ("forecast_p50", "P50"),
            ("forecast_p90", "P90"),
        ):
            value = row.get(field)

            if month is None or value is None:
                continue

            output.append(
                {
                    "month": month,
                    "quantile": label,
                    "value": safe_float(value),
                }
            )

    return output


def filter_risk_rows(
    rows: list[dict[str, Any]],
    minimum_probability: float,
    recommended_only: bool,
) -> list[dict[str, Any]]:
    output = [
        row
        for row in rows
        if (
            safe_float(
                row.get("stockout_probability_30d")
            ) >= minimum_probability
            and (
                not recommended_only
                or int(
                    row.get(
                        "recommended_order_quantity",
                        0,
                    )
                ) > 0
            )
        )
    ]

    return sorted(
        output,
        key=lambda row: (
            safe_float(
                row.get("stockout_probability_30d")
            ),
            int(
                row.get(
                    "recommended_order_quantity",
                    0,
                )
            ),
            str(row.get("product_id", "")),
        ),
        reverse=True,
    )


def filter_recommendations(
    rows: list[dict[str, Any]],
    selected_types: list[str],
    minimum_priority: int,
) -> list[dict[str, Any]]:
    allowed = set(selected_types)

    output = [
        row
        for row in rows
        if (
            row.get("recommendation_type") in allowed
            and int(row.get("priority", 0))
            >= minimum_priority
        )
    ]

    return sorted(
        output,
        key=lambda row: (
            int(row.get("priority", 0)),
            int(row.get("recommended_quantity") or 0),
            str(row.get("product_id", "")),
        ),
        reverse=True,
    )


def recommendation_type_summary(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = Counter(
        str(row.get("recommendation_type", "UNKNOWN"))
        for row in rows
    )

    order = (
        "EXPEDITE",
        "PURCHASE",
        "REDUCE/DEFER",
        "REVIEW",
        "HOLD",
    )

    return [
        {
            "type": value,
            "label": recommendation_label(value),
            "count": counts.get(value, 0),
        }
        for value in order
    ]


def action_quantity(
    rows: list[dict[str, Any]],
) -> int:
    return sum(
        int(row.get("recommended_quantity") or 0)
        for row in rows
        if row.get("recommendation_type")
        in {"EXPEDITE", "PURCHASE"}
    )


def render_forecast_chart(
    rows: list[dict[str, Any]],
) -> None:
    values = build_forecast_chart_rows(rows)

    if not values:
        st.info("Pre vybraný produkt nie je dostupná predikcia.")
        return

    st.vega_lite_chart(
        {
            "data": {"values": values},
            "mark": {
                "type": "line",
                "point": True,
                "strokeWidth": 2.2,
            },
            "height": 340,
            "encoding": {
                "x": {
                    "field": "month",
                    "type": "temporal",
                    "title": "Mesiac",
                    "axis": {
                        "format": "%m/%Y",
                        "grid": False,
                    },
                },
                "y": {
                    "field": "value",
                    "type": "quantitative",
                    "title": "Predikované množstvo",
                    "scale": {"zero": True},
                },
                "color": {
                    "field": "quantile",
                    "type": "nominal",
                    "title": "Kvantíl",
                    "sort": ["P10", "P50", "P90"],
                },
                "tooltip": [
                    {
                        "field": "month",
                        "type": "temporal",
                        "title": "Mesiac",
                        "format": "%m/%Y",
                    },
                    {
                        "field": "quantile",
                        "type": "nominal",
                        "title": "Kvantíl",
                    },
                    {
                        "field": "value",
                        "type": "quantitative",
                        "title": "Množstvo",
                        "format": ",.2f",
                    },
                ],
            },
        },
        use_container_width=True,
    )


def render_recommendation_chart(
    rows: list[dict[str, Any]],
) -> None:
    values = recommendation_type_summary(rows)

    st.vega_lite_chart(
        {
            "data": {"values": values},
            "mark": {
                "type": "bar",
                "cornerRadiusEnd": 3,
            },
            "height": 300,
            "encoding": {
                "y": {
                    "field": "label",
                    "type": "nominal",
                    "title": "Odporúčanie",
                    "sort": "-x",
                },
                "x": {
                    "field": "count",
                    "type": "quantitative",
                    "title": "Počet produktov",
                    "scale": {"zero": True},
                },
                "tooltip": [
                    {
                        "field": "label",
                        "type": "nominal",
                        "title": "Odporúčanie",
                    },
                    {
                        "field": "count",
                        "type": "quantitative",
                        "title": "Produkty",
                    },
                ],
            },
        },
        use_container_width=True,
    )


def recommendation_table_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "Produkt": row.get("product_id"),
            "Typ": recommendation_label(
                str(row.get("recommendation_type", ""))
            ),
            "Priorita": row.get("priority"),
            "Množstvo": row.get("recommended_quantity"),
            "Dátum": row.get("recommended_date"),
            "Confidence": 100.0 * safe_float(
                row.get("confidence")
            ),
            "Stav": row.get("status"),
            "Vysvetlenie": row.get("explanation"),
        }
        for row in rows
    ]


def risk_table_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "Produkt": row.get("product_id"),
            "Sklad": row.get("stock_available"),
            "Incoming": row.get("incoming_quantity"),
            "P30": 100.0 * safe_float(
                row.get("stockout_probability_30d")
            ),
            "P60": 100.0 * safe_float(
                row.get("stockout_probability_60d")
            ),
            "P90": 100.0 * safe_float(
                row.get("stockout_probability_90d")
            ),
            "Overstock 90": 100.0 * safe_float(
                row.get("overstock_probability_90d")
            ),
            "Reorder point": safe_float(
                row.get("reorder_point")
            ),
            "Odporúčané množstvo":
                row.get("recommended_order_quantity"),
            "Odporúčaný dátum":
                row.get("recommended_order_date"),
        }
        for row in rows
    ]


def render_ml_dashboard(
    api_get: ApiGetter,
) -> None:
    st.subheader("ML jadro")
    st.caption(
        "Predikcie, riziká zásob a poradné odporúčania. "
        "Dashboard nevykonáva objednávky ani nemení dáta."
    )

    try:
        status = api_get(
            "/api/v1/ml/status",
            None,
        )
        overview = api_get(
            "/api/v1/ml/overview",
            None,
        )
        model_runs = api_get(
            "/api/v1/ml/model-runs",
            {"limit": 100},
        ).get("items", [])
        forecasts = api_get(
            "/api/v1/ml/forecast",
            {"limit": 1000},
        ).get("items", [])
        risks = api_get(
            "/api/v1/ml/inventory-risk",
            {"limit": 500},
        ).get("items", [])
        recommendations = api_get(
            "/api/v1/ml/recommendations",
            {"limit": 500},
        ).get("items", [])
    except Exception as exc:
        st.error(
            "ML API momentálne nie je dostupné. "
            f"Typ chyby: {type(exc).__name__}"
        )
        return

    product_ids = product_ids_from_payloads(
        forecasts,
        risks,
        recommendations,
    )

    high_risk_count = sum(
        1
        for row in risks
        if safe_float(
            row.get("stockout_probability_30d")
        ) >= 0.50
    )

    metrics = st.columns(5)
    metrics[0].metric(
        "Model",
        str(status.get("model_family", "—")),
    )
    metrics[1].metric(
        "Forecast riadky",
        int(status.get("forecast_rows", 0)),
    )
    metrics[2].metric(
        "Riziko P30 ≥ 50 %",
        high_risk_count,
    )
    metrics[3].metric(
        "Pending odporúčania",
        int(
            status.get(
                "pending_recommendations",
                0,
            )
        ),
    )
    metrics[4].metric(
        "Navrhované množstvo",
        action_quantity(recommendations),
    )

    st.info(
        "Všetky odporúčania sú v stave pending a vyžadujú "
        "ľudské posúdenie. Finančné hodnoty nie sú dopočítané, "
        "kým nebude schválený cost model."
    )

    tabs = st.tabs(
        [
            "Prehľad modelu",
            "Forecast",
            "Skladové riziká",
            "Odporúčania",
            "Detail produktu",
        ]
    )

    with tabs[0]:
        quality = overview.get("model_quality", {})
        coverage = overview.get("coverage", {})
        selection = overview.get("selection_counts", {})
        dq = overview.get("data_quality", {})
        feature = overview.get("feature_run", {})
        lineage = overview.get("lineage", {})

        def pct(value: Any) -> str:
            if value is None:
                return "—"
            return f"{100.0 * safe_float(value):.1f} %"

        st.subheader("Prehľad modelu")

        cards = st.columns(4)
        cards[0].metric(
            "Baseline WAPE",
            pct(quality.get("baseline", {}).get("median_wape")),
            help="Nižšia hodnota je lepšia.",
        )
        cards[1].metric(
            "LightGBM WAPE",
            pct(quality.get("lightgbm", {}).get("median_wape")),
            help="Nižšia hodnota je lepšia.",
        )
        cards[2].metric(
            "Hybrid WAPE",
            pct(quality.get("hybrid", {}).get("median_wape")),
            help="Nižšia hodnota je lepšia.",
        )
        cards[3].metric(
            "Coverage",
            pct(coverage.get("overall_holdout")),
            help="Pokrytie kalibrovaného intervalu na holdout dátach.",
        )

        left, right = st.columns(2)

        with left:
            st.markdown("#### Porovnanie modelov")
            values = [
                {
                    "Model": label,
                    "WAPE": 100.0 * safe_float(
                        quality.get(key, {}).get("median_wape")
                    ),
                }
                for label, key in (
                    ("Baseline", "baseline"),
                    ("LightGBM", "lightgbm"),
                    ("Hybrid", "hybrid"),
                )
            ]
            st.vega_lite_chart(
                {
                    "data": {"values": values},
                    "mark": {"type": "bar", "cornerRadiusEnd": 4},
                    "height": 300,
                    "encoding": {
                        "x": {
                            "field": "Model",
                            "type": "nominal",
                            "sort": ["Baseline", "LightGBM", "Hybrid"],
                            "title": None,
                        },
                        "y": {
                            "field": "WAPE",
                            "type": "quantitative",
                            "title": "Median WAPE (%)",
                            "scale": {"zero": True},
                        },
                        "tooltip": [
                            {"field": "Model", "type": "nominal"},
                            {
                                "field": "WAPE",
                                "type": "quantitative",
                                "format": ".2f",
                            },
                        ],
                    },
                },
                use_container_width=True,
            )

        with right:
            st.markdown("#### Vybraný model podľa produktu")
            values = [
                {
                    "Model": "Baseline",
                    "Produkty": int(selection.get("baseline", 0)),
                },
                {
                    "Model": "LightGBM",
                    "Produkty": int(selection.get("lightgbm", 0)),
                },
            ]
            st.vega_lite_chart(
                {
                    "data": {"values": values},
                    "mark": {"type": "bar", "cornerRadiusEnd": 4},
                    "height": 300,
                    "encoding": {
                        "y": {
                            "field": "Model",
                            "type": "nominal",
                            "title": None,
                        },
                        "x": {
                            "field": "Produkty",
                            "type": "quantitative",
                            "title": "Počet produktov",
                            "scale": {"zero": True},
                        },
                        "tooltip": [
                            {"field": "Model", "type": "nominal"},
                            {"field": "Produkty", "type": "quantitative"},
                        ],
                    },
                },
                use_container_width=True,
            )
            st.caption(
                "Cold-start produkty: "
                f"{int(selection.get('cold_start', 0))}."
            )

        st.markdown("#### Kalibrácia intervalov")
        coverage_cards = st.columns(3)
        coverage_cards[0].metric(
            "Celkové holdout coverage",
            pct(coverage.get("overall_holdout")),
        )
        coverage_cards[1].metric(
            "Minimum podľa horizontu",
            pct(coverage.get("minimum_horizon")),
        )
        coverage_cards[2].metric(
            "Minimum model × horizont",
            pct(coverage.get("minimum_cell")),
        )

        st.markdown("#### Data-quality warningy")
        warning_count = int(dq.get("warning_count", 0))
        critical_count = int(dq.get("critical_count", 0))
        issue_count = int(dq.get("issue_count", 0))

        if critical_count:
            st.error(f"Kritické dátové chyby: {critical_count}.")
        elif warning_count:
            st.warning(
                f"Validácia má {warning_count} upozornenie "
                f"z {issue_count} nálezov. Odporúčania ostávajú pending."
            )
        else:
            st.success("Validácia nemá kritické chyby ani warningy.")

        issue_rows = [
            {
                "Závažnosť": row.get("severity"),
                "Kontrola": row.get("check_code"),
                "Entita": row.get("entity_type"),
                "Stĺpec": row.get("column_name"),
                "Správa": row.get("message"),
                "Hodnota": str(row.get("observed_value")),
            }
            for row in dq.get("issues", [])
        ]
        if issue_rows:
            st.dataframe(
                issue_rows,
                use_container_width=True,
                hide_index=True,
            )

        st.markdown("#### Feature run a model lineage")
        feature_col, lineage_col = st.columns(2)

        with feature_col:
            st.dataframe(
                [
                    {"Ukazovateľ": "Feature run", "Hodnota": feature.get("id")},
                    {
                        "Ukazovateľ": "Feature verzia",
                        "Hodnota": feature.get("feature_version"),
                    },
                    {
                        "Ukazovateľ": "Produkty",
                        "Hodnota": feature.get("product_count"),
                    },
                    {
                        "Ukazovateľ": "Riadky",
                        "Hodnota": feature.get("row_count"),
                    },
                    {
                        "Ukazovateľ": "Sales cutoff",
                        "Hodnota": feature.get("sales_source_max_month"),
                    },
                    {
                        "Ukazovateľ": "Inventory mesiac",
                        "Hodnota": feature.get(
                            "inventory_source_max_month"
                        ),
                    },
                    {
                        "Ukazovateľ": "Panel maximum",
                        "Hodnota": feature.get("panel_max_month"),
                    },
                ],
                use_container_width=True,
                hide_index=True,
            )

        with lineage_col:
            labels = (
                ("Data quality", "data_quality_run_id"),
                ("Feature run", "feature_run_id"),
                ("Baseline", "baseline_run_id"),
                ("LightGBM", "lightgbm_run_id"),
                ("Hybrid", "hybrid_run_id"),
                ("Kalibrovaný model", "calibrated_run_id"),
            )
            st.dataframe(
                [
                    {"Fáza": label, "Run ID": lineage.get(key)}
                    for label, key in labels
                ],
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Technický detail lineage"):
            st.json(
                {
                    "training_cutoff": overview.get("training_cutoff"),
                    "forecast_period": overview.get("forecast_period"),
                    "dataset_fingerprint": lineage.get(
                        "dataset_fingerprint"
                    ),
                    "feature_git_commit": lineage.get(
                        "feature_git_commit"
                    ),
                    "calibrated_code_commit": lineage.get(
                        "calibrated_code_commit"
                    ),
                    "calibrated_finished_at": lineage.get(
                        "calibrated_finished_at"
                    ),
                }
            )

    with tabs[1]:
        if not product_ids:
            st.warning(
                "ML API nevrátilo žiadne produkty."
            )
        else:
            selected_product = st.selectbox(
                "Produkt",
                options=product_ids,
                key="ml_forecast_product",
            )

            product_forecasts = [
                row
                for row in forecasts
                if row.get("product_id")
                == selected_product
            ]

            first_forecast = (
                product_forecasts[0]
                if product_forecasts
                else {}
            )

            forecast_metrics = st.columns(4)
            forecast_metrics[0].metric(
                "P50 · najbližší mesiac",
                safe_float(
                    first_forecast.get(
                        "forecast_p50"
                    )
                ),
            )
            forecast_metrics[1].metric(
                "P90 · najbližší mesiac",
                safe_float(
                    first_forecast.get(
                        "forecast_p90"
                    )
                ),
            )
            forecast_metrics[2].metric(
                "Confidence",
                probability_percent(
                    first_forecast.get(
                        "confidence_score"
                    )
                ),
            )
            forecast_metrics[3].metric(
                "Vybraný model",
                str(
                    first_forecast.get(
                        "selected_model",
                        "—",
                    )
                ),
            )

            render_forecast_chart(
                product_forecasts
            )

            st.dataframe(
                [
                    {
                        "Mesiac":
                            row.get("forecast_month"),
                        "Horizont":
                            row.get("horizon"),
                        "P10":
                            row.get("forecast_p10"),
                        "P50":
                            row.get("forecast_p50"),
                        "P90":
                            row.get("forecast_p90"),
                        "Model":
                            row.get("selected_model"),
                        "Cold start":
                            row.get("is_cold_start"),
                        "Confidence":
                            row.get("confidence_score"),
                    }
                    for row in product_forecasts
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tabs[2]:
        controls = st.columns(2)

        minimum_probability = controls[0].slider(
            "Minimálne stockout riziko za 30 dní",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
            format="%.2f",
            key="ml_risk_probability",
        )

        recommended_only = controls[1].checkbox(
            "Iba produkty s odporúčaným nákupom",
            value=False,
            key="ml_risk_recommended_only",
        )

        filtered_risks = filter_risk_rows(
            risks,
            minimum_probability,
            recommended_only,
        )

        risk_metrics = st.columns(4)
        risk_metrics[0].metric(
            "Zobrazené produkty",
            len(filtered_risks),
        )
        risk_metrics[1].metric(
            "Priemer P30",
            probability_percent(
                (
                    sum(
                        safe_float(
                            row.get(
                                "stockout_probability_30d"
                            )
                        )
                        for row in filtered_risks
                    )
                    / len(filtered_risks)
                )
                if filtered_risks
                else 0
            ),
        )
        risk_metrics[2].metric(
            "Odporúčaný nákup",
            sum(
                int(
                    row.get(
                        "recommended_order_quantity",
                        0,
                    )
                )
                for row in filtered_risks
            ),
        )
        risk_metrics[3].metric(
            "Incoming",
            sum(
                int(row.get("incoming_quantity", 0))
                for row in filtered_risks
            ),
        )

        st.dataframe(
            risk_table_rows(filtered_risks),
            use_container_width=True,
            hide_index=True,
            height=520,
            column_config={
                "Produkt":
                    st.column_config.TextColumn(
                        "Produkt",
                        pinned=True,
                    ),
                "P30":
                    st.column_config.NumberColumn(
                        "P30",
                        format="%.1f %%",
                    ),
                "P60":
                    st.column_config.NumberColumn(
                        "P60",
                        format="%.1f %%",
                    ),
                "P90":
                    st.column_config.NumberColumn(
                        "P90",
                        format="%.1f %%",
                    ),
                "Overstock 90":
                    st.column_config.NumberColumn(
                        "Overstock 90",
                        format="%.1f %%",
                    ),
            },
        )

    with tabs[3]:
        type_options = list(
            RECOMMENDATION_LABELS
        )

        controls = st.columns(2)
        selected_types = controls[0].multiselect(
            "Typ odporúčania",
            options=type_options,
            default=type_options,
            format_func=recommendation_label,
            key="ml_recommendation_types",
        )
        minimum_priority = controls[1].slider(
            "Minimálna priorita",
            min_value=1,
            max_value=100,
            value=1,
            step=1,
            key="ml_recommendation_priority",
        )

        filtered_recommendations = (
            filter_recommendations(
                recommendations,
                selected_types,
                minimum_priority,
            )
        )

        recommendation_metrics = st.columns(4)
        recommendation_metrics[0].metric(
            "Zobrazené odporúčania",
            len(filtered_recommendations),
        )
        recommendation_metrics[1].metric(
            "EXPEDITE",
            sum(
                row.get("recommendation_type")
                == "EXPEDITE"
                for row in filtered_recommendations
            ),
        )
        recommendation_metrics[2].metric(
            "PURCHASE",
            sum(
                row.get("recommendation_type")
                == "PURCHASE"
                for row in filtered_recommendations
            ),
        )
        recommendation_metrics[3].metric(
            "Navrhované množstvo",
            action_quantity(
                filtered_recommendations
            ),
        )

        st.dataframe(
            recommendation_table_rows(
                filtered_recommendations
            ),
            use_container_width=True,
            hide_index=True,
            height=520,
            column_config={
                "Produkt":
                    st.column_config.TextColumn(
                        "Produkt",
                        pinned=True,
                    ),
                "Priorita":
                    st.column_config.ProgressColumn(
                        "Priorita",
                        min_value=1,
                        max_value=100,
                    ),
                "Confidence":
                    st.column_config.NumberColumn(
                        "Confidence",
                        format="%.1f %%",
                    ),
                "Vysvetlenie":
                    st.column_config.TextColumn(
                        "Vysvetlenie",
                        width="large",
                    ),
            },
        )

    with tabs[4]:
        if not product_ids:
            st.warning(
                "ML API nevrátilo žiadne produkty."
            )
        else:
            selected_product = st.selectbox(
                "Produkt",
                options=product_ids,
                key="ml_detail_product",
            )

            try:
                detail = api_get(
                    (
                        "/api/v1/ml/products/"
                        + selected_product
                    ),
                    None,
                )
            except Exception as exc:
                st.error(
                    "Detail produktu sa nepodarilo načítať. "
                    f"Typ chyby: {type(exc).__name__}"
                )
                return

            product = detail.get("product") or {}
            detail_forecasts = (
                detail.get("forecasts") or []
            )
            risk = (
                detail.get("inventory_risk") or {}
            )
            recommendation = (
                detail.get("recommendation") or {}
            )

            st.subheader(
                str(
                    product.get(
                        "product_name",
                        selected_product,
                    )
                )
            )
            st.caption(
                f"{selected_product} · "
                f"{product.get('category', '—')}"
            )

            product_metrics = st.columns(5)
            product_metrics[0].metric(
                "Sklad",
                risk.get("stock_available", "—"),
            )
            product_metrics[1].metric(
                "Incoming",
                risk.get("incoming_quantity", "—"),
            )
            product_metrics[2].metric(
                "Reorder point",
                risk.get("reorder_point", "—"),
            )
            product_metrics[3].metric(
                "P30",
                probability_percent(
                    risk.get(
                        "stockout_probability_30d"
                    )
                ),
            )
            product_metrics[4].metric(
                "Odporúčanie",
                recommendation_label(
                    str(
                        recommendation.get(
                            "recommendation_type",
                            "—",
                        )
                    )
                ),
            )

            render_forecast_chart(
                detail_forecasts
            )

            left, right = st.columns(2)

            with left:
                st.subheader("Produkt a sklad")
                st.json(
                    {
                        "product": product,
                        "inventory_risk": risk,
                    }
                )

            with right:
                st.subheader("Odporúčanie")
                st.json(recommendation)
