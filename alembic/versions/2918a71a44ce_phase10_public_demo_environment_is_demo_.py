"""phase10 public demo environment: is_demo, DEMO_VIEWER role, demo_usage

Revision ID: 2918a71a44ce
Revises: 692395df9cde
Create Date: 2026-09-26

Additive only — nothing here alters or drops existing data:

  * organizations.is_demo  BOOLEAN NOT NULL DEFAULT false. Every existing
    organization (any real tenant) gets false, so this cannot
    change how any real tenant is treated.
  * membership_role gains the DEMO_VIEWER value. On PostgreSQL that needs a
    hand-written ALTER TYPE (Alembic autogenerate never diffs enum members
    — see the Phase 1 migration af9ede758982 for the same pattern). On
    SQLite the enum is a plain VARCHAR with no CHECK constraint and
    "DEMO_VIEWER" (11 chars) fits the existing width, so nothing to do.
  * demo_usage: the public demo's global LLM usage counters. Empty and
    unused on any deployment that does not set LEADLENS_DEMO_MODE.

Production note: apply this to a production database BEFORE deploying code
that includes this revision's model changes to it (the Organization model
now selects is_demo). See docs/V2_DEMO_ENVIRONMENT.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2918a71a44ce'
down_revision: Union[str, Sequence[str], None] = '692395df9cde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE cannot run inside a transaction block on older
        # PostgreSQL versions, hence the autocommit block (same as Phase 1).
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE membership_role ADD VALUE IF NOT EXISTS 'DEMO_VIEWER'")

    op.add_column(
        'organizations',
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'demo_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('window_key', sa.String(length=20), nullable=False),
        sa.Column('scope', sa.String(length=60), nullable=False),
        sa.Column('count', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_demo_usage')),
        sa.UniqueConstraint('window_key', 'scope', name='uq_demo_usage_window_key_scope'),
    )


def downgrade() -> None:
    """Downgrade schema.

    The DEMO_VIEWER enum value is intentionally NOT removed on PostgreSQL:
    PostgreSQL cannot drop a single enum value, and leaving an unused value
    in the type is harmless.
    """
    op.drop_table('demo_usage')
    op.drop_column('organizations', 'is_demo')
