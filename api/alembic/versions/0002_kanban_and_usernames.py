"""Usernames, project removal, leads and kanban history.

Revision ID: 0002_kanban
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_kanban"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(80), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, email, role FROM users ORDER BY created_at, id")).all()
    used: set[str] = set()
    for user_id, email, role in rows:
        base = "admin" if role == "ADMIN" and "admin" not in used else (email.split("@")[0].lower() or "user")
        base = "".join(character if character.isalnum() or character in "_.-" else "_" for character in base)[:65] or "user"
        candidate = base
        number = 2
        while candidate in used:
            candidate = f"{base}_{number}"
            number += 1
        used.add(candidate)
        connection.execute(sa.text("UPDATE users SET username = :username WHERE id = :id"), {"username": candidate, "id": user_id})
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", nullable=False)
        batch.drop_constraint("uq_users_email", type_="unique")
        batch.create_unique_constraint("uq_users_username", ["username"])
        batch.drop_column("email")
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("company_name", sa.String(180), nullable=False),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("requested_service", sa.String(24), nullable=False),
        sa.Column("monthly_ad_budget", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(8), nullable=False),
        sa.Column("low_budget", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("won_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_leads_project_id", "leads", ["project_id"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])
    op.create_table("lead_status_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("old_status", sa.String(24), nullable=True),
        sa.Column("new_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_lead_status_history_lead_id", "lead_status_history", ["lead_id"])


def downgrade() -> None:
    op.drop_table("lead_status_history")
    op.drop_table("leads")
    op.drop_column("projects", "deleted_at")
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    connection = op.get_bind()
    for user_id, username in connection.execute(sa.text("SELECT id, username FROM users")).all():
        connection.execute(sa.text("UPDATE users SET email = :email WHERE id = :id"),
                           {"email": f"{username}@local.ads.kz", "id": user_id})
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", nullable=False)
        batch.drop_constraint("uq_users_username", type_="unique")
        batch.create_unique_constraint("uq_users_email", ["email"])
        batch.drop_column("username")
