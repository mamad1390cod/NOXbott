"""Store the Telegram media type for config deliveries."""

import sqlalchemy as sa
from alembic import op

revision = "8c2d7e4f1a9b"
down_revision = "7b1f4c9d2a6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("order_deliveries")
    }
    if "media_type" not in columns:
        op.add_column(
            "order_deliveries",
            sa.Column(
                "media_type",
                sa.String(length=20),
                nullable=False,
                server_default="document",
            ),
        )
        op.alter_column(
            "order_deliveries",
            "media_type",
            server_default=None,
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("order_deliveries")
    }
    if "media_type" in columns:
        op.drop_column("order_deliveries", "media_type")
