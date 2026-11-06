"""Drop five server defaults the chain set and the models do not declare.

Every write goes through the models, whose defaults run in Python: `App` by the ORM, `Event` and
`Device` by `pg_insert` on the model. So no write relies on these, and the chain now builds what the
models describe. Catalog-only: no table is rewritten, and each statement holds its lock only for the
catalog update.

Downgrade puts the five defaults back.

Revision ID: 012_drop_chain_only_defaults
Revises: 011_dashboard_session_state
Create Date: 2026-11-06 17:30:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "012_drop_chain_only_defaults"
down_revision = "011_dashboard_session_state"
branch_labels = None
depends_on = None

DEFAULTS = (
    ("apps", "app_metadata", "'{}'::jsonb"),
    ("apps", "created_at", "now()"),
    ("apps", "updated_at", "now()"),
    ("devices", "platform", "'ios'"),
    ("events", "platform", "'ios'"),
)


def upgrade() -> None:
    for table, column, _default in DEFAULTS:
        op.alter_column(table, column, server_default=None)


def downgrade() -> None:
    for table, column, default in DEFAULTS:
        op.alter_column(table, column, server_default=sa.text(default))
