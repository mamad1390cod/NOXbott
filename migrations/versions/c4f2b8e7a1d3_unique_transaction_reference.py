"""Enforce idempotency for wallet transaction references."""

from alembic import op
import sqlalchemy as sa


revision = "c4f2b8e7a1d3"
down_revision = ("a7b3c9d2e1f4", "dcd011347b60")
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT ref_id, id FROM transactions "
            "WHERE ref_id IS NOT NULL ORDER BY ref_id, created_at, id"
        )
    ).mappings()
    seen: set[str] = set()
    for row in rows:
        ref_id = row["ref_id"]
        if ref_id in seen:
            legacy_ref = f"{ref_id[:48]}-legacy-{str(row['id'])[:8]}"
            bind.execute(
                sa.text(
                    "UPDATE transactions SET ref_id = :legacy_ref WHERE id = :id"
                ),
                {"legacy_ref": legacy_ref, "id": row["id"]},
            )
        else:
            seen.add(ref_id)
    op.create_index(
        "uq_transactions_ref_id",
        "transactions",
        ["ref_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_transactions_ref_id", table_name="transactions")
