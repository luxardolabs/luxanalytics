"""Rename pre-convention primary keys to the fleet naming convention.

The models now carry the canonical MetaData naming_convention (luxarch --emit naming-convention).
Alembic applies it to every op, so a database built from empty already has pk_<table>. A database
created before it (prod) has Postgres's default <table>_pkey. Name-only, and a no-op where the
convention name is already in place. Indexes are explicitly named and already match.

Revision ID: 006_naming_convention_pks
Revises: 005_drop_timestamp_indexes
Create Date: 2026-10-07 04:10:00.000000
"""

from alembic import op

revision = "006_naming_convention_pks"
down_revision = "005_drop_timestamp_indexes"
branch_labels = None
depends_on = None

_TABLES = ("apps", "devices", "events")


def _rename(table: str, old: str, new: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{old}'
                       AND conrelid = 'public.{table}'::regclass) THEN
                ALTER TABLE public.{table} RENAME CONSTRAINT {old} TO {new};
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for table in _TABLES:
        _rename(table, f"{table}_pkey", f"pk_{table}")


def downgrade() -> None:
    for table in _TABLES:
        _rename(table, f"pk_{table}", f"{table}_pkey")
