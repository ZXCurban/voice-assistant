"""Add optional geo fields to clinics (city + coordinates).

Additive only: all columns nullable, no data backfill here (seed_demo.py
owns demo values). Lets the assistant rank "nearest clinic" by the user's
city/address without breaking existing rows or API contracts.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_clinic_geo"
down_revision: str | None = "0001_multi_clinic_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clinics", sa.Column("city", sa.String(length=200), nullable=True))
    op.add_column("clinics", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("clinics", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("clinics", "longitude")
    op.drop_column("clinics", "latitude")
    op.drop_column("clinics", "city")
