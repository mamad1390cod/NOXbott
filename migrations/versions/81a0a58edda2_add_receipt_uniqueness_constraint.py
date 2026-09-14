"""add_receipt_uniqueness_constraint

Revision ID: 81a0a58edda2
Revises: c7f252d69e51
Create Date: 2026-09-14 15:30:59.196413

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81a0a58edda2'
down_revision: Union[str, Sequence[str], None] = 'c7f252d69e51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
