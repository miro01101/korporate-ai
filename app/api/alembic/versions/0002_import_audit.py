"""Create XLSX import audit tables.

Revision ID: 0002_import_audit
Revises: 0001_platform_skeleton
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_import_audit"
down_revision: str | None = "0001_platform_skeleton"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "source_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'manual_xlsx'"),
        ),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("archive_path", sa.Text(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("platform_version", sa.Text(), nullable=False),
        sa.Column("schema_revision", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'registered'"),
        ),
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
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "error_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "warning_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "row_count_raw",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "row_count_core",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "source_type = 'manual_xlsx'",
            name="ck_import_batches_source_type",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'registered', "
            "'validating', "
            "'raw_loaded', "
            "'staging_loaded', "
            "'rejected', "
            "'completed', "
            "'failed'"
            ")",
            name="ck_import_batches_status",
        ),
        sa.CheckConstraint(
            "file_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_import_batches_sha256",
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0",
            name="ck_import_batches_file_size_positive",
        ),
        sa.CheckConstraint(
            "error_count >= 0 "
            "AND warning_count >= 0 "
            "AND row_count_raw >= 0 "
            "AND row_count_core >= 0",
            name="ck_import_batches_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_import_batches_time_order",
        ),
        schema="audit",
    )

    op.create_index(
        "uq_import_batches_file_sha256",
        "import_batches",
        ["file_sha256"],
        unique=True,
        schema="audit",
    )
    op.create_index(
        "ix_import_batches_status_started_at",
        "import_batches",
        ["status", "started_at"],
        unique=False,
        schema="audit",
    )

    op.create_table(
        "import_issues",
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
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("sheet_name", sa.Text(), nullable=True),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("business_key", sa.Text(), nullable=True),
        sa.Column("column_name", sa.Text(), nullable=True),
        sa.Column("actual_value", sa.Text(), nullable=True),
        sa.Column("expected_condition", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["audit.import_batches.id"],
            name="fk_import_issues_batch",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "severity IN ('ERROR', 'WARNING', 'INFO')",
            name="ck_import_issues_severity",
        ),
        sa.CheckConstraint(
            "source_row_number IS NULL OR source_row_number >= 2",
            name="ck_import_issues_source_row",
        ),
        schema="audit",
    )

    op.create_index(
        "ix_import_issues_batch_severity",
        "import_issues",
        ["import_batch_id", "severity"],
        unique=False,
        schema="audit",
    )
    op.create_index(
        "ix_import_issues_batch_rule",
        "import_issues",
        ["import_batch_id", "rule_code"],
        unique=False,
        schema="audit",
    )

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

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE
            ON audit.import_batches, audit.import_issues
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE, SELECT, UPDATE
            ON SEQUENCE audit.import_issues_id_seq
            TO korporate_app
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_import_issues_batch_rule",
        table_name="import_issues",
        schema="audit",
    )
    op.drop_index(
        "ix_import_issues_batch_severity",
        table_name="import_issues",
        schema="audit",
    )
    op.drop_table("import_issues", schema="audit")

    op.drop_index(
        "ix_import_batches_status_started_at",
        table_name="import_batches",
        schema="audit",
    )
    op.drop_index(
        "uq_import_batches_file_sha256",
        table_name="import_batches",
        schema="audit",
    )
    op.drop_table("import_batches", schema="audit")

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = '0001_platform_skeleton',
                updated_at = now()
            WHERE key = 'schema_revision'
            """
        )
    )
