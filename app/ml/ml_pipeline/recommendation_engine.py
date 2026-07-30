from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import numpy as np
import pandas as pd
from psycopg import Connection
from psycopg.types.json import Jsonb

from ml_pipeline.db import execute_many, query_frame


POLICY_VERSION = "recommendation-policy-v1"
EXPECTED_PRODUCT_COUNT = 80
RECOMMENDATION_NAMESPACE = UUID(
    "4b24d26a-b560-4bd4-9f41-6676ac0383a4"
)

EXPEDITE = "EXPEDITE"
PURCHASE = "PURCHASE"
REDUCE_DEFER = "REDUCE/DEFER"
REVIEW = "REVIEW"
HOLD = "HOLD"
RECOMMENDATION_TYPES = (
    EXPEDITE,
    PURCHASE,
    REDUCE_DEFER,
    REVIEW,
    HOLD,
)


@dataclass(frozen=True)
class RecommendationSummary:
    model_run_id: UUID
    as_of_date: date
    product_count: int
    row_count: int
    type_counts: dict[str, int]
    pending_count: int
    recommended_quantity: int


def deterministic_recommendation_id(
    model_run_id: UUID,
    product_id: str,
) -> UUID:
    return uuid5(
        RECOMMENDATION_NAMESPACE,
        f"{model_run_id}|{product_id}",
    )


def shortage_score(
    probability_30d: float,
    probability_60d: float,
    probability_90d: float,
) -> int:
    score = round(
        100.0
        * (
            0.50 * float(probability_30d)
            + 0.30 * float(probability_60d)
            + 0.20 * float(probability_90d)
        )
    )
    return int(np.clip(score, 0, 100))


def classify_recommendation(row: pd.Series) -> str:
    quantity = int(row["recommended_order_quantity"])
    incoming = int(row["incoming_quantity"])
    p30 = float(row["stockout_probability_30d"])
    overstock = float(row["overstock_probability_90d"])
    stock = int(row["stock_available"])
    max_stock = int(row["max_stock"])
    cold_start = bool(row["is_cold_start"])

    if quantity > 0 and incoming > 0 and p30 >= 0.50:
        return EXPEDITE
    if quantity > 0:
        return PURCHASE
    if overstock >= 0.50 or stock > max_stock:
        return REDUCE_DEFER
    if cold_start:
        return REVIEW
    return HOLD


def recommendation_priority(
    recommendation_type: str,
    row: pd.Series,
) -> int:
    shortage = shortage_score(
        float(row["stockout_probability_30d"]),
        float(row["stockout_probability_60d"]),
        float(row["stockout_probability_90d"]),
    )
    overstock = round(
        100.0 * float(row["overstock_probability_90d"])
    )

    if recommendation_type == EXPEDITE:
        return min(100, max(80, shortage))
    if recommendation_type == PURCHASE:
        return min(99, max(50, shortage))
    if recommendation_type == REDUCE_DEFER:
        return min(90, max(40, overstock))
    if recommendation_type == REVIEW:
        return 40
    return 10


def reason_codes(
    recommendation_type: str,
    row: pd.Series,
) -> list[str]:
    codes = [
        "POLICY_V1",
        "TYPE_" + recommendation_type.replace("/", "_"),
        "SEGMENT_"
        + str(row["abc_class"])
        + "_"
        + str(row["xyz_class"]),
    ]

    quantity = int(row["recommended_order_quantity"])
    incoming = int(row["incoming_quantity"])
    p30 = float(row["stockout_probability_30d"])
    overstock = float(row["overstock_probability_90d"])
    stock = int(row["stock_available"])
    max_stock = int(row["max_stock"])
    confidence = float(row["confidence_score"])

    if quantity > 0:
        codes.append("REORDER_REQUIRED")
    if incoming > 0:
        codes.append("OPEN_INCOMING")
    if p30 >= 0.50:
        codes.append("HIGH_STOCKOUT_RISK_30D")
    if p30 >= 0.75:
        codes.append("CRITICAL_STOCKOUT_RISK_30D")
    if overstock >= 0.50:
        codes.append("HIGH_OVERSTOCK_RISK_90D")
    if stock > max_stock:
        codes.append("ABOVE_MAX_STOCK")
    if bool(row["is_cold_start"]):
        codes.append("COLD_START")
    if confidence < 0.40:
        codes.append("LOW_MODEL_CONFIDENCE")
    if recommendation_type == HOLD:
        codes.append("NO_ACTION_REQUIRED")

    return codes


def _decimal_text(value: Any) -> str:
    return f"{float(value):.3f}"


def recommended_action(
    recommendation_type: str,
    row: pd.Series,
) -> str:
    quantity = int(row["recommended_order_quantity"])
    recommended_date = row["recommended_order_date"]

    if recommendation_type == EXPEDITE:
        return (
            "Urýchliť otvorenú dodávku a pripraviť "
            f"doplňujúci nákup {quantity} ks."
        )
    if recommendation_type == PURCHASE:
        return (
            f"Pripraviť nákup {quantity} ks k "
            f"{recommended_date}."
        )
    if recommendation_type == REDUCE_DEFER:
        return (
            "Pozastaviť alebo odložiť ďalšie objednávky "
            "a manuálne preveriť nadbytočnú zásobu."
        )
    if recommendation_type == REVIEW:
        return (
            "Manuálne preveriť cold-start produkt pred "
            "nákupným rozhodnutím."
        )
    return "Bez novej objednávky; pokračovať v monitorovaní."


def explanation(
    recommendation_type: str,
    row: pd.Series,
) -> str:
    quantity = int(row["recommended_order_quantity"])
    moq = int(row["minimum_order_quantity"])
    stock = int(row["stock_available"])
    incoming = int(row["incoming_quantity"])
    earliest = row.get("earliest_incoming")
    earliest_text = (
        str(earliest)
        if earliest is not None and not pd.isna(earliest)
        else "bez potvrdeného dátumu"
    )

    prefix = {
        EXPEDITE: (
            "Aj po započítaní otvoreného príjmu zostáva "
            "vysoké krátkodobé riziko nedostatku."
        ),
        PURCHASE: (
            "Skladová pozícia je pod kalibrovaným reorder pointom."
        ),
        REDUCE_DEFER: (
            "Nový nákup nie je potrebný a zásoba vykazuje "
            "riziko nadbytku alebo prekročenie max_stock."
        ),
        REVIEW: (
            "Cold-start produkt nemá dostatočnú históriu na "
            "automatizované nákupné rozhodnutie."
        ),
        HOLD: (
            "Aktuálna skladová pozícia nevyžaduje operatívny zásah."
        ),
    }[recommendation_type]

    return (
        f"{prefix} Produkt={row['product_id']}; segment="
        f"{row['abc_class']}/{row['xyz_class']}; sklad={stock} ks; "
        f"incoming={incoming} ks ({earliest_text}); odporúčané "
        f"množstvo={quantity} ks; MOQ={moq}; reorder_point="
        f"{_decimal_text(row['reorder_point'])}; P(stockout 30/60/90d)="
        f"{_decimal_text(row['stockout_probability_30d'])}/"
        f"{_decimal_text(row['stockout_probability_60d'])}/"
        f"{_decimal_text(row['stockout_probability_90d'])}; "
        f"P(overstock 90d)="
        f"{_decimal_text(row['overstock_probability_90d'])}; model="
        f"{row['selected_model']}; confidence="
        f"{_decimal_text(row['confidence_score'])}; politika="
        f"{POLICY_VERSION}. Rozhodnutie musí potvrdiť používateľ."
    )


def build_recommendations(context: pd.DataFrame) -> pd.DataFrame:
    required = {
        "model_run_id",
        "product_id",
        "as_of_date",
        "stock_available",
        "incoming_quantity",
        "reorder_point",
        "stockout_probability_30d",
        "stockout_probability_60d",
        "stockout_probability_90d",
        "overstock_probability_90d",
        "recommended_order_quantity",
        "recommended_order_date",
        "minimum_order_quantity",
        "max_stock",
        "abc_class",
        "xyz_class",
        "selected_model",
        "is_cold_start",
        "confidence_score",
        "earliest_incoming",
    }
    missing = required - set(context.columns)
    if missing:
        raise ValueError(
            "Missing recommendation context columns: "
            + ", ".join(sorted(missing))
        )
    if len(context) != EXPECTED_PRODUCT_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_PRODUCT_COUNT} products; "
            f"found {len(context)}."
        )
    if context["product_id"].astype(str).nunique() != EXPECTED_PRODUCT_COUNT:
        raise ValueError(
            "Recommendation context must contain 80 unique products."
        )
    if context["model_run_id"].nunique() != 1:
        raise ValueError("Recommendation context must use one model run.")
    if context["as_of_date"].nunique() != 1:
        raise ValueError("Recommendation context must use one as-of date.")

    records: list[dict[str, Any]] = []
    for _, row in context.sort_values(
        "product_id",
        kind="stable",
    ).iterrows():
        recommendation_type = classify_recommendation(row)
        quantity = int(row["recommended_order_quantity"])
        action_quantity = (
            quantity
            if recommendation_type in {EXPEDITE, PURCHASE}
            else None
        )
        action_date = (
            row["recommended_order_date"]
            if recommendation_type in {EXPEDITE, PURCHASE}
            else None
        )
        confidence = float(
            np.clip(float(row["confidence_score"]), 0.0, 1.0)
        )
        model_run_id = row["model_run_id"]
        if not isinstance(model_run_id, UUID):
            model_run_id = UUID(str(model_run_id))

        records.append(
            {
                "id": deterministic_recommendation_id(
                    model_run_id,
                    str(row["product_id"]),
                ),
                "model_run_id": model_run_id,
                "product_id": str(row["product_id"]),
                "recommendation_type": recommendation_type,
                "priority": recommendation_priority(
                    recommendation_type,
                    row,
                ),
                "recommended_action": recommended_action(
                    recommendation_type,
                    row,
                ),
                "recommended_quantity": action_quantity,
                "recommended_date": action_date,
                "expected_value_eur": None,
                "risk_if_ignored_eur": None,
                "confidence": confidence,
                "reason_codes": reason_codes(
                    recommendation_type,
                    row,
                ),
                "explanation": explanation(
                    recommendation_type,
                    row,
                ),
                "status": "pending",
            }
        )

    output = pd.DataFrame(records)
    if output["id"].nunique() != EXPECTED_PRODUCT_COUNT:
        raise ValueError("Deterministic recommendation IDs are not unique.")
    if output.duplicated(["model_run_id", "product_id"]).any():
        raise ValueError("Duplicate model-run/product recommendation rows.")
    if not output["priority"].between(1, 100).all():
        raise ValueError("Recommendation priorities must be in [1, 100].")
    if not output["confidence"].between(0.0, 1.0).all():
        raise ValueError("Recommendation confidence must be in [0, 1].")
    if set(output["status"]) != {"pending"}:
        raise ValueError("All generated recommendations must be pending.")

    action_mask = output["recommendation_type"].isin(
        [EXPEDITE, PURCHASE]
    )
    if output.loc[action_mask, "recommended_quantity"].isna().any():
        raise ValueError("Purchase/expedite rows require quantity.")
    if (output.loc[action_mask, "recommended_quantity"] <= 0).any():
        raise ValueError("Purchase/expedite quantities must be positive.")
    if output.loc[action_mask, "recommended_date"].isna().any():
        raise ValueError("Purchase/expedite rows require a date.")
    if output.loc[~action_mask, "recommended_quantity"].notna().any():
        raise ValueError("Non-purchase rows must not carry order quantity.")
    if output.loc[~action_mask, "recommended_date"].notna().any():
        raise ValueError("Non-purchase rows must not carry order date.")

    return output


def _load_context(connection: Connection[Any]) -> pd.DataFrame:
    return query_frame(
        connection,
        """
        WITH ready_run AS (
            SELECT id, feature_run_id, training_cutoff
            FROM ml.model_runs
            WHERE status = 'completed'
              AND model_family = 'hybrid_calibrated'
              AND coalesce(
                    (parameters ->> 'inventory_risk_ready')::boolean,
                    false
                  ) = true
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
        ),
        open_lines AS (
            SELECT
                pol.product_id,
                min(pol.delivery_date) AS earliest_incoming
            FROM core.purchase_order_lines AS pol
            JOIN core.purchase_orders AS po
              ON po.purchase_order_id = pol.purchase_order_id
            CROSS JOIN ready_run AS rr
            WHERE po.order_date <= rr.training_cutoff
              AND pol.delivery_date >= rr.training_cutoff
              AND pol.ordered_quantity > pol.delivered_quantity
            GROUP BY pol.product_id
        )
        SELECT
            r.model_run_id,
            r.product_id,
            r.as_of_date,
            r.stock_available,
            r.incoming_quantity,
            r.reorder_point,
            r.stockout_probability_30d,
            r.stockout_probability_60d,
            r.stockout_probability_90d,
            r.overstock_probability_90d,
            r.recommended_order_quantity,
            r.recommended_order_date,
            p.minimum_order_quantity,
            pf.max_stock,
            pf.abc_class,
            pf.xyz_class,
            f.selected_model,
            f.is_cold_start,
            f.confidence_score,
            ol.earliest_incoming
        FROM ready_run AS rr
        JOIN ml.inventory_risk AS r
          ON r.model_run_id = rr.id
        JOIN core.products AS p
          ON p.product_id = r.product_id
        JOIN ml.product_monthly_features AS pf
          ON pf.feature_run_id = rr.feature_run_id
         AND pf.product_id = r.product_id
         AND pf.month_start = rr.training_cutoff
        JOIN ml.forecasts AS f
          ON f.model_run_id = rr.id
         AND f.product_id = r.product_id
         AND f.horizon = 1
        LEFT JOIN open_lines AS ol
          ON ol.product_id = r.product_id
        ORDER BY r.product_id
        """,
    )


def preview_recommendations(connection: Connection[Any]) -> pd.DataFrame:
    return build_recommendations(_load_context(connection))


def run_recommendation_engine(
    connection: Connection[Any],
) -> RecommendationSummary:
    recommendations = preview_recommendations(connection)
    model_run_id = recommendations.iloc[0]["model_run_id"]

    existing = query_frame(
        connection,
        """
        SELECT id, product_id
        FROM ml.recommendations
        WHERE model_run_id = %s
        """,
        (model_run_id,),
    )
    expected_ids = {
        str(row.product_id): row.id
        for row in recommendations.itertuples(index=False)
    }
    for row in existing.itertuples(index=False):
        expected_id = expected_ids.get(str(row.product_id))
        if expected_id is None or row.id != expected_id:
            raise RuntimeError(
                "Existing recommendations conflict with deterministic IDs."
            )

    rows = []
    for row in recommendations.itertuples(index=False):
        quantity = (
            None
            if pd.isna(row.recommended_quantity)
            else int(row.recommended_quantity)
        )
        recommended_date = (
            None
            if pd.isna(row.recommended_date)
            else row.recommended_date
        )
        rows.append(
            (
                row.id,
                row.model_run_id,
                row.product_id,
                row.recommendation_type,
                int(row.priority),
                row.recommended_action,
                quantity,
                recommended_date,
                row.expected_value_eur,
                row.risk_if_ignored_eur,
                Decimal(str(row.confidence)),
                Jsonb(list(row.reason_codes)),
                row.explanation,
                row.status,
            )
        )

    try:
        execute_many(
            connection,
            """
            INSERT INTO ml.recommendations (
                id,
                model_run_id,
                product_id,
                recommendation_type,
                priority,
                recommended_action,
                recommended_quantity,
                recommended_date,
                expected_value_eur,
                risk_if_ignored_eur,
                confidence,
                reason_codes,
                explanation,
                status
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (id) DO UPDATE
            SET recommendation_type = EXCLUDED.recommendation_type,
                priority = EXCLUDED.priority,
                recommended_action = EXCLUDED.recommended_action,
                recommended_quantity = EXCLUDED.recommended_quantity,
                recommended_date = EXCLUDED.recommended_date,
                expected_value_eur = EXCLUDED.expected_value_eur,
                risk_if_ignored_eur = EXCLUDED.risk_if_ignored_eur,
                confidence = EXCLUDED.confidence,
                reason_codes = EXCLUDED.reason_codes,
                explanation = EXCLUDED.explanation,
                updated_at = now()
            WHERE ml.recommendations.status = 'pending'
            """,
            rows,
        )

        validation = query_frame(
            connection,
            """
            SELECT
                count(*) AS row_count,
                count(DISTINCT product_id) AS product_count,
                count(*) FILTER (WHERE status = 'pending') AS pending_count,
                count(*) FILTER (
                    WHERE priority < 1 OR priority > 100
                       OR confidence < 0 OR confidence > 1
                ) AS invalid_score_count,
                count(*) FILTER (
                    WHERE recommendation_type IN ('PURCHASE', 'EXPEDITE')
                      AND (recommended_quantity IS NULL
                           OR recommended_quantity <= 0
                           OR recommended_date IS NULL)
                ) AS invalid_action_count,
                count(*) FILTER (
                    WHERE recommendation_type NOT IN ('PURCHASE', 'EXPEDITE')
                      AND (recommended_quantity IS NOT NULL
                           OR recommended_date IS NOT NULL)
                ) AS invalid_nonaction_count,
                count(*) FILTER (
                    WHERE status = 'pending'
                      AND (
                        expected_value_eur IS NOT NULL
                        OR risk_if_ignored_eur IS NOT NULL
                      )
                ) AS unapproved_financial_value_count
            FROM ml.recommendations
            WHERE model_run_id = %s
            """,
            (model_run_id,),
        ).iloc[0]

        if int(validation["row_count"]) != EXPECTED_PRODUCT_COUNT:
            raise RuntimeError("Recommendation write did not create 80 rows.")
        if int(validation["product_count"]) != EXPECTED_PRODUCT_COUNT:
            raise RuntimeError("Recommendation product uniqueness failed.")
        for column in (
            "invalid_score_count",
            "invalid_action_count",
            "invalid_nonaction_count",
            "unapproved_financial_value_count",
        ):
            if int(validation[column]) != 0:
                raise RuntimeError(
                    f"Recommendation validation failed: {column}."
                )

        connection.commit()
    except Exception:
        connection.rollback()
        raise

    type_counts = {
        name: int(
            (recommendations["recommendation_type"] == name).sum()
        )
        for name in RECOMMENDATION_TYPES
    }
    return RecommendationSummary(
        model_run_id=model_run_id,
        as_of_date=_context_date(recommendations),
        product_count=EXPECTED_PRODUCT_COUNT,
        row_count=EXPECTED_PRODUCT_COUNT,
        type_counts=type_counts,
        pending_count=int(validation["pending_count"]),
        recommended_quantity=int(
            recommendations["recommended_quantity"].fillna(0).sum()
        ),
    )


def _context_date(recommendations: pd.DataFrame) -> date:
    value = recommendations.iloc[0]["as_of_date"]
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value
