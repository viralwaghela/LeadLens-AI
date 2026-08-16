"""phase8 saas onboarding organization settings automations flag

Revision ID: 692395df9cde
Revises: 026598ba5867
Create Date: 2026-08-16 19:01:47.248733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '692395df9cde'
down_revision: Union[str, Sequence[str], None] = '026598ba5867'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default=false so existing rows (there are none live yet —
    # organization_settings is dormant Phase 0 infrastructure, see
    # core/db/models/organization.py — but this keeps the migration safe
    # for any environment that already has rows) get a real value rather
    # than failing the NOT NULL constraint.
    op.add_column(
        'organization_settings',
        sa.Column('automations_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organization_settings', 'automations_enabled')
