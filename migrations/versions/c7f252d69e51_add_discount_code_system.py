"""add_discount_code_system

Revision ID: c7f252d69e51
Revises: 8c2d7e4f1a9b
Create Date: 2026-09-14 12:08:40.781421

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f252d69e51'
down_revision: Union[str, Sequence[str], None] = '8c2d7e4f1a9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create discount_codes table
    op.create_table(
        'discount_codes',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('discount_type', sa.Enum('PERCENTAGE', 'FIXED', name='discounttype'), nullable=False),
        sa.Column('discount_value', sa.Integer(), nullable=False),
        sa.Column('max_eligible_amount', sa.BigInteger(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_discount_codes_code')
    )
    op.create_index(op.f('ix_discount_codes_code'), 'discount_codes', ['code'], unique=True)
    op.create_index(op.f('ix_discount_codes_is_active'), 'discount_codes', ['is_active'], unique=False)
    
    # Add discount fields to carts table
    op.add_column('carts', sa.Column('discount_code', sa.String(length=50), nullable=True))
    op.add_column('carts', sa.Column('discount_amount', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove discount fields from carts table
    op.drop_column('carts', 'discount_amount')
    op.drop_column('carts', 'discount_code')
    
    # Drop discount_codes table
    op.drop_index(op.f('ix_discount_codes_is_active'), table_name='discount_codes')
    op.drop_index(op.f('ix_discount_codes_code'), table_name='discount_codes')
    op.drop_table('discount_codes')
