"""Create normalized core logistics tables.

Revision ID: 0005_core_logistics
Revises: 0004_staging_xlsx
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_core_logistics"
down_revision: str | None = "0004_staging_xlsx"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def batch_column() -> sa.Column:
    return sa.Column(
        "source_import_batch_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
    )


def batch_fk(table_name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["source_import_batch_id"],
        ["audit.import_batches.id"],
        name=f"fk_{table_name}_source_batch",
    )


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        batch_column(),
        *timestamps(),
        sa.UniqueConstraint(
            "supplier_name",
            name="uq_suppliers_name",
        ),
        batch_fk("suppliers"),
        schema="core",
    )

    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Text(), primary_key=True),
        sa.Column("customer_name", sa.Text(), nullable=False),
        batch_column(),
        *timestamps(),
        batch_fk("customers"),
        schema="core",
    )

    op.create_table(
        "products",
        sa.Column("product_id", sa.Text(), primary_key=True),
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
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
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
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["core.suppliers.id"],
            name="fk_products_supplier",
        ),
        batch_fk("products"),
        schema="core",
    )
    op.create_index(
        "ix_products_supplier",
        "products",
        ["supplier_id"],
        schema="core",
    )

    op.create_table(
        "vehicles",
        sa.Column("vehicle_id", sa.Text(), primary_key=True),
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
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        *timestamps(),
        batch_fk("vehicles"),
        schema="core",
    )

    op.create_table(
        "sales_orders",
        sa.Column("order_id", sa.Text(), primary_key=True),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("order_status", sa.Text(), nullable=False),
        sa.Column("expedition_date", sa.Date(), nullable=False),
        batch_column(),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["core.customers.customer_id"],
            name="fk_sales_orders_customer",
        ),
        batch_fk("sales_orders"),
        schema="core",
    )
    op.create_index(
        "ix_sales_orders_customer",
        "sales_orders",
        ["customer_id"],
        schema="core",
    )
    op.create_index(
        "ix_sales_orders_order_date",
        "sales_orders",
        ["order_date"],
        schema="core",
    )

    op.create_table(
        "sales_order_lines",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "unit_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["core.sales_orders.order_id"],
            name="fk_sales_order_lines_order",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_sales_order_lines_product",
        ),
        batch_fk("sales_order_lines"),
        sa.UniqueConstraint(
            "order_id",
            "line_number",
            name="uq_sales_order_lines_order_line",
        ),
        schema="core",
    )
    op.create_index(
        "ix_sales_order_lines_product",
        "sales_order_lines",
        ["product_id"],
        schema="core",
    )

    op.create_table(
        "purchase_orders",
        sa.Column("purchase_order_id", sa.Text(), primary_key=True),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        batch_column(),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["core.suppliers.id"],
            name="fk_purchase_orders_supplier",
        ),
        batch_fk("purchase_orders"),
        schema="core",
    )
    op.create_index(
        "ix_purchase_orders_supplier",
        "purchase_orders",
        ["supplier_id"],
        schema="core",
    )
    op.create_index(
        "ix_purchase_orders_order_date",
        "purchase_orders",
        ["order_date"],
        schema="core",
    )

    op.create_table(
        "purchase_order_lines",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("purchase_order_id", sa.Text(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("ordered_quantity", sa.Integer(), nullable=False),
        sa.Column("delivered_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "purchase_price",
            sa.Numeric(precision=10, scale=2),
            nullable=False,
        ),
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["core.purchase_orders.purchase_order_id"],
            name="fk_purchase_order_lines_order",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_purchase_order_lines_product",
        ),
        batch_fk("purchase_order_lines"),
        sa.UniqueConstraint(
            "purchase_order_id",
            "line_number",
            name="uq_purchase_order_lines_order_line",
        ),
        schema="core",
    )
    op.create_index(
        "ix_purchase_order_lines_product",
        "purchase_order_lines",
        ["product_id"],
        schema="core",
    )

    op.create_table(
        "inventory_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        batch_column(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "snapshot_date",
            name="uq_inventory_snapshots_date",
        ),
        batch_fk("inventory_snapshots"),
        schema="core",
    )

    op.create_table(
        "inventory_snapshot_lines",
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("stock_actual", sa.Integer(), nullable=False),
        sa.Column("stock_reserved", sa.Integer(), nullable=False),
        sa.Column("stock_available", sa.Integer(), nullable=False),
        sa.Column("warehouse_location", sa.Text(), nullable=False),
        sa.Column("min_stock", sa.Integer(), nullable=False),
        sa.Column("max_stock", sa.Integer(), nullable=False),
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id",
            "product_id",
            name="pk_inventory_snapshot_lines",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["core.inventory_snapshots.id"],
            name="fk_inventory_lines_snapshot",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_inventory_lines_product",
        ),
        batch_fk("inventory_snapshot_lines"),
        schema="core",
    )
    op.create_index(
        "ix_inventory_lines_product",
        "inventory_snapshot_lines",
        ["product_id"],
        schema="core",
    )

    op.create_table(
        "expeditions",
        sa.Column("order_id", sa.Text(), primary_key=True),
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
        batch_column(),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["core.sales_orders.order_id"],
            name="fk_expeditions_order",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["core.vehicles.vehicle_id"],
            name="fk_expeditions_vehicle",
        ),
        batch_fk("expeditions"),
        schema="core",
    )
    op.create_index(
        "ix_expeditions_vehicle",
        "expeditions",
        ["vehicle_id"],
        schema="core",
    )
    op.create_index(
        "ix_expeditions_date",
        "expeditions",
        ["expedition_date"],
        schema="core",
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0005_core_logistics',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA core
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE, SELECT, UPDATE
            ON ALL SEQUENCES IN SCHEMA core
            TO korporate_app
            """
        )
    )


def downgrade() -> None:
    for table_name in (
        "expeditions",
        "inventory_snapshot_lines",
        "inventory_snapshots",
        "purchase_order_lines",
        "purchase_orders",
        "sales_order_lines",
        "sales_orders",
        "vehicles",
        "products",
        "customers",
        "suppliers",
    ):
        op.drop_table(table_name, schema="core")

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
