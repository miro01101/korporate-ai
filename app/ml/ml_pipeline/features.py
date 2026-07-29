from __future__ import annotations

from collections.abc import Iterable
import hashlib
import math

import numpy as np
import pandas as pd


SALES_VALUE_COLUMNS = (
    "units_sold",
    "order_count",
    "customer_count",
    "revenue",
    "gross_profit",
)

INVENTORY_COLUMNS = (
    "stock_actual",
    "stock_reserved",
    "stock_available",
    "min_stock",
    "max_stock",
)


def dataset_fingerprint(
    frames: Iterable[pd.DataFrame],
) -> str:
    digest = hashlib.sha256()

    for frame in frames:
        normalized = frame.copy()

        normalized = normalized.reindex(
            sorted(normalized.columns),
            axis=1,
        )

        normalized = normalized.sort_values(
            list(normalized.columns),
            kind="stable",
        ).reset_index(drop=True)

        digest.update(
            pd.util.hash_pandas_object(
                normalized.astype("string"),
                index=False,
            ).values.tobytes()
        )

    return digest.hexdigest()


def _months_since_last_sale(
    values: pd.Series,
) -> pd.Series:
    output: list[float] = []
    last_positive_index: int | None = None

    for position, raw_value in enumerate(values):
        value = float(raw_value)

        if value > 0:
            output.append(0.0)
            last_positive_index = position
        elif last_positive_index is None:
            output.append(float("nan"))
        else:
            output.append(
                float(position - last_positive_index)
            )

    return pd.Series(
        output,
        index=values.index,
        dtype="float64",
    )


def _abc_for_month(
    values: pd.Series,
) -> pd.Series:
    positive = values.clip(lower=0).fillna(0)
    total = float(positive.sum())

    result = pd.Series(
        "C",
        index=values.index,
        dtype="string",
    )

    if total <= 0:
        return result

    ordered = positive.sort_values(
        ascending=False,
        kind="stable",
    )

    cumulative_before = (
        ordered.cumsum() - ordered
    ) / total

    classes = pd.Series(
        np.where(
            cumulative_before < 0.80,
            "A",
            np.where(
                cumulative_before < 0.95,
                "B",
                "C",
            ),
        ),
        index=ordered.index,
        dtype="string",
    )

    result.loc[classes.index] = classes

    return result


def _xyz_class(
    zero_ratio: float | None,
    coefficient_of_variation: float | None,
    is_cold_start: bool,
) -> str:
    if is_cold_start:
        return "Z"

    if zero_ratio is None or math.isnan(zero_ratio):
        return "Z"

    cv = (
        coefficient_of_variation
        if coefficient_of_variation is not None
        else float("inf")
    )

    if math.isnan(cv):
        cv = float("inf")

    if zero_ratio <= 0.15 and cv <= 0.50:
        return "X"

    if zero_ratio <= 0.40 and cv <= 1.00:
        return "Y"

    return "Z"


def build_product_monthly_features(
    products: pd.DataFrame,
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
) -> pd.DataFrame:
    if products.empty:
        raise ValueError("Product dataset is empty.")

    products = products.copy()
    sales = sales.copy()
    inventory = inventory.copy()

    for frame in (sales, inventory):
        if not frame.empty:
            frame["month_start"] = (
                pd.to_datetime(frame["month_start"])
                .dt.to_period("M")
                .dt.to_timestamp()
            )

    month_candidates: list[pd.Timestamp] = []

    if not sales.empty:
        month_candidates.extend(
            [
                sales["month_start"].min(),
                sales["month_start"].max(),
            ]
        )

    if not inventory.empty:
        month_candidates.extend(
            [
                inventory["month_start"].min(),
                inventory["month_start"].max(),
            ]
        )

    if not month_candidates:
        raise ValueError(
            "Neither sales nor inventory contains a month."
        )

    min_month = min(month_candidates)
    max_month = max(month_candidates)

    months = pd.DataFrame(
        {
            "month_start": pd.date_range(
                min_month,
                max_month,
                freq="MS",
            )
        }
    )

    product_dimensions = products[
        [
            "product_id",
            "product_name",
            "category",
            "supplier_id",
            "purchase_price",
            "sales_price",
            "minimum_order_quantity",
            "lead_time_days",
        ]
    ].drop_duplicates("product_id")

    panel = product_dimensions.merge(
        months,
        how="cross",
    )

    sales_columns = [
        "month_start",
        "product_id",
        *SALES_VALUE_COLUMNS,
    ]

    panel = panel.merge(
        sales[sales_columns],
        on=["product_id", "month_start"],
        how="left",
        validate="one_to_one",
    )

    for column in SALES_VALUE_COLUMNS:
        panel[column] = pd.to_numeric(
            panel[column],
            errors="coerce",
        ).fillna(0)

    if inventory.empty:
        for column in INVENTORY_COLUMNS:
            panel[column] = np.nan
    else:
        panel = panel.merge(
            inventory[
                [
                    "month_start",
                    "product_id",
                    *INVENTORY_COLUMNS,
                ]
            ],
            on=["product_id", "month_start"],
            how="left",
            validate="one_to_one",
        )

        for column in INVENTORY_COLUMNS:
            panel[column] = pd.to_numeric(
                panel[column],
                errors="coerce",
            )

    panel = panel.sort_values(
        ["product_id", "month_start"],
        kind="stable",
    ).reset_index(drop=True)

    feature_groups: list[pd.DataFrame] = []

    for _, group in panel.groupby(
        "product_id",
        sort=False,
        observed=True,
    ):
        group = group.copy()
        units = group["units_sold"].astype(float)

        for lag in (1, 2, 3, 6, 12):
            group[f"lag_{lag}"] = units.shift(lag)

        shifted_units = units.shift(1)

        for window in (3, 6, 12):
            group[f"rolling_mean_{window}"] = (
                shifted_units.rolling(
                    window=window,
                    min_periods=1,
                ).mean()
            )

        for window in (3, 6):
            group[f"rolling_std_{window}"] = (
                shifted_units.rolling(
                    window=window,
                    min_periods=2,
                ).std(ddof=0)
            )

        group["zero_demand"] = units.eq(0)

        group["zero_ratio_12"] = (
            group["zero_demand"]
            .astype(float)
            .rolling(
                window=12,
                min_periods=1,
            )
            .mean()
        )

        rolling_mean_current = units.rolling(
            window=12,
            min_periods=2,
        ).mean()

        rolling_std_current = units.rolling(
            window=12,
            min_periods=2,
        ).std(ddof=0)

        group["demand_cv_12"] = np.where(
            rolling_mean_current > 0,
            rolling_std_current / rolling_mean_current,
            np.nan,
        )

        group["months_since_last_sale"] = (
            _months_since_last_sale(units)
        )

        group["gross_profit_12"] = (
            group["gross_profit"]
            .astype(float)
            .rolling(
                window=12,
                min_periods=1,
            )
            .sum()
        )

        group["positive_demand_months"] = (
            units.gt(0).astype(int).cumsum()
        )

        group["is_cold_start"] = (
            group["positive_demand_months"] < 3
        )

        feature_groups.append(group)

    output = pd.concat(
        feature_groups,
        ignore_index=True,
    )

    output["abc_class"] = (
        output.groupby(
            "month_start",
            group_keys=False,
            observed=True,
        )["gross_profit_12"]
        .apply(_abc_for_month)
        .astype("string")
    )

    output["xyz_class"] = [
        _xyz_class(
            float(zero_ratio)
            if pd.notna(zero_ratio)
            else None,
            float(cv)
            if pd.notna(cv)
            else None,
            bool(cold_start),
        )
        for zero_ratio, cv, cold_start in zip(
            output["zero_ratio_12"],
            output["demand_cv_12"],
            output["is_cold_start"],
            strict=True,
        )
    ]

    output["units_sold"] = (
        output["units_sold"].round().astype("int64")
    )

    output["order_count"] = (
        output["order_count"].round().astype("int64")
    )

    output["customer_count"] = (
        output["customer_count"].round().astype("int64")
    )

    return output.drop(
        columns=[
            "gross_profit_12",
            "positive_demand_months",
        ]
    )
