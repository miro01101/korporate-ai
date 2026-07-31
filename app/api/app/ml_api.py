"""Read-only machine-learning API endpoints."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.config import get_settings
from app.db import engine


router = APIRouter(
    prefix="/api/v1/ml",
    tags=["ml"],
)
settings = get_settings()


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MLStatusResponse(StrictResponseModel):
    status: Literal["ready"]
    api_version: str
    platform_version: str
    latest_model_run_id: UUID
    model_family: str
    model_version: str
    training_cutoff: date
    forecast_horizon_months: int
    forecast_rows: int
    inventory_risk_rows: int
    recommendation_rows: int
    pending_recommendations: int
    transaction_read_only: bool


class ModelRunItem(StrictResponseModel):
    id: UUID
    feature_run_id: UUID
    status: str
    model_family: str
    model_version: str
    training_cutoff: date
    forecast_horizon_months: int
    feature_version: str
    code_commit: str | None
    dataset_fingerprint: str
    parameters: dict[str, Any]
    artifact_path: str | None
    artifact_sha256: str | None
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


class ModelRunListResponse(StrictResponseModel):
    count: int
    limit: int
    items: list[ModelRunItem]


class ForecastItem(StrictResponseModel):
    model_run_id: UUID
    product_id: str
    forecast_month: date
    horizon: int
    forecast_p10: float
    forecast_p50: float
    forecast_p90: float
    selected_model: str
    is_cold_start: bool
    backtest_wape: float | None
    backtest_bias: float | None
    confidence_score: float
    created_at: datetime


class ForecastListResponse(StrictResponseModel):
    model_run_id: UUID
    count: int
    limit: int
    items: list[ForecastItem]


class InventoryRiskItem(StrictResponseModel):
    model_run_id: UUID
    product_id: str
    as_of_date: date
    stock_available: int
    incoming_quantity: int
    expected_lead_time_demand: float
    safety_stock: float
    reorder_point: float
    stockout_probability_30d: float
    stockout_probability_60d: float
    stockout_probability_90d: float
    overstock_probability_90d: float
    recommended_order_quantity: int
    recommended_order_date: date | None
    created_at: datetime


class InventoryRiskListResponse(StrictResponseModel):
    model_run_id: UUID
    count: int
    limit: int
    items: list[InventoryRiskItem]


class RecommendationItem(StrictResponseModel):
    id: UUID
    model_run_id: UUID
    product_id: str
    recommendation_type: str
    priority: int = Field(ge=1, le=100)
    recommended_action: str
    recommended_quantity: int | None
    recommended_date: date | None
    expected_value_eur: float | None
    risk_if_ignored_eur: float | None
    confidence: float = Field(ge=0, le=1)
    reason_codes: list[str]
    explanation: str
    status: str
    created_at: datetime
    updated_at: datetime


class RecommendationListResponse(StrictResponseModel):
    model_run_id: UUID
    count: int
    limit: int
    items: list[RecommendationItem]


class ProductCore(StrictResponseModel):
    product_id: str
    product_name: str
    category: str
    unit: str
    purchase_price: float
    sales_price: float
    supplier_id: int
    minimum_order_quantity: int
    lead_time_days: int
    weight_kg: float
    volume_m3: float


class ProductMLDetailResponse(StrictResponseModel):
    model_run_id: UUID
    product: ProductCore
    forecasts: list[ForecastItem]
    inventory_risk: InventoryRiskItem | None
    recommendation: RecommendationItem | None


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def row_to_dict(row: Any) -> dict[str, Any]:
    return {
        key: json_value(value)
        for key, value in row.items()
    }


def fetch_all_readonly(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as raw_connection:
        connection = raw_connection.execution_options(
            postgresql_readonly=True,
        )
        with connection.begin():
            rows = connection.execute(
                text(query),
                parameters or {},
            ).mappings().all()

    return [row_to_dict(row) for row in rows]


def fetch_one_readonly(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with engine.connect() as raw_connection:
        connection = raw_connection.execution_options(
            postgresql_readonly=True,
        )
        with connection.begin():
            row = connection.execute(
                text(query),
                parameters or {},
            ).mappings().one_or_none()

    return row_to_dict(row) if row is not None else None


def resolve_model_run_id(
    model_run_id: UUID | None,
) -> UUID:
    if model_run_id is not None:
        row = fetch_one_readonly(
            """
            SELECT id
            FROM ml.model_runs
            WHERE id = :model_run_id
            """,
            {"model_run_id": model_run_id},
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Model run was not found.",
            )
        return model_run_id

    row = fetch_one_readonly(
        """
        SELECT id
        FROM ml.model_runs
        WHERE status = 'completed'
          AND model_family = 'hybrid_calibrated'
          AND coalesce(
                (parameters ->> 'inventory_risk_ready')::boolean,
                false
              ) = true
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """
    )

    if row is None:
        raise HTTPException(
            status_code=503,
            detail="No completed ML run is ready for inventory risk.",
        )

    return UUID(row["id"])


@router.get("/status", response_model=MLStatusResponse)
def ml_status() -> dict[str, Any]:
    row = fetch_one_readonly(
        """
        WITH latest AS (
            SELECT
                id,
                model_family,
                model_version,
                training_cutoff,
                forecast_horizon_months
            FROM ml.model_runs
            WHERE status = 'completed'
              AND model_family = 'hybrid_calibrated'
              AND coalesce(
                    (parameters ->> 'inventory_risk_ready')::boolean,
                    false
                  ) = true
            ORDER BY finished_at DESC, id DESC
            LIMIT 1
        )
        SELECT
            (
                SELECT value
                FROM meta.system_info
                WHERE key = 'platform_version'
            ) AS platform_version,
            latest.id AS latest_model_run_id,
            latest.model_family,
            latest.model_version,
            latest.training_cutoff,
            latest.forecast_horizon_months,
            (
                SELECT count(*)
                FROM ml.forecasts AS f
                WHERE f.model_run_id = latest.id
            ) AS forecast_rows,
            (
                SELECT count(*)
                FROM ml.inventory_risk AS r
                WHERE r.model_run_id = latest.id
            ) AS inventory_risk_rows,
            (
                SELECT count(*)
                FROM ml.recommendations AS rec
                WHERE rec.model_run_id = latest.id
            ) AS recommendation_rows,
            (
                SELECT count(*)
                FROM ml.recommendations AS rec
                WHERE rec.model_run_id = latest.id
                  AND rec.status = 'pending'
            ) AS pending_recommendations,
            current_setting('transaction_read_only')::boolean
                AS transaction_read_only
        FROM latest
        """
    )

    if row is None:
        raise HTTPException(
            status_code=503,
            detail="No completed ML run is ready.",
        )

    return {
        "status": "ready",
        "api_version": settings.app_version,
        **row,
    }


@router.get("/model-runs", response_model=ModelRunListResponse)
def model_runs(
    status: str | None = Query(default=None),
    model_family: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    rows = fetch_all_readonly(
        """
        SELECT
            id,
            feature_run_id,
            status,
            model_family,
            model_version,
            training_cutoff,
            forecast_horizon_months,
            feature_version,
            code_commit,
            dataset_fingerprint,
            parameters,
            artifact_path,
            artifact_sha256,
            started_at,
            finished_at,
            error_message
        FROM ml.model_runs
        WHERE (
            CAST(:status AS text) IS NULL
            OR status = CAST(:status AS text)
        )
          AND (
            CAST(:model_family AS text) IS NULL
            OR model_family = CAST(:model_family AS text)
          )
        ORDER BY started_at DESC, id DESC
        LIMIT :limit
        """,
        {
            "status": status,
            "model_family": model_family,
            "limit": limit,
        },
    )

    return {
        "count": len(rows),
        "limit": limit,
        "items": rows,
    }


@router.get("/forecast", response_model=ForecastListResponse)
def forecast(
    model_run_id: UUID | None = Query(default=None),
    product_id: str | None = Query(default=None),
    horizon: int | None = Query(default=None, ge=1, le=36),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, Any]:
    resolved_run = resolve_model_run_id(model_run_id)
    rows = fetch_all_readonly(
        """
        SELECT
            model_run_id,
            product_id,
            forecast_month,
            horizon,
            forecast_p10,
            forecast_p50,
            forecast_p90,
            selected_model,
            is_cold_start,
            backtest_wape,
            backtest_bias,
            confidence_score,
            created_at
        FROM ml.forecasts
        WHERE model_run_id = :model_run_id
          AND (
            CAST(:product_id AS text) IS NULL
            OR product_id = CAST(:product_id AS text)
          )
          AND (
            CAST(:horizon AS integer) IS NULL
            OR horizon = CAST(:horizon AS integer)
          )
        ORDER BY product_id, horizon, forecast_month
        LIMIT :limit
        """,
        {
            "model_run_id": resolved_run,
            "product_id": product_id,
            "horizon": horizon,
            "limit": limit,
        },
    )
    return {
        "model_run_id": resolved_run,
        "count": len(rows),
        "limit": limit,
        "items": rows,
    }


@router.get(
    "/inventory-risk",
    response_model=InventoryRiskListResponse,
)
def inventory_risk(
    model_run_id: UUID | None = Query(default=None),
    product_id: str | None = Query(default=None),
    recommended_only: bool = Query(default=False),
    min_stockout_probability: float | None = Query(
        default=None,
        ge=0,
        le=1,
    ),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    resolved_run = resolve_model_run_id(model_run_id)
    rows = fetch_all_readonly(
        """
        SELECT
            model_run_id,
            product_id,
            as_of_date,
            stock_available,
            incoming_quantity,
            expected_lead_time_demand,
            safety_stock,
            reorder_point,
            stockout_probability_30d,
            stockout_probability_60d,
            stockout_probability_90d,
            overstock_probability_90d,
            recommended_order_quantity,
            recommended_order_date,
            created_at
        FROM ml.inventory_risk
        WHERE model_run_id = :model_run_id
          AND (
            CAST(:product_id AS text) IS NULL
            OR product_id = CAST(:product_id AS text)
          )
          AND (
            CAST(:recommended_only AS boolean) = false
            OR recommended_order_quantity > 0
          )
          AND (
            CAST(:minimum_probability AS numeric) IS NULL
            OR stockout_probability_30d >=
               CAST(:minimum_probability AS numeric)
          )
        ORDER BY
            stockout_probability_30d DESC,
            recommended_order_quantity DESC,
            product_id
        LIMIT :limit
        """,
        {
            "model_run_id": resolved_run,
            "product_id": product_id,
            "recommended_only": recommended_only,
            "minimum_probability": min_stockout_probability,
            "limit": limit,
        },
    )
    return {
        "model_run_id": resolved_run,
        "count": len(rows),
        "limit": limit,
        "items": rows,
    }


@router.get(
    "/recommendations",
    response_model=RecommendationListResponse,
)
def recommendations(
    model_run_id: UUID | None = Query(default=None),
    recommendation_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_priority: int | None = Query(default=None, ge=1, le=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    resolved_run = resolve_model_run_id(model_run_id)
    rows = fetch_all_readonly(
        """
        SELECT
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
            status,
            created_at,
            updated_at
        FROM ml.recommendations
        WHERE model_run_id = :model_run_id
          AND (
            CAST(:recommendation_type AS text) IS NULL
            OR recommendation_type =
               CAST(:recommendation_type AS text)
          )
          AND (
            CAST(:status AS text) IS NULL
            OR status = CAST(:status AS text)
          )
          AND (
            CAST(:min_priority AS integer) IS NULL
            OR priority >= CAST(:min_priority AS integer)
          )
        ORDER BY priority DESC, product_id
        LIMIT :limit
        """,
        {
            "model_run_id": resolved_run,
            "recommendation_type": recommendation_type,
            "status": status,
            "min_priority": min_priority,
            "limit": limit,
        },
    )
    return {
        "model_run_id": resolved_run,
        "count": len(rows),
        "limit": limit,
        "items": rows,
    }


@router.get(
    "/products/{product_id}",
    response_model=ProductMLDetailResponse,
)
def product_detail(
    product_id: str,
    model_run_id: UUID | None = Query(default=None),
) -> dict[str, Any]:
    resolved_run = resolve_model_run_id(model_run_id)

    product = fetch_one_readonly(
        """
        SELECT
            product_id,
            product_name,
            category,
            unit,
            purchase_price,
            sales_price,
            supplier_id,
            minimum_order_quantity,
            lead_time_days,
            weight_kg,
            volume_m3
        FROM core.products
        WHERE product_id = :product_id
        """,
        {"product_id": product_id},
    )

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Product was not found.",
        )

    forecasts = fetch_all_readonly(
        """
        SELECT
            model_run_id,
            product_id,
            forecast_month,
            horizon,
            forecast_p10,
            forecast_p50,
            forecast_p90,
            selected_model,
            is_cold_start,
            backtest_wape,
            backtest_bias,
            confidence_score,
            created_at
        FROM ml.forecasts
        WHERE model_run_id = :model_run_id
          AND product_id = :product_id
        ORDER BY horizon, forecast_month
        """,
        {
            "model_run_id": resolved_run,
            "product_id": product_id,
        },
    )

    risk = fetch_one_readonly(
        """
        SELECT
            model_run_id,
            product_id,
            as_of_date,
            stock_available,
            incoming_quantity,
            expected_lead_time_demand,
            safety_stock,
            reorder_point,
            stockout_probability_30d,
            stockout_probability_60d,
            stockout_probability_90d,
            overstock_probability_90d,
            recommended_order_quantity,
            recommended_order_date,
            created_at
        FROM ml.inventory_risk
        WHERE model_run_id = :model_run_id
          AND product_id = :product_id
        ORDER BY as_of_date DESC
        LIMIT 1
        """,
        {
            "model_run_id": resolved_run,
            "product_id": product_id,
        },
    )

    recommendation = fetch_one_readonly(
        """
        SELECT
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
            status,
            created_at,
            updated_at
        FROM ml.recommendations
        WHERE model_run_id = :model_run_id
          AND product_id = :product_id
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        {
            "model_run_id": resolved_run,
            "product_id": product_id,
        },
    )

    return {
        "model_run_id": resolved_run,
        "product": product,
        "forecasts": forecasts,
        "inventory_risk": risk,
        "recommendation": recommendation,
    }
