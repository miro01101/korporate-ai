"""Add business keys for idempotent full-workbook merge.

Revision ID: 0011_full_workbook_merge
Revises: 0010_ml_foundation
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_full_workbook_merge"
down_revision: str | None = "0010_ml_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "purchase_orders",
        sa.Column(
            "source_purchase_order_id",
            sa.Text(),
            nullable=True,
        ),
        schema="core",
    )

    op.execute(
        sa.text(
            """
            UPDATE core.purchase_orders
            SET source_purchase_order_id = purchase_order_id
            WHERE source_purchase_order_id IS NULL
            """
        )
    )

    op.alter_column(
        "purchase_orders",
        "source_purchase_order_id",
        schema="core",
        existing_type=sa.Text(),
        nullable=False,
    )

    op.create_unique_constraint(
        "uq_core_purchase_orders_source_supplier",
        "purchase_orders",
        ["source_purchase_order_id", "supplier_id"],
        schema="core",
    )
    op.create_unique_constraint(
        "uq_core_sales_order_lines_order_product",
        "sales_order_lines",
        ["order_id", "product_id"],
        schema="core",
    )
    op.create_unique_constraint(
        "uq_core_purchase_order_lines_order_product",
        "purchase_order_lines",
        ["purchase_order_id", "product_id"],
        schema="core",
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = CASE
                    WHEN key = 'schema_revision'
                        THEN '0011_full_workbook_merge'
                    ELSE value
                END,
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_core_purchase_order_lines_order_product",
        "purchase_order_lines",
        schema="core",
        type_="unique",
    )
    op.drop_constraint(
        "uq_core_sales_order_lines_order_product",
        "sales_order_lines",
        schema="core",
        type_="unique",
    )
    op.drop_constraint(
        "uq_core_purchase_orders_source_supplier",
        "purchase_orders",
        schema="core",
        type_="unique",
    )
    op.drop_column(
        "purchase_orders",
        "source_purchase_order_id",
        schema="core",
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = CASE
                    WHEN key = 'schema_revision'
                        THEN '0010_ml_foundation'
                    ELSE value
                END,
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )
