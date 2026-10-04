"""Business isolation, encrypted integrations and durable Telegram outbox."""
from alembic import op
import sqlalchemy as sa

revision = "0006_businesses"
down_revision = "0005_meta_ads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizations", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("business_integrations",
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("meta_page_id", sa.String(80), nullable=True, unique=True),
        sa.Column("config", sa.JSON(), nullable=False), sa.Column("secrets", sa.Text(), nullable=False))
    op.create_table("telegram_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=False), sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.create_index("ix_telegram_notifications_organization_id", "telegram_notifications", ["organization_id"])
    op.create_index("ix_telegram_notifications_next_attempt_at", "telegram_notifications", ["next_attempt_at"])


def downgrade():
    op.drop_table("telegram_notifications")
    op.drop_table("business_integrations")
    op.drop_column("users", "token_version")
    op.drop_column("organizations", "active")
