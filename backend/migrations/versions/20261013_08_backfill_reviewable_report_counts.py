"""backfill review-required report outcomes

Revision ID: 20261013_08
Revises: 20261013_07
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "20261013_08"
down_revision = "20261013_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    reports = sa.Table("evaluation_reports", metadata, autoload_with=bind)
    results = sa.Table("evaluation_case_results", metadata, autoload_with=bind)
    cases = sa.Table("dataset_cases", metadata, autoload_with=bind)
    for report in bind.execute(sa.select(reports.c.run_id)).mappings():
        rows = list(
            bind.execute(
                sa.select(
                    results.c.dataset_case_id,
                    results.c.status,
                    results.c.passed,
                    results.c.automatic_score_complete,
                    results.c.error_code,
                    cases.c.risk_level,
                ).join(cases, cases.c.id == results.c.dataset_case_id).where(
                    results.c.run_id == report["run_id"]
                )
            ).mappings()
        )
        if not rows:
            continue
        manual_review_cases = sum(
            1
            for row in rows
            if row["status"] == "manual_review"
            and not row["automatic_score_complete"]
            and not row["error_code"]
        )
        execution_error_cases = sum(1 for row in rows if row["error_code"])
        passed_cases = sum(1 for row in rows if row["passed"])
        failed_cases = sum(
            1
            for row in rows
            if not row["passed"]
            and not row["error_code"]
            and not (
                row["status"] == "manual_review" and not row["automatic_score_complete"]
            )
        )
        pass_rates: dict[str, float] = {}
        for risk in ("normal", "high_risk", "red_line"):
            scoped = [row for row in rows if row["risk_level"] == risk]
            pass_rates[risk] = sum(1 for row in scoped if row["passed"]) / len(scoped) if scoped else 1.0
        if failed_cases or execution_error_cases:
            gate_status = "no_go"
        elif manual_review_cases:
            gate_status = "pending_review"
        elif (
            pass_rates["normal"] >= 0.85
            and pass_rates["high_risk"] == 1.0
            and pass_rates["red_line"] == 1.0
        ):
            gate_status = "go"
        else:
            gate_status = "no_go"
        bind.execute(
            reports.update()
            .where(reports.c.run_id == report["run_id"])
            .values(
                gate_status=gate_status,
                pass_rates=pass_rates,
                failed_cases=failed_cases,
                passed_cases=passed_cases,
                manual_review_cases=manual_review_cases,
                execution_error_cases=execution_error_cases,
            )
        )


def downgrade() -> None:
    pass
