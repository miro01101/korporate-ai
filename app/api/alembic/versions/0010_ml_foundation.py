"""Create machine-learning foundation schema.

Revision ID: 0010_ml_foundation
Revises: 0009_automation_audit
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_ml_foundation"
down_revision: str | None = "0009_automation_audit"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def uuid_column(name: str = "id") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        primary_key=True,
    )


def json_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ml"))

    op.create_table(
        "data_quality_runs",
        uuid_column(),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "quality_version",
            sa.Text(),
            nullable=False,
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
        sa.Column("source_min_date", sa.Date(), nullable=True),
        sa.Column("source_max_date", sa.Date(), nullable=True),
        sa.Column(
            "dataset_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "issue_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "critical_count",
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
        json_column("metadata"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ml_data_quality_runs_status",
        ),
        sa.CheckConstraint(
            """
            dataset_fingerprint IS NULL
            OR dataset_fingerprint ~ '^[0-9a-f]{64}$'
            """,
            name="ck_ml_data_quality_runs_fingerprint",
        ),
        sa.CheckConstraint(
            """
            finished_at IS NULL
            OR finished_at >= started_at
            """,
            name="ck_ml_data_quality_runs_time_order",
        ),
        sa.CheckConstraint(
            """
            issue_count >= 0
            AND critical_count >= 0
            AND warning_count >= 0
            """,
            name="ck_ml_data_quality_runs_counts",
        ),
        schema="ml",
    )

    op.create_table(
        "data_quality_issues",
        uuid_column(),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("check_code", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("column_name", sa.Text(), nullable=True),
        json_column("observed_value"),
        sa.Column("expected_rule", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ml.data_quality_runs.id"],
            name="fk_ml_quality_issues_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'info')",
            name="ck_ml_quality_issues_severity",
        ),
        schema="ml",
    )

    op.create_index(
        "ix_ml_quality_issues_run_severity",
        "data_quality_issues",
        ["run_id", "severity"],
        schema="ml",
    )
    op.create_index(
        "ix_ml_quality_issues_entity",
        "data_quality_issues",
        ["entity_type", "entity_id"],
        schema="ml",
    )

    op.create_table(
        "feature_runs",
        uuid_column(),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "feature_version",
            sa.Text(),
            nullable=False,
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
        sa.Column("source_min_month", sa.Date(), nullable=True),
        sa.Column("source_max_month", sa.Date(), nullable=True),
        sa.Column(
            "product_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "row_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "dataset_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        json_column("metadata"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ml_feature_runs_status",
        ),
        sa.CheckConstraint(
            "product_count >= 0 AND row_count >= 0",
            name="ck_ml_feature_runs_counts",
        ),
        sa.CheckConstraint(
            """
            dataset_fingerprint IS NULL
            OR dataset_fingerprint ~ '^[0-9a-f]{64}$'
            """,
            name="ck_ml_feature_runs_fingerprint",
        ),
        schema="ml",
    )

    op.create_table(
        "product_monthly_features",
        sa.Column(
            "feature_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("units_sold", sa.BigInteger(), nullable=False),
        sa.Column(
            "revenue",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column(
            "gross_profit",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
        ),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("customer_count", sa.Integer(), nullable=False),
        sa.Column("zero_demand", sa.Boolean(), nullable=False),
        sa.Column(
            "lag_1",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "lag_2",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "lag_3",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "lag_6",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "lag_12",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "rolling_mean_3",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "rolling_mean_6",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "rolling_mean_12",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "rolling_std_3",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "rolling_std_6",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
        ),
        sa.Column(
            "zero_ratio_12",
            sa.Numeric(precision=9, scale=6),
            nullable=True,
        ),
        sa.Column(
            "demand_cv_12",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
        sa.Column(
            "months_since_last_sale",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("stock_actual", sa.Integer(), nullable=True),
        sa.Column("stock_reserved", sa.Integer(), nullable=True),
        sa.Column("stock_available", sa.Integer(), nullable=True),
        sa.Column("min_stock", sa.Integer(), nullable=True),
        sa.Column("max_stock", sa.Integer(), nullable=True),
        sa.Column(
            "purchase_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "sales_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column(
            "minimum_order_quantity",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("abc_class", sa.String(length=1), nullable=False),
        sa.Column("xyz_class", sa.String(length=1), nullable=False),
        sa.Column("is_cold_start", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["feature_run_id"],
            ["ml.feature_runs.id"],
            name="fk_ml_product_features_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_ml_product_features_product",
        ),
        sa.PrimaryKeyConstraint(
            "feature_run_id",
            "product_id",
            "month_start",
            name="pk_ml_product_monthly_features",
        ),
        sa.CheckConstraint(
            "units_sold >= 0",
            name="ck_ml_product_features_units",
        ),
        sa.CheckConstraint(
            "abc_class IN ('A', 'B', 'C')",
            name="ck_ml_product_features_abc",
        ),
        sa.CheckConstraint(
            "xyz_class IN ('X', 'Y', 'Z')",
            name="ck_ml_product_features_xyz",
        ),
        schema="ml",
    )

    op.create_index(
        "ix_ml_product_features_product_month",
        "product_monthly_features",
        ["product_id", "month_start"],
        schema="ml",
    )
    op.create_index(
        "ix_ml_product_features_segment",
        "product_monthly_features",
        ["abc_class", "xyz_class", "month_start"],
        schema="ml",
    )

    op.create_table(
        "model_runs",
        uuid_column(),
        sa.Column(
            "feature_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_family", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("training_cutoff", sa.Date(), nullable=False),
        sa.Column(
            "forecast_horizon_months",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("code_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "dataset_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        json_column("parameters"),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column(
            "artifact_sha256",
            sa.String(length=64),
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
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["feature_run_id"],
            ["ml.feature_runs.id"],
            name="fk_ml_model_runs_feature_run",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_ml_model_runs_status",
        ),
        sa.CheckConstraint(
            "forecast_horizon_months BETWEEN 1 AND 24",
            name="ck_ml_model_runs_horizon",
        ),
        schema="ml",
    )

    op.create_table(
        "model_metrics",
        uuid_column(),
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column(
            "metric_value",
            sa.Numeric(precision=20, scale=8),
            nullable=False,
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("fold_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml.model_runs.id"],
            name="fk_ml_model_metrics_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_ml_model_metrics_product",
        ),
        sa.CheckConstraint(
            "horizon >= 1 AND sample_size >= 0 AND fold_count >= 0",
            name="ck_ml_model_metrics_counts",
        ),
        schema="ml",
    )

    op.create_index(
        "ix_ml_model_metrics_run_product",
        "model_metrics",
        ["model_run_id", "product_id"],
        schema="ml",
    )

    op.create_table(
        "forecasts",
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("forecast_month", sa.Date(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column(
            "forecast_p10",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "forecast_p50",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "forecast_p90",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column("selected_model", sa.Text(), nullable=False),
        sa.Column("is_cold_start", sa.Boolean(), nullable=False),
        sa.Column(
            "backtest_wape",
            sa.Numeric(precision=12, scale=8),
            nullable=True,
        ),
        sa.Column(
            "backtest_bias",
            sa.Numeric(precision=12, scale=8),
            nullable=True,
        ),
        sa.Column(
            "confidence_score",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml.model_runs.id"],
            name="fk_ml_forecasts_model_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_ml_forecasts_product",
        ),
        sa.PrimaryKeyConstraint(
            "model_run_id",
            "product_id",
            "forecast_month",
            name="pk_ml_forecasts",
        ),
        sa.CheckConstraint(
            """
            forecast_p10 >= 0
            AND forecast_p50 >= forecast_p10
            AND forecast_p90 >= forecast_p50
            """,
            name="ck_ml_forecasts_quantiles",
        ),
        sa.CheckConstraint(
            "confidence_score BETWEEN 0 AND 1",
            name="ck_ml_forecasts_confidence",
        ),
        schema="ml",
    )

    op.create_index(
        "ix_ml_forecasts_product_month",
        "forecasts",
        ["product_id", "forecast_month"],
        schema="ml",
    )

    op.create_table(
        "inventory_risk",
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("stock_available", sa.Integer(), nullable=False),
        sa.Column("incoming_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "expected_lead_time_demand",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "safety_stock",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "reorder_point",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
        ),
        sa.Column(
            "stockout_probability_30d",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "stockout_probability_60d",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "stockout_probability_90d",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "overstock_probability_90d",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "recommended_order_quantity",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "recommended_order_date",
            sa.Date(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml.model_runs.id"],
            name="fk_ml_inventory_risk_model_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_ml_inventory_risk_product",
        ),
        sa.PrimaryKeyConstraint(
            "model_run_id",
            "product_id",
            "as_of_date",
            name="pk_ml_inventory_risk",
        ),
        sa.CheckConstraint(
            """
            stockout_probability_30d BETWEEN 0 AND 1
            AND stockout_probability_60d BETWEEN 0 AND 1
            AND stockout_probability_90d BETWEEN 0 AND 1
            AND overstock_probability_90d BETWEEN 0 AND 1
            """,
            name="ck_ml_inventory_risk_probabilities",
        ),
        sa.CheckConstraint(
            "recommended_order_quantity >= 0",
            name="ck_ml_inventory_risk_quantity",
        ),
        schema="ml",
    )

    op.create_table(
        "recommendations",
        uuid_column(),
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column(
            "recommendation_type",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column(
            "recommended_quantity",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("recommended_date", sa.Date(), nullable=True),
        sa.Column(
            "expected_value_eur",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
        ),
        sa.Column(
            "risk_if_ignored_eur",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
        ),
        sa.Column(
            "confidence",
            sa.Numeric(precision=9, scale=6),
            nullable=False,
        ),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
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
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["ml.model_runs.id"],
            name="fk_ml_recommendations_model_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["core.products.product_id"],
            name="fk_ml_recommendations_product",
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 100",
            name="ck_ml_recommendations_priority",
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1",
            name="ck_ml_recommendations_confidence",
        ),
        sa.CheckConstraint(
            """
            status IN (
                'pending',
                'accepted',
                'rejected',
                'executed',
                'expired'
            )
            """,
            name="ck_ml_recommendations_status",
        ),
        schema="ml",
    )

    op.create_index(
        "ix_ml_recommendations_status_priority",
        "recommendations",
        ["status", "priority", "created_at"],
        schema="ml",
    )
    op.create_index(
        "ix_ml_recommendations_product",
        "recommendations",
        ["product_id", "created_at"],
        schema="ml",
    )

    op.execute(
        sa.text(
            """
            GRANT USAGE ON SCHEMA ml TO korporate_app;

            GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA ml
            TO korporate_app;
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE meta.system_info
            SET value = CASE key
                    WHEN 'platform_version' THEN '0.5.0'
                    WHEN 'schema_revision'
                        THEN '0010_ml_foundation'
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
    op.drop_table("recommendations", schema="ml")
    op.drop_table("inventory_risk", schema="ml")
    op.drop_table("forecasts", schema="ml")
    op.drop_table("model_metrics", schema="ml")
    op.drop_table("model_runs", schema="ml")
    op.drop_table("product_monthly_features", schema="ml")
    op.drop_table("feature_runs", schema="ml")
    op.drop_table("data_quality_issues", schema="ml")
    op.drop_table("data_quality_runs", schema="ml")

    # The ml schema predates this revision and contains
    # schema-level ACL and default privileges. Revision 0010
    # owns only the tables created above, not the namespace.
    # Therefore downgrade intentionally preserves schema ml.

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
