"""Meta form answers, comments and qualified feedback.

Revision ID: 0004_meta_cards
Revises: 0003_whatsapp
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_meta_cards"
down_revision = "0003_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lead_status_history") as batch:
        batch.alter_column("actor_id", existing_type=sa.String(36), nullable=True)
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("email", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("source", sa.String(20), nullable=False, server_default="MANUAL"))
        batch.add_column(sa.Column("meta_lead_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("meta_form_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("meta_page_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("form_answers", sa.JSON(), nullable=False, server_default="[]"))
        batch.create_unique_constraint("uq_leads_meta_lead_id", ["meta_lead_id"])
    op.create_table("lead_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_lead_comments_lead_id", "lead_comments", ["lead_id"])
    op.create_table("meta_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("event_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("lead_id", "event_name", name="uq_meta_feedback_lead_event"))
    op.create_index("ix_meta_feedback_lead_id", "meta_feedback", ["lead_id"])


def downgrade() -> None:
    op.drop_table("meta_feedback")
    op.drop_table("lead_comments")
    with op.batch_alter_table("leads") as batch:
        batch.drop_constraint("uq_leads_meta_lead_id", type_="unique")
        for name in ("form_answers", "meta_page_id", "meta_form_id", "meta_lead_id", "source", "email"):
            batch.drop_column(name)
    with op.batch_alter_table("lead_status_history") as batch:
        batch.alter_column("actor_id", existing_type=sa.String(36), nullable=False)
