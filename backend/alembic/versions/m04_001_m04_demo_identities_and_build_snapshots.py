"""M04 demo identities and build snapshots

Revision ID: m04_001
Revises: m02_002
"""

from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m04_001"
down_revision = "m02_002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_price_id_part", "part_price", ["id", "part_id"])
    op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "build",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("budget_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("budget_currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "budget_amount IS NULL OR (budget_amount > 0 AND budget_amount <> 'NaN'::numeric)"
        ),
        sa.CheckConstraint("budget_currency = 'USD'"),
        sa.CheckConstraint("status IN ('draft','archived')"),
        sa.CheckConstraint("version >= 1"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user_account.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_build_owner", "build", ["tenant_id", "owner_user_id", "status"], unique=False
    )
    op.create_table(
        "build_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user_account.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "request_key"),
    )
    op.create_table(
        "build_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("build_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(length=30), nullable=False),
        sa.Column("part_id", sa.Uuid(), nullable=False),
        sa.Column("part_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("part_price_id", sa.Uuid(), nullable=True),
        sa.Column("price_snapshot_usd", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("snapshot_currency", sa.String(length=3), nullable=True),
        sa.Column("price_source", sa.Text(), nullable=True),
        sa.Column("price_dataset_updated_at", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_snapshot_usd IS NULL OR (price_snapshot_usd > 0 AND "
            "price_snapshot_usd <> 'NaN'::numeric)"
        ),
        sa.CheckConstraint(
            "slot IN ('CPU','GPU','MOTHERBOARD','MEMORY','STORAGE','PSU','CASE','COOLER')"
        ),
        sa.CheckConstraint("snapshot_currency IS NULL OR snapshot_currency = 'USD'"),
        sa.CheckConstraint(
            "(price_snapshot_usd IS NULL AND part_price_id IS NULL AND "
            "snapshot_currency IS NULL AND price_source IS NULL AND "
            "price_dataset_updated_at IS NULL) OR (price_snapshot_usd IS NOT NULL AND "
            "part_price_id IS NOT NULL AND snapshot_currency IS NOT NULL AND "
            "price_source IS NOT NULL AND price_dataset_updated_at IS NOT NULL)"
        ),
        sa.CheckConstraint("quantity BETWEEN 1 AND 99"),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["build.id"],
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["hardware_part.id"],
        ),
        sa.ForeignKeyConstraint(
            ["part_price_id", "part_id"], ["part_price.id", "part_price.part_id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_id", "slot"),
    )
    op.create_index(op.f("ix_build_item_build_id"), "build_item", ["build_id"], unique=False)
    op.bulk_insert(
        sa.table(
            "tenant",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.Text()),
            sa.column("status", sa.String()),
        ),
        [
            {
                "id": UUID("00000000-0000-4000-8000-000000000001"),
                "name": "Local Demo",
                "status": "demo",
            }
        ],
    )
    op.bulk_insert(
        sa.table(
            "user_account",
            sa.column("id", sa.Uuid()),
            sa.column("username", sa.String()),
            sa.column("status", sa.String()),
        ),
        [
            {
                "id": UUID("00000000-0000-4000-8000-000000000002"),
                "username": "local-demo",
                "status": "demo",
            }
        ],
    )


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_build_item_build_id"), table_name="build_item")
    op.drop_table("build_item")
    op.drop_table("build_request")
    op.drop_index("ix_build_owner", table_name="build")
    op.drop_table("build")
    op.drop_table("user_account")
    op.drop_table("tenant")
    op.drop_constraint("uq_price_id_part", "part_price", type_="unique")
    # ### end Alembic commands ###
