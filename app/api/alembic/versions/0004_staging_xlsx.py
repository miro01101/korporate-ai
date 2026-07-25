"""Create typed XLSX staging tables.

Revision ID: 0004_staging_xlsx
Revises: 0003_raw_xlsx
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_staging_xlsx"
down_revision: str | None = "0003_raw_xlsx"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def audit_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "import_batch_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "source_row_number",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def batch_constraints(table_name: str) -> list[sa.Constraint]:
    return [
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["audit.import_batches.id"],
            name=f"fk_{table_name}_batch",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "import_batch_id",
            "source_row_number",
            name=f"uq_{table_name}_batch_row",
        ),
        sa.CheckConstraint(
            "source_row_number >= 2",
            name=f"ck_{table_name}_source_row",
        ),
    ]


def create_batch_index(table_name: str) -> None:
    op.create_index(
        f"ix_{table_name}_batch",
        table_name,
        ["import_batch_id"],
        unique=False,
        schema="stg",
    )


def upgrade() -> None:
    op.create_table(
        "products",
        *audit_columns(),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column(
            "purchase_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column(
            "sales_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("supplier", sa.Text(), nullable=False),
        sa.Column(
            "minimum_order_quantity",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column(
            "weight_kg",
            sa.Numeric(precision=10, scale=3),
            nullable=False,
        ),
        sa.Column(
            "volume_m3",
            sa.Numeric(precision=10, scale=5),
            nullable=False,
        ),
        *batch_constraints("products"),
        sa.UniqueConstraint(
            "import_batch_id",
            "product_id",
            name="uq_products_batch_product",
        ),
        schema="stg",
    )
    create_batch_index("products")

    op.create_table(
        "sales",
        *audit_columns(),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "unit_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("order_status", sa.Text(), nullable=False),
        sa.Column("expedition_date", sa.Date(), nullable=False),
        *batch_constraints("sales"),
        schema="stg",
    )
    create_batch_index("sales")
    op.create_index(
        "ix_sales_batch_order",
        "sales",
        ["import_batch_id", "order_id"],
        unique=False,
        schema="stg",
    )

    op.create_table(
        "inventory",
        *audit_columns(),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("stock_actual", sa.Integer(), nullable=False),
        sa.Column("stock_reserved", sa.Integer(), nullable=False),
        sa.Column("stock_available", sa.Integer(), nullable=False),
        sa.Column("warehouse_location", sa.Text(), nullable=False),
        sa.Column("min_stock", sa.Integer(), nullable=False),
        sa.Column("max_stock", sa.Integer(), nullable=False),
        *batch_constraints("inventory"),
        sa.UniqueConstraint(
            "import_batch_id",
            "snapshot_date",
            "product_id",
            name="uq_inventory_batch_snapshot_product",
        ),
        schema="stg",
    )
    create_batch_index("inventory")
    op.create_index(
        "ix_inventory_batch_snapshot",
        "inventory",
        ["import_batch_id", "snapshot_date"],
        unique=False,
        schema="stg",
    )

    op.create_table(
        "purchases",
        *audit_columns(),
        sa.Column("purchase_order_id", sa.Text(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("supplier", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("ordered_quantity", sa.Integer(), nullable=False),
        sa.Column("delivered_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "purchase_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        *batch_constraints("purchases"),
        schema="stg",
    )
    create_batch_index("purchases")
    op.create_index(
        "ix_purchases_batch_order",
        "purchases",
        ["import_batch_id", "purchase_order_id"],
        unique=False,
        schema="stg",
    )

    op.create_table(
        "expedition",
        *audit_columns(),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=False),
            nullable=False,
        ),
        sa.Column(
            "picked_at",
            sa.DateTime(timezone=False),
            nullable=False,
        ),
        sa.Column("expedition_date", sa.Date(), nullable=False),
        sa.Column("delivery_type", sa.Text(), nullable=False),
        sa.Column("vehicle_id", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column(
            "weight_kg",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
        ),
        sa.Column(
            "volume_m3",
            sa.Numeric(precision=12, scale=3),
            nullable=False,
        ),
        *batch_constraints("expedition"),
        sa.UniqueConstraint(
            "import_batch_id",
            "order_id",
            name="uq_expedition_batch_order",
        ),
        schema="stg",
    )
    create_batch_index("expedition")

    op.create_table(
        "vehicles",
        *audit_columns(),
        sa.Column("vehicle_id", sa.Text(), nullable=False),
        sa.Column("capacity_kg", sa.Integer(), nullable=False),
        sa.Column(
            "capacity_m3",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("availability", sa.Text(), nullable=False),
        sa.Column(
            "cost_per_km",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        sa.Column("driver", sa.Text(), nullable=False),
        *batch_constraints("vehicles"),
        sa.UniqueConstraint(
            "import_batch_id",
            "vehicle_id",
            name="uq_vehicles_batch_vehicle",
        ),
        schema="stg",
    )
    create_batch_index("vehicles")

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0004_staging_xlsx',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA stg
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE, SELECT, UPDATE
            ON ALL SEQUENCES IN SCHEMA stg
            TO korporate_app
            """
        )
    )


def downgrade() -> None:
    for table_name in (
        "vehicles",
        "expedition",
        "purchases",
        "inventory",
        "sales",
        "products",
    ):
        op.drop_table(table_name, schema="stg")

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0003_raw_xlsx',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )
