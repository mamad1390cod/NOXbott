"""add discount fields to cart

Revision ID: add_discount_to_cart
Revises: add_order_deliveries
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_discount_to_cart'
down_revision = 'add_order_deliveries'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add discount fields to carts table
    op.add_column('carts', sa.Column('discount_code', sa.String(50), nullable=True))
    op.add_column('carts', sa.Column('discount_amount', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    # Remove discount fields from carts table
    op.drop_column('carts', 'discount_amount')
    op.drop_column('carts', 'discount_code')
