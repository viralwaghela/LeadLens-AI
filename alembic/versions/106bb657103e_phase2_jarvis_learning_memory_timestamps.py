"""phase2 jarvis learning memory timestamps

Revision ID: 106bb657103e
Revises: af9ede758982
Create Date: 2026-08-16 00:11:13.402665

Renames jarvis_learning_records.recorded_at -> created_at and adds
updated_at, matching core/db/models/jarvis.py's Phase 2 shape (one
mutable timestamp for "first written", one for "last changed" — needed
now that Phase 2 makes this table genuinely live-written, with rows
that get updated in place by services/jarvis_memory.py's upsert logic).

Safe against a non-empty table (defensive only — this table has had
zero live writers before Phase 2, confirmed via the Phase 0/1 audits,
so no production deployment should actually have existing rows here):
on Postgres, the two new NOT NULL columns get a server-side default of
"now" for the ADD COLUMN step, then the default is dropped immediately
after so the resulting schema exactly matches the model (which has no
server default — every write sets both timestamps explicitly). SQLite
doesn't support ALTER COLUMN ... DROP DEFAULT (or a superfluous
constant default on an always-empty local/test table), so on that
dialect the columns are simply added NOT NULL with no default — safe
because SQLite deployments of this schema are dev/test-only and this
table is created empty every time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '106bb657103e'
down_revision: Union[str, Sequence[str], None] = 'af9ede758982'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    default = sa.func.now() if is_postgres else None

    op.add_column(
        'jarvis_learning_records',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=default),
    )
    op.add_column(
        'jarvis_learning_records',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=default),
    )
    if is_postgres:
        op.alter_column('jarvis_learning_records', 'created_at', server_default=None)
        op.alter_column('jarvis_learning_records', 'updated_at', server_default=None)
    op.drop_index(op.f('ix_jarvis_learning_records_recorded_at'), table_name='jarvis_learning_records')
    op.create_index(op.f('ix_jarvis_learning_records_created_at'), 'jarvis_learning_records', ['created_at'], unique=False)
    op.drop_column('jarvis_learning_records', 'recorded_at')


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    default = sa.func.now() if is_postgres else None

    op.add_column(
        'jarvis_learning_records',
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False, server_default=default),
    )
    if is_postgres:
        op.alter_column('jarvis_learning_records', 'recorded_at', server_default=None)
    op.drop_index(op.f('ix_jarvis_learning_records_created_at'), table_name='jarvis_learning_records')
    op.create_index(op.f('ix_jarvis_learning_records_recorded_at'), 'jarvis_learning_records', ['recorded_at'], unique=False)
    op.drop_column('jarvis_learning_records', 'updated_at')
    op.drop_column('jarvis_learning_records', 'created_at')
