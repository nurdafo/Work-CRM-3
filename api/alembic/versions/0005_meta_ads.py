"""Meta advertisement identity and label.

Revision ID: 0005_meta_ads
Revises: 0004_meta_cards
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_meta_ads"
down_revision = "0004_meta_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("meta_ad_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("meta_ad_name", sa.String(180), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("meta_ad_name")
        batch.drop_column("meta_ad_id")
