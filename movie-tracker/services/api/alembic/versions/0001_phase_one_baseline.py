"""Create Phase 1 migration baseline.

Revision ID: 0001_phase_one_baseline
Revises:
Create Date: 2026-07-17
"""

from collections.abc import Sequence

revision: str = "0001_phase_one_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reserve a tested baseline before domain tables are introduced."""


def downgrade() -> None:
    """The baseline contains no database objects."""
