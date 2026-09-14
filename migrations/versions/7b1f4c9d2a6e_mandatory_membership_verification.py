"""Persist mandatory-membership verification per user."""

import sqlalchemy as sa

from alembic import op

revision = "7b1f4c9d2a6e"
down_revision = "c4f2b8e7a1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "mandatory_membership_verified" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }:
        op.add_column(
            "users",
            sa.Column(
                "mandatory_membership_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.alter_column(
            "users",
            "mandatory_membership_verified",
            server_default=None,
        )


def downgrade() -> None:
    if "mandatory_membership_verified" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }:
        op.drop_column("users", "mandatory_membership_verified")
