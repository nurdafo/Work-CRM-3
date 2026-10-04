"""Track WhatsApp opening as a contact attempt.

Revision ID: 0003_whatsapp
Revises: 0002_kanban
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_whatsapp"
down_revision = "0002_kanban"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("first_whatsapp_click_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_whatsapp_click_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("whatsapp_click_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("first_response_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("first_response_seconds")
        batch.drop_column("whatsapp_click_count")
        batch.drop_column("last_whatsapp_click_at")
        batch.drop_column("first_whatsapp_click_at")
