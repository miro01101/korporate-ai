"""Create raw XLSX import tables.

Revision ID: 0003_raw_xlsx
Revises: 0002_import_audit
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_raw_xlsx"
down_revision: str | None = "0002_import_audit"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


RAW_TABLES = (
    "xlsx_products",
    "xlsx_sales",
    "xlsx_inventory",
    "xlsx_purchases",
    "xlsx_expedition",
    "xlsx_vehicles",
)


def upgrade() -> None:
    for table_name in RAW_TABLES:
        op.create_table(
            table_name,
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
                "source_data",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "loaded_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
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
            schema="raw",
        )

        op.create_index(
            f"ix_{table_name}_batch",
            table_name,
            ["import_batch_id"],
            unique=False,
            schema="raw",
        )

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

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA raw
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE, SELECT, UPDATE
            ON ALL SEQUENCES IN SCHEMA raw
            TO korporate_app
            """
        )
    )


def downgrade() -> None:
    for table_name in reversed(RAW_TABLES):
        op.drop_index(
            f"ix_{table_name}_batch",
            table_name=table_name,
            schema="raw",
        )
        op.drop_table(table_name, schema="raw")

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0002_import_audit',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )
