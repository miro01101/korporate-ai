"""Add Google Drive pipeline audit and set platform version 0.4.0.

Revision ID: 0009_automation_audit
Revises: 0008_platform_version
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_automation_audit"
down_revision: str | None = "0008_platform_version"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "trigger_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'google_drive_timer'"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "attempt_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "google_file_id",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "source_filename",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "source_modified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "source_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "import_batch_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "mart_refresh_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
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
        sa.Column(
            "alert_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "error_stage",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["audit.import_batches.id"],
            name="fk_pipeline_runs_import_batch",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mart_refresh_run_id"],
            ["mart.refresh_runs.id"],
            name="fk_pipeline_runs_mart_refresh",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            """
            trigger_type IN (
                'google_drive_timer',
                'manual_cli'
            )
            """,
            name="ck_pipeline_runs_trigger_type",
        ),
        sa.CheckConstraint(
            """
            status IN (
                'running',
                'completed',
                'failed',
                'rejected',
                'skipped_duplicate',
                'no_file'
            )
            """,
            name="ck_pipeline_runs_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_pipeline_runs_attempt_number",
        ),
        sa.CheckConstraint(
            """
            source_sha256 IS NULL
            OR source_sha256 ~ '^[0-9a-f]{64}$'
            """,
            name="ck_pipeline_runs_sha256",
        ),
        sa.CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= started_at
            """,
            name="ck_pipeline_runs_time_order",
        ),
        sa.CheckConstraint(
            """
            (
                status = 'running'
                AND finished_at IS NULL
            )
            OR
            (
                status <> 'running'
                AND finished_at IS NOT NULL
            )
            """,
            name="ck_pipeline_runs_finished_state",
        ),
        schema="audit",
    )

    op.create_index(
        "ix_pipeline_runs_status_started_at",
        "pipeline_runs",
        ["status", "started_at"],
        unique=False,
        schema="audit",
    )

    op.create_index(
        "ix_pipeline_runs_google_file_modified",
        "pipeline_runs",
        ["google_file_id", "source_modified_at"],
        unique=False,
        schema="audit",
    )

    op.create_index(
        "ix_pipeline_runs_source_sha256",
        "pipeline_runs",
        ["source_sha256"],
        unique=False,
        schema="audit",
    )

    op.create_index(
        "uq_pipeline_runs_terminal_file_version",
        "pipeline_runs",
        ["google_file_id", "source_modified_at"],
        unique=True,
        schema="audit",
        postgresql_where=sa.text(
            """
            google_file_id IS NOT NULL
            AND source_modified_at IS NOT NULL
            AND status IN (
                'completed',
                'rejected',
                'skipped_duplicate'
            )
            """
        ),
    )

    op.execute(
        sa.text(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE
            ON TABLE audit.pipeline_runs
            TO korporate_app
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = CASE key
                    WHEN 'platform_version' THEN '0.4.0'
                    WHEN 'schema_revision'
                        THEN '0009_automation_audit'
                    ELSE value
                END,
                updated_at = now()
            WHERE key IN (
                'platform_version',
                'schema_revision'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pipeline_runs_terminal_file_version",
        table_name="pipeline_runs",
        schema="audit",
    )

    op.drop_index(
        "ix_pipeline_runs_source_sha256",
        table_name="pipeline_runs",
        schema="audit",
    )

    op.drop_index(
        "ix_pipeline_runs_google_file_modified",
        table_name="pipeline_runs",
        schema="audit",
    )

    op.drop_index(
        "ix_pipeline_runs_status_started_at",
        table_name="pipeline_runs",
        schema="audit",
    )

    op.drop_table(
        "pipeline_runs",
        schema="audit",
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = CASE key
                    WHEN 'platform_version' THEN '0.3.0'
                    WHEN 'schema_revision'
                        THEN '0008_platform_version'
                    ELSE value
                END,
                updated_at = now()
            WHERE key IN (
                'platform_version',
                'schema_revision'
            )
            """
        )
    )
