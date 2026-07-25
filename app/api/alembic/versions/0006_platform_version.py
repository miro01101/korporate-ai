"""Set platform version to 0.2.0.

Revision ID: 0006_platform_version
Revises: 0005_core_logistics
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_platform_version"
down_revision: str | None = "0005_core_logistics"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0.2.0',
                updated_at = now()
            WHERE key = 'platform_version'
            """
        )
    )

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


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0.1.0',
                updated_at = now()
            WHERE key = 'platform_version'
            """
        )
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
