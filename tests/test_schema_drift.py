# luxarch:schema-drift asserter v6 — DO NOT edit the marker line above.
#
# WHY THIS FILE EXISTS
# The guards are STATIC — luxarch/luxlint/luxaudit read files; none of them ever opens your database.
# So the one thing they structurally cannot see is whether your ORM models still match the schema that
# is actually in the DB. A repo can be fully onboarded, every guard green, every scan honest — and be
# one `alembic revision --autogenerate` away from a migration that DROPS tables, columns and indexes,
# because that drift lives in the database, not the AST. Nothing lied; the boundary was never the
# guards' to check. (one repo hit exactly this: 113 pending autogenerate ops, 42 destructive — 35
# index drops, 2 tables, a column, an FK, 3 constraints — accumulated under a green onboard-check.)
#
# WHAT THIS DOES
# Diffs your `Base.metadata` against the LIVE schema (alembic's `compare_metadata`) and FAILS ONLY on
# DESTRUCTIVE operations — a drop of a table / column / index / constraint / foreign key. Additive
# (`add_*`) and in-place modifications (`modify_type`/`modify_nullable`/`modify_default`) are REPORTED,
# not failed. Cosmetic `modify_comment` diffs are ignored entirely — a check that fails on cosmetics is
# one people learn to ignore, and in a generated migration those cosmetic lines pad the file so the
# real drops don't stand out on review. Binary and un-gameable: the FACT that the DB and the models
# disagree DESTRUCTIVELY, not a static guess.
#
# WHY IT OWNS ITS OWN ENGINE (do not "reuse the app engine")
# It builds a short-lived engine from a URL and disposes it INSIDE its own event loop. That is
# deliberate: reusing the app's pooled AsyncEngine here leaks a connection onto the loop this test
# opens and closes (`Event loop is closed` / `Future attached to a different loop`), and the usual
# workaround — a dedicated NullPool engine — then trips `fw.dev_prod_pool_parity`. Owning
# the lifecycle end-to-end needs no NullPool and touches no shared pool, so no adopting repo hits that.
#
# WHAT THIS DOES *NOT* DO (use `--emit migration-chain` for that)
# This validates a LIVE schema against the models; it does NOT prove the migration CHAIN builds that
# schema from empty. A baseline that omits three tables reports `add_table` — ADDITIVE — which this waves
# through, so a chain broken on its first revision passes here. And do NOT point this at
# the create_all-built test DB the suite uses: both sides then derive from the same metadata, so it is
# near-vacuous. "Does the chain BUILD the models, from empty?" is a different question with a different
# failure policy (fail on ANY structural diff) — that is `luxarch --emit migration-chain`, gated by
# repo.migrations_build_schema.
#
# HOW TO WIRE IT (see: FLEET-ONBOARDING-STANDARD "the static/runtime boundary")
#   1) Save as tests/test_schema_drift.py (or paste into an existing DB-backed test module).
#   2) Point DB_URL_IMPORT at your test database URL, and METADATA_IMPORT at the package that imports
#      EVERY model (so Base.metadata is complete — see the note on each below).
#   3) Run it against a DB migrated to HEAD — a REAL migrated DB, not the create_all test DB (see above).
#      Otherwise it reports the un-applied migrations themselves as drift (a real fact, but a different one).
#   4) Keep it in the suite CI runs. luxarch can EMIT this file; it can never RUN it (it has no DB).
from __future__ import annotations

import importlib
from typing import Any

# --- EDIT THIS ---------------------------------------------------------------------------------------
# The DB URL (a string, or a `module:attr` that resolves to one — dotted attrs work, e.g. a settings
# object). Sync (`postgresql://…`) and async (`postgresql+asyncpg://…`) drivers are both handled.
DB_URL_IMPORT = "app.core.config:settings.DATABASE_URL"
# The declarative Base — imported from the PACKAGE that registers every model (its `__init__` imports
# all model modules and re-exports Base). Do NOT point at a bare `base_model`: that Base's metadata
# reflects only whatever models happened to be imported by this test's import chain, so every
# unimported table reads as a false "remove_table". Point at the thing that pulls the whole model set.
METADATA_IMPORT = "app.models:Base"
# -----------------------------------------------------------------------------------------------------

# Operation names alembic emits for a DROP — the only ones that fail this test.
_DESTRUCTIVE_PREFIX = "remove_"  # remove_table / remove_column / remove_index / remove_constraint / remove_fk


def _load(path: str) -> object:
    mod, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"expected 'module:attribute', got {path!r}")
    obj: object = importlib.import_module(mod)
    for part in attr.split(
        "."
    ):  # dotted attrs (settings.database_url) resolve step by step
        obj = getattr(obj, part)
    return obj


def _metadata(obj: object) -> object:
    return getattr(
        obj, "metadata", obj
    )  # a declarative Base has .metadata; a MetaData is itself


def _url(obj: object) -> str:
    return (
        obj if isinstance(obj, str) else str(obj)
    )  # accept a URL string or a URL object


def _flatten(diffs: list[Any]) -> list[Any]:
    # compare_metadata returns a list whose entries are either an op-tuple or a LIST of op-tuples.
    out: list[Any] = []
    for d in diffs:
        out.extend(d) if isinstance(d, list) else out.append(d)
    return out


def _compare_sync(sync_conn, metadata) -> list[Any]:  # type: ignore[no-untyped-def]
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    return list(compare_metadata(MigrationContext.configure(sync_conn), metadata))


def _diff(url: str, metadata: object) -> list[Any]:
    """Build a short-lived engine from `url`, diff, dispose — the engine's whole lifecycle stays inside
    this call (and, for async, inside one asyncio.run), so there is no shared pool and no NullPool."""
    from sqlalchemy import make_url

    if make_url(url).get_dialect().is_async:
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        async def _run() -> list[Any]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    diffs = await conn.run_sync(lambda c: _compare_sync(c, metadata))
                    return list(diffs)
            finally:
                await engine.dispose()

        return asyncio.run(_run())

    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return _compare_sync(conn, metadata)
    finally:
        engine.dispose()


def test_no_destructive_schema_drift() -> None:
    metadata = _metadata(_load(METADATA_IMPORT))
    ops = _flatten(_diff(_url(_load(DB_URL_IMPORT)), metadata))
    destructive = [op for op in ops if str(op[0]).startswith(_DESTRUCTIVE_PREFIX)]
    additive = [op for op in ops if str(op[0]).startswith("add_")]
    modify = [
        op
        for op in ops
        if str(op[0]).startswith("modify_") and op[0] != "modify_comment"
    ]
    if additive or modify:  # informational — never fails the build
        print(
            f"schema drift (non-destructive): {len(additive)} additive, {len(modify)} modify — "
            "review at the next migration, but not a drop."
        )
    assert not destructive, (
        f"{len(destructive)} DESTRUCTIVE schema-drift op(s): the models drop what the DB has —\n"
        + "\n".join(f"  {op[0]}: {op[1:]}" for op in destructive)
        + "\nAn `alembic revision --autogenerate` here would generate these DROPs. Reconcile the "
        "models with the DB (or write the intended migration deliberately) before that ships."
    )
