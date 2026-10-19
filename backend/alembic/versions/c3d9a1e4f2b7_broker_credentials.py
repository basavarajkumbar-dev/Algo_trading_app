"""user-entered broker credentials (encrypted)

Revision ID: c3d9a1e4f2b7
Revises: 27ff6be8838a
Create Date: 2026-10-20 01:30:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d9a1e4f2b7"
down_revision: Union[str, None] = "27ff6be8838a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broker_credentials",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("broker", sa.String(length=20), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(length=8), nullable=False),
        sa.Column("api_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "broker", name="uq_broker_credentials_user"),
    )
    op.create_index(
        op.f("ix_broker_credentials_user_id"), "broker_credentials", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_broker_credentials_user_id"), table_name="broker_credentials")
    op.drop_table("broker_credentials")
