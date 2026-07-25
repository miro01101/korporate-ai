"""Create initial platform schemas and metadata.

Revision ID: 0001_platform_skeleton
Revises:
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_platform_skeleton"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


SCHEMAS = (
    "meta",
    "raw",
    "stg",
    "core",
    "mart",
    "ml",
    "workflow",
    "audit",
    "auth",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(
            sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        )

    op.create_table(
        "system_info",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="meta",
    )

    op.execute(
        sa.text(
            """
            INSERT INTO meta.system_info (key, value)
            VALUES
                ('application', 'Korporate AI Logistics Platform'),
                ('platform_version', '0.1.0'),
                ('schema_revision', '0001_platform_skeleton')
            """
        )
    )

    for schema in SCHEMAS:
        op.execute(
            sa.text(
                f'GRANT USAGE ON SCHEMA "{schema}" '
                "TO korporate_app"
            )
        )

        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE '
                f'ON ALL TABLES IN SCHEMA "{schema}" '
                "TO korporate_app"
            )
        )

        op.execute(
            sa.text(
                f'GRANT USAGE, SELECT, UPDATE '
                f'ON ALL SEQUENCES IN SCHEMA "{schema}" '
                "TO korporate_app"
            )
        )

        op.execute(
            sa.text(
                f'ALTER DEFAULT PRIVILEGES '
                f'FOR ROLE korporate_admin '
                f'IN SCHEMA "{schema}" '
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                "ON TABLES TO korporate_app"
            )
        )

        op.execute(
            sa.text(
                f'ALTER DEFAULT PRIVILEGES '
                f'FOR ROLE korporate_admin '
                f'IN SCHEMA "{schema}" '
                "GRANT USAGE, SELECT, UPDATE "
                "ON SEQUENCES TO korporate_app"
            )
        )


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(
            sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        )
