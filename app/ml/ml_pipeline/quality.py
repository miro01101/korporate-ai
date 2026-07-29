from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class QualityIssue:
    severity: str
    check_code: str
    entity_type: str
    entity_id: str | None
    period: date | None
    column_name: str | None
    observed_value: dict[str, Any]
    expected_rule: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _issue(
    *,
    severity: str,
    check_code: str,
    entity_type: str,
    expected_rule: str,
    message: str,
    entity_id: str | None = None,
    period: date | None = None,
    column_name: str | None = None,
    observed_value: dict[str, Any] | None = None,
) -> QualityIssue:
    return QualityIssue(
        severity=severity,
        check_code=check_code,
        entity_type=entity_type,
        entity_id=entity_id,
        period=period,
        column_name=column_name,
        observed_value=observed_value or {},
        expected_rule=expected_rule,
        message=message,
    )


def validate_source_frames(
    frames: dict[str, pd.DataFrame],
) -> list[QualityIssue]:
    products = frames["products"].copy()
    sales = frames["sales"].copy()
    inventory = frames["inventory"].copy()
    purchases = frames["purchases"].copy()

    issues: list[QualityIssue] = []

    required_product_columns = {
        "product_id",
        "product_name",
        "category",
        "supplier_id",
        "purchase_price",
        "sales_price",
        "minimum_order_quantity",
        "lead_time_days",
    }

    missing_product_columns = sorted(
        required_product_columns - set(products.columns)
    )

    if missing_product_columns:
        issues.append(
            _issue(
                severity="critical",
                check_code="PRODUCT_REQUIRED_COLUMNS",
                entity_type="dataset",
                expected_rule="All required product columns exist.",
                message="Product dataset is missing required columns.",
                observed_value={
                    "missing_columns": missing_product_columns
                },
            )
        )
        return issues

    duplicate_products = products[
        products.duplicated("product_id", keep=False)
    ]

    if not duplicate_products.empty:
        issues.append(
            _issue(
                severity="critical",
                check_code="PRODUCT_ID_UNIQUE",
                entity_type="product",
                expected_rule="product_id is unique.",
                message="Duplicate product identifiers detected.",
                observed_value={
                    "duplicate_rows": int(len(duplicate_products)),
                    "duplicate_products": int(
                        duplicate_products[
                            "product_id"
                        ].nunique()
                    ),
                },
            )
        )

    for column in (
        "purchase_price",
        "sales_price",
        "minimum_order_quantity",
        "lead_time_days",
    ):
        invalid = products[
            pd.to_numeric(
                products[column],
                errors="coerce",
            ).fillna(-1)
            <= 0
        ]

        if not invalid.empty:
            issues.append(
                _issue(
                    severity="critical",
                    check_code=f"PRODUCT_{column.upper()}_POSITIVE",
                    entity_type="product",
                    column_name=column,
                    expected_rule=f"{column} must be greater than zero.",
                    message=f"Invalid {column} values detected.",
                    observed_value={
                        "invalid_rows": int(len(invalid))
                    },
                )
            )

    known_products = set(
        products["product_id"].astype(str)
    )

    if not sales.empty:
        duplicate_sales = sales[
            sales.duplicated(
                ["product_id", "month_start"],
                keep=False,
            )
        ]

        if not duplicate_sales.empty:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="SALES_PRODUCT_MONTH_UNIQUE",
                    entity_type="sales",
                    expected_rule=(
                        "One sales row per product and month."
                    ),
                    message=(
                        "Duplicate product-month sales rows detected."
                    ),
                    observed_value={
                        "duplicate_rows": int(len(duplicate_sales))
                    },
                )
            )

        unknown_sales_products = (
            set(sales["product_id"].astype(str))
            - known_products
        )

        if unknown_sales_products:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="SALES_PRODUCT_REFERENCE",
                    entity_type="sales",
                    expected_rule=(
                        "Every sales product exists in core.products."
                    ),
                    message="Sales contain unknown product IDs.",
                    observed_value={
                        "unknown_product_count": len(
                            unknown_sales_products
                        )
                    },
                )
            )

        for column in (
            "units_sold",
            "revenue",
            "gross_profit",
            "order_count",
            "customer_count",
        ):
            values = pd.to_numeric(
                sales[column],
                errors="coerce",
            )

            negative_count = int((values < 0).sum())

            if negative_count:
                severity = (
                    "warning"
                    if column == "gross_profit"
                    else "critical"
                )

                issues.append(
                    _issue(
                        severity=severity,
                        check_code=(
                            f"SALES_{column.upper()}_NONNEGATIVE"
                        ),
                        entity_type="sales",
                        column_name=column,
                        expected_rule=(
                            f"{column} should not be negative."
                        ),
                        message=(
                            f"Negative {column} values detected."
                        ),
                        observed_value={
                            "negative_rows": negative_count
                        },
                    )
                )

    if not inventory.empty:
        duplicate_inventory = inventory[
            inventory.duplicated(
                ["product_id", "month_start"],
                keep=False,
            )
        ]

        if not duplicate_inventory.empty:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="INVENTORY_PRODUCT_MONTH_UNIQUE",
                    entity_type="inventory",
                    expected_rule=(
                        "One inventory row per product and month."
                    ),
                    message=(
                        "Duplicate product-month inventory rows detected."
                    ),
                    observed_value={
                        "duplicate_rows": int(
                            len(duplicate_inventory)
                        )
                    },
                )
            )

        inventory_numeric = inventory[
            [
                "stock_actual",
                "stock_reserved",
                "stock_available",
                "min_stock",
                "max_stock",
            ]
        ].apply(pd.to_numeric, errors="coerce")

        negative_inventory = int(
            (inventory_numeric < 0).any(axis=1).sum()
        )

        if negative_inventory:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="INVENTORY_NONNEGATIVE",
                    entity_type="inventory",
                    expected_rule=(
                        "Inventory quantities must not be negative."
                    ),
                    message="Negative inventory values detected.",
                    observed_value={
                        "invalid_rows": negative_inventory
                    },
                )
            )

        stock_balance_invalid = int(
            (
                inventory_numeric["stock_actual"]
                - inventory_numeric["stock_reserved"]
                != inventory_numeric["stock_available"]
            ).sum()
        )

        if stock_balance_invalid:
            issues.append(
                _issue(
                    severity="warning",
                    check_code="INVENTORY_STOCK_BALANCE",
                    entity_type="inventory",
                    expected_rule=(
                        "stock_available equals "
                        "stock_actual minus stock_reserved."
                    ),
                    message=(
                        "Inventory stock balance inconsistencies "
                        "detected."
                    ),
                    observed_value={
                        "invalid_rows": stock_balance_invalid
                    },
                )
            )

        min_max_invalid = int(
            (
                inventory_numeric["max_stock"]
                < inventory_numeric["min_stock"]
            ).sum()
        )

        if min_max_invalid:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="INVENTORY_MIN_MAX_ORDER",
                    entity_type="inventory",
                    expected_rule=(
                        "max_stock must be greater than or "
                        "equal to min_stock."
                    ),
                    message=(
                        "Inventory min/max stock inconsistencies "
                        "detected."
                    ),
                    observed_value={
                        "invalid_rows": min_max_invalid
                    },
                )
            )

    if not purchases.empty:
        ordered = pd.to_numeric(
            purchases["ordered_quantity"],
            errors="coerce",
        )

        delivered = pd.to_numeric(
            purchases["delivered_quantity"],
            errors="coerce",
        )

        invalid_quantity = int(
            (
                (ordered <= 0)
                | (delivered < 0)
                | (delivered > ordered)
            ).sum()
        )

        if invalid_quantity:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="PURCHASE_QUANTITY_CONSISTENCY",
                    entity_type="purchase",
                    expected_rule=(
                        "ordered_quantity > 0 and "
                        "0 <= delivered_quantity <= ordered_quantity."
                    ),
                    message=(
                        "Invalid purchase quantities detected."
                    ),
                    observed_value={
                        "invalid_rows": invalid_quantity
                    },
                )
            )

        order_dates = pd.to_datetime(
            purchases["order_date"],
            errors="coerce",
        )

        delivery_dates = pd.to_datetime(
            purchases["delivery_date"],
            errors="coerce",
        )

        invalid_dates = int(
            (delivery_dates < order_dates).sum()
        )

        if invalid_dates:
            issues.append(
                _issue(
                    severity="critical",
                    check_code="PURCHASE_DATE_ORDER",
                    entity_type="purchase",
                    expected_rule=(
                        "delivery_date must not precede order_date."
                    ),
                    message=(
                        "Purchase delivery dates precede order dates."
                    ),
                    observed_value={
                        "invalid_rows": invalid_dates
                    },
                )
            )

    if not sales.empty:
        sales_months = pd.to_datetime(
            sales["month_start"]
        ).dt.to_period("M")

        expected_months = pd.period_range(
            sales_months.min(),
            sales_months.max(),
            freq="M",
        )

        missing_months = expected_months.difference(
            sales_months.unique()
        )

        if len(missing_months):
            issues.append(
                _issue(
                    severity="critical",
                    check_code="SALES_CALENDAR_CONTINUITY",
                    entity_type="sales",
                    expected_rule=(
                        "Every calendar month exists in the "
                        "sales history."
                    ),
                    message="Sales history has missing months.",
                    observed_value={
                        "missing_months": [
                            str(value)
                            for value in missing_months
                        ]
                    },
                )
            )

    return issues
