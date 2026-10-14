"""Reject PostgreSQL numeric NaN independently of application validation."""

from alembic import op

revision = "m02_002"
down_revision = "m02_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "ck_price_finite", "part_price", "price_usd IS NULL OR price_usd <> 'NaN'::numeric"
    )


def downgrade():
    op.drop_constraint("ck_price_finite", "part_price", type_="check")
