-- Streamed after the dump by `make restore` and `make prod-restore`, inside the one transaction the
-- recipe opens with BEGIN. A restored database carries no session generation: a dump taken before
-- dumps left it out holds one a later logout may have revoked, so its row is deleted here, and the
-- app writes a fresh one when it starts. Only this COMMIT commits the restore: a stream cut short
-- (Ctrl-C, a dropped ssh hop, a failed read) ends without it, and nothing is kept. psql exits 0
-- either way, so the recipe requires the line echoed after it.
DO $$
BEGIN
  IF to_regclass('public.dashboard_session_state') IS NOT NULL THEN
    DELETE FROM public.dashboard_session_state;
  END IF;
END
$$;
COMMIT;
\echo restore committed
