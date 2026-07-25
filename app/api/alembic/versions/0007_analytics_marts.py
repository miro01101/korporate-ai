"""Create analytical mart tables.

Revision ID: 0007_analytics_marts
Revises: 0006_platform_version
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_analytics_marts"
down_revision: str | None = "0006_platform_version"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def refreshed_at_column() -> sa.Column:
    return sa.Column(
        "refreshed_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "refresh_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "row_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_refresh_runs_status",
        ),
        schema="mart",
    )

    op.create_table(
        "sales_monthly",
        sa.Column("month_start", sa.Date(), primary_key=True),
        sa.Column(
            "revenue",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "estimated_cost",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "gross_profit",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "gross_margin_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column("units_sold", sa.BigInteger(), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("customer_count", sa.Integer(), nullable=False),
        sa.Column(
            "average_order_value",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
        sa.Column("sales_line_count", sa.Integer(), nullable=False),
        sa.Column(
            "historical_cost_line_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "fallback_cost_line_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "historical_cost_coverage_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=False,
        ),
        refreshed_at_column(),
        schema="mart",
    )

    op.create_table(
        "product_sales_monthly",
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column("units_sold", sa.BigInteger(), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("customer_count", sa.Integer(), nullable=False),
        sa.Column(
            "revenue",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "estimated_cost",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "gross_profit",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "gross_margin_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "average_unit_price",
            sa.Numeric(precision=14, scale=4),
            nullable=True,
        ),
        sa.Column(
            "historical_cost_coverage_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=False,
        ),
        refreshed_at_column(),
        sa.PrimaryKeyConstraint(
            "month_start",
            "product_id",
            name="pk_product_sales_monthly",
        ),
        schema="mart",
    )
    op.create_index(
        "ix_product_sales_monthly_category",
        "product_sales_monthly",
        ["month_start", "category"],
        schema="mart",
    )
    op.create_index(
        "ix_product_sales_monthly_supplier",
        "product_sales_monthly",
        ["month_start", "supplier_name"],
        schema="mart",
    )

    op.create_table(
        "inventory_health_monthly",
        sa.Column("month_start", sa.Date(), primary_key=True),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("stockout_products", sa.Integer(), nullable=False),
        sa.Column("below_min_products", sa.Integer(), nullable=False),
        sa.Column("above_max_products", sa.Integer(), nullable=False),
        sa.Column("healthy_products", sa.Integer(), nullable=False),
        sa.Column(
            "inventory_cost_value",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "inventory_sales_value",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "inventory_potential_margin_value",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "products_with_month_sales",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "products_without_month_sales",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "historical_cost_product_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "fallback_cost_product_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "days_cover_coverage_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=False,
        ),
        sa.Column(
            "average_days_of_cover",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
        sa.Column(
            "median_days_of_cover",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
        refreshed_at_column(),
        schema="mart",
    )

    op.create_table(
        "procurement_supplier_monthly",
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column(
            "purchase_order_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "purchase_line_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("ordered_units", sa.BigInteger(), nullable=False),
        sa.Column("delivered_units", sa.BigInteger(), nullable=False),
        sa.Column("undelivered_units", sa.BigInteger(), nullable=False),
        sa.Column(
            "ordered_value",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "delivered_value",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "fill_rate_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "average_actual_lead_time_days",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "average_standard_lead_time_days",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "within_standard_lead_time_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column("late_line_count", sa.Integer(), nullable=False),
        refreshed_at_column(),
        sa.PrimaryKeyConstraint(
            "month_start",
            "supplier_id",
            name="pk_procurement_supplier_monthly",
        ),
        schema="mart",
    )

    op.create_table(
        "expedition_monthly",
        sa.Column("month_start", sa.Date(), primary_key=True),
        sa.Column("expedition_count", sa.Integer(), nullable=False),
        sa.Column(
            "own_delivery_expeditions",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "external_delivery_expeditions",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "pickup_expeditions",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "total_weight_kg",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "total_volume_m3",
            sa.Numeric(precision=16, scale=3),
            nullable=False,
        ),
        sa.Column(
            "average_picking_hours",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "median_picking_hours",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "average_order_to_expedition_days",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "same_day_order_expedition_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        refreshed_at_column(),
        schema="mart",
    )

    op.create_table(
        "vehicle_utilization_monthly",
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("vehicle_id", sa.Text(), nullable=False),
        sa.Column("driver", sa.Text(), nullable=False),
        sa.Column("capacity_kg", sa.Integer(), nullable=False),
        sa.Column(
            "capacity_m3",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("trip_count", sa.Integer(), nullable=False),
        sa.Column("active_day_count", sa.Integer(), nullable=False),
        sa.Column(
            "transported_weight_kg",
            sa.Numeric(precision=16, scale=2),
            nullable=False,
        ),
        sa.Column(
            "transported_volume_m3",
            sa.Numeric(precision=16, scale=3),
            nullable=False,
        ),
        sa.Column(
            "average_weight_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "average_volume_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "maximum_weight_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "maximum_volume_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column("overloaded_trips", sa.Integer(), nullable=False),
        refreshed_at_column(),
        sa.PrimaryKeyConstraint(
            "month_start",
            "vehicle_id",
            name="pk_vehicle_utilization_monthly",
        ),
        schema="mart",
    )

    op.create_table(
        "management_kpis_monthly",
        sa.Column("month_start", sa.Date(), primary_key=True),
        sa.Column(
            "revenue",
            sa.Numeric(precision=16, scale=2),
            nullable=True,
        ),
        sa.Column(
            "estimated_cost",
            sa.Numeric(precision=16, scale=2),
            nullable=True,
        ),
        sa.Column(
            "gross_profit",
            sa.Numeric(precision=16, scale=2),
            nullable=True,
        ),
        sa.Column(
            "gross_margin_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column("units_sold", sa.BigInteger(), nullable=True),
        sa.Column("sales_order_count", sa.Integer(), nullable=True),
        sa.Column("customer_count", sa.Integer(), nullable=True),
        sa.Column(
            "inventory_cost_value",
            sa.Numeric(precision=16, scale=2),
            nullable=True,
        ),
        sa.Column("stockout_products", sa.Integer(), nullable=True),
        sa.Column("below_min_products", sa.Integer(), nullable=True),
        sa.Column("above_max_products", sa.Integer(), nullable=True),
        sa.Column(
            "average_days_of_cover",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
        sa.Column(
            "procurement_delivered_value",
            sa.Numeric(precision=16, scale=2),
            nullable=True,
        ),
        sa.Column(
            "procurement_fill_rate_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "average_procurement_lead_time_days",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column(
            "procurement_within_standard_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column("expedition_count", sa.Integer(), nullable=True),
        sa.Column(
            "own_delivery_expeditions",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "external_delivery_expeditions",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("pickup_expeditions", sa.Integer(), nullable=True),
        sa.Column(
            "average_picking_hours",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column("own_fleet_trip_count", sa.Integer(), nullable=True),
        sa.Column(
            "average_vehicle_weight_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        sa.Column(
            "average_vehicle_volume_utilization_pct",
            sa.Numeric(precision=9, scale=4),
            nullable=True,
        ),
        refreshed_at_column(),
        schema="mart",
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0007_analytics_marts',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
            ON ALL TABLES IN SCHEMA mart
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE, SELECT, UPDATE
            ON ALL SEQUENCES IN SCHEMA mart
            TO korporate_app
            """
        )
    )


def downgrade() -> None:
    for table_name in (
        "management_kpis_monthly",
        "vehicle_utilization_monthly",
        "expedition_monthly",
        "procurement_supplier_monthly",
        "inventory_health_monthly",
        "product_sales_monthly",
        "sales_monthly",
        "refresh_runs",
    ):
        op.drop_table(table_name, schema="mart")

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0006_platform_version',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )
