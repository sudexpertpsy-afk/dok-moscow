"""W-39: воронка заявок — статусы, связи, бета-defaults

Revision ID: s9h0c1d2e3f4
Revises: r8g9b0c1d2e3
Create Date: 2026-08-02 09:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s9h0c1d2e3f4"
down_revision: str | None = "r8g9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "invited",
                "registered",
                "rejected",
                "spam",
                name="lead_status",
                native_enum=False,
            ),
            nullable=False,
            server_default="new",
        ),
    )
    op.create_index("ix_leads_status", "leads", ["status"])
    op.add_column("leads", sa.Column("admin_note", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column("contact_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("leads", sa.Column("org_id", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("invite_id", sa.Integer(), nullable=True))
    op.add_column(
        "leads", sa.Column("admin_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "leads",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_leads_org_id", "leads", ["org_id"])
    op.create_index("ix_leads_invite_id", "leads", ["invite_id"])
    op.create_foreign_key(
        "fk_leads_org_id_organizations",
        "leads",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_leads_invite_id_invites",
        "leads",
        "invites",
        ["invite_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("invites", sa.Column("lead_id", sa.Integer(), nullable=True))
    op.create_index("ix_invites_lead_id", "invites", ["lead_id"])
    op.create_foreign_key(
        "fk_invites_lead",
        "invites",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("organizations", sa.Column("source_lead_id", sa.Integer(), nullable=True))
    op.create_index("ix_organizations_source_lead_id", "organizations", ["source_lead_id"])
    op.create_foreign_key(
        "fk_orgs_source_lead",
        "organizations",
        "leads",
        ["source_lead_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "payment_settings",
        sa.Column(
            "beta_default_tariff",
            sa.String(length=32),
            nullable=False,
            server_default="organization",
        ),
    )
    op.add_column(
        "payment_settings",
        sa.Column(
            "beta_default_months",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )

    op.alter_column("leads", "status", server_default=None)
    op.alter_column("leads", "contact_count", server_default=None)
    op.alter_column("leads", "updated_at", server_default=None)
    op.alter_column("payment_settings", "beta_default_tariff", server_default=None)
    op.alter_column("payment_settings", "beta_default_months", server_default=None)


def downgrade() -> None:
    op.drop_column("payment_settings", "beta_default_months")
    op.drop_column("payment_settings", "beta_default_tariff")

    op.drop_constraint("fk_orgs_source_lead", "organizations", type_="foreignkey")
    op.drop_index("ix_organizations_source_lead_id", table_name="organizations")
    op.drop_column("organizations", "source_lead_id")

    op.drop_constraint("fk_invites_lead", "invites", type_="foreignkey")
    op.drop_index("ix_invites_lead_id", table_name="invites")
    op.drop_column("invites", "lead_id")

    op.drop_constraint("fk_leads_invite_id_invites", "leads", type_="foreignkey")
    op.drop_constraint("fk_leads_org_id_organizations", "leads", type_="foreignkey")
    op.drop_index("ix_leads_invite_id", table_name="leads")
    op.drop_index("ix_leads_org_id", table_name="leads")
    op.drop_index("ix_leads_status", table_name="leads")
    op.drop_column("leads", "updated_at")
    op.drop_column("leads", "admin_notified_at")
    op.drop_column("leads", "invite_id")
    op.drop_column("leads", "org_id")
    op.drop_column("leads", "contact_count")
    op.drop_column("leads", "admin_note")
    op.drop_column("leads", "status")
