from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import json
from typing import Any

import pandas as pd


def _month_start(value: Any, *, label: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)

    if pd.isna(timestamp):
        raise ValueError(f"{label} is missing.")

    normalized = timestamp.to_period("M").to_timestamp()

    if timestamp.normalize() != normalized:
        raise ValueError(
            f"{label} must be a month-start value; found {timestamp.date()}."
        )

    return normalized


def frame_month_max(
    frame: pd.DataFrame,
    *,
    label: str,
) -> date | None:
    if frame.empty:
        return None

    if "month_start" not in frame.columns:
        raise ValueError(f"{label} is missing month_start.")

    months = pd.to_datetime(
        frame["month_start"],
        errors="raise",
    )

    if months.isna().any():
        raise ValueError(f"{label} contains a missing month_start.")

    maximum = months.max()
    return _month_start(maximum, label=f"{label} maximum").date()


def feature_temporal_metadata(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
    features: pd.DataFrame,
) -> dict[str, str]:
    sales_max = frame_month_max(sales, label="sales source")
    inventory_max = frame_month_max(
        inventory,
        label="inventory source",
    )
    panel_max = frame_month_max(features, label="feature panel")

    if sales_max is None:
        raise ValueError("Sales source does not contain a training month.")

    if inventory_max is None:
        raise ValueError("Inventory source does not contain a snapshot month.")

    if panel_max is None:
        raise ValueError("Feature panel does not contain a month.")

    expected_panel_max = max(sales_max, inventory_max)

    if panel_max != expected_panel_max:
        raise ValueError(
            "Feature panel maximum does not match source maxima: "
            f"{panel_max} != {expected_panel_max}."
        )

    return {
        "sales_source_max_month": sales_max.isoformat(),
        "inventory_source_max_month": inventory_max.isoformat(),
        "panel_max_month": panel_max.isoformat(),
    }


def training_cutoff_from_metadata(metadata: Any) -> date:
    payload: Mapping[str, Any]

    if isinstance(metadata, Mapping):
        payload = metadata
    elif isinstance(metadata, str):
        parsed = json.loads(metadata)
        if not isinstance(parsed, Mapping):
            raise RuntimeError("Feature metadata must be a JSON object.")
        payload = parsed
    else:
        raise RuntimeError("Feature metadata is missing or invalid.")

    raw_cutoff = payload.get("sales_source_max_month")

    if raw_cutoff in (None, ""):
        raise RuntimeError(
            "Feature metadata is missing sales_source_max_month. "
            "Build a new feature run before model training."
        )

    return _month_start(
        raw_cutoff,
        label="sales training cutoff",
    ).date()


def filter_features_to_training_cutoff(
    features: pd.DataFrame,
    training_cutoff: date | str | pd.Timestamp,
) -> pd.DataFrame:
    if features.empty:
        raise ValueError("Feature frame is empty.")

    if "product_id" not in features.columns:
        raise ValueError("Feature frame is missing product_id.")

    if "month_start" not in features.columns:
        raise ValueError("Feature frame is missing month_start.")

    cutoff = _month_start(
        training_cutoff,
        label="training cutoff",
    )

    frame = features.copy()
    frame["month_start"] = pd.to_datetime(
        frame["month_start"],
        errors="raise",
    )

    source_product_count = int(frame["product_id"].nunique())
    filtered = frame[frame["month_start"] <= cutoff].copy()

    if filtered.empty:
        raise ValueError("No feature rows remain at the training cutoff.")

    if filtered["month_start"].max() != cutoff:
        raise ValueError(
            "Feature panel does not contain the sales training cutoff month."
        )

    if int(filtered["product_id"].nunique()) != source_product_count:
        raise ValueError(
            "Training cutoff removed one or more products from the feature panel."
        )

    latest_by_product = filtered.groupby(
        "product_id",
        observed=True,
    )["month_start"].max()

    if not latest_by_product.eq(cutoff).all():
        raise ValueError(
            "Every product must contain the sales training cutoff month."
        )

    return filtered.sort_values(
        ["product_id", "month_start"],
        kind="stable",
    ).reset_index(drop=True)


def expected_forecast_months(
    training_cutoff: date | str | pd.Timestamp,
    horizons: Sequence[int],
) -> tuple[date, ...]:
    cutoff = _month_start(
        training_cutoff,
        label="training cutoff",
    )

    normalized_horizons = tuple(int(value) for value in horizons)

    if not normalized_horizons:
        raise ValueError("At least one forecast horizon is required.")

    if any(value <= 0 for value in normalized_horizons):
        raise ValueError("Forecast horizons must be positive.")

    if len(set(normalized_horizons)) != len(normalized_horizons):
        raise ValueError("Forecast horizons must be unique.")

    return tuple(
        (cutoff + pd.offsets.MonthBegin(horizon)).date()
        for horizon in normalized_horizons
    )


def validate_forecast_window(
    forecasts: pd.DataFrame,
    *,
    training_cutoff: date | str | pd.Timestamp,
    horizons: Sequence[int],
    label: str,
) -> None:
    required = {"product_id", "forecast_month", "horizon"}
    missing = required - set(forecasts.columns)

    if missing:
        raise ValueError(
            f"{label} is missing columns: {', '.join(sorted(missing))}."
        )

    if forecasts.empty:
        raise ValueError(f"{label} is empty.")

    expected_horizons = tuple(int(value) for value in horizons)
    expected_month_by_horizon = dict(
        zip(
            expected_horizons,
            expected_forecast_months(
                training_cutoff,
                expected_horizons,
            ),
            strict=True,
        )
    )

    frame = forecasts.copy()
    frame["product_id"] = frame["product_id"].astype(str)
    frame["horizon"] = pd.to_numeric(
        frame["horizon"],
        errors="raise",
    ).astype(int)
    frame["forecast_month"] = pd.to_datetime(
        frame["forecast_month"],
        errors="raise",
    ).dt.date

    if frame.duplicated(["product_id", "horizon"]).any():
        raise ValueError(f"{label} contains duplicate product-horizon rows.")

    horizon_sets = frame.groupby(
        "product_id",
        observed=True,
    )["horizon"].apply(
        lambda values: tuple(sorted(int(value) for value in values))
    )

    expected_sorted = tuple(sorted(expected_horizons))

    if not horizon_sets.apply(
        lambda value: value == expected_sorted
    ).all():
        raise ValueError(
            f"Every product in {label} must contain horizons "
            f"{expected_sorted}."
        )

    expected_months = frame["horizon"].map(expected_month_by_horizon)

    if not frame["forecast_month"].eq(expected_months).all():
        raise ValueError(
            f"{label} does not immediately follow the sales training cutoff."
        )
