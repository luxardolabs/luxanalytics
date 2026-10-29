-- Streamed after the dump by `make restore` and `make prod-restore`, inside the one transaction the
-- recipe opens with BEGIN. A restored database carries no session generation: a dump taken before
-- dumps left it out holds one a later logout may have revoked, so its row is deleted here, and the
-- app writes a fresh one when it starts. Only this COMMIT commits the restore: a stream cut short
-- (Ctrl-C, a dropped ssh hop, a failed read) ends without it, and nothing is kept.
--
-- The proof of the commit is the database's comment, set to the run's own token (psql -v
-- restore_token) in the same transaction: the recipe reads it back over a new connection once psql
-- has gone, so nothing the dump prints, and no way it ends, can stand in for it.
DO $$
BEGIN
  IF to_regclass('public.dashboard_session_state') IS NOT NULL THEN
    DELETE FROM public.dashboard_session_state;
  END IF;
END
$$;
COMMENT ON DATABASE luxanalytics IS :'restore_token';
COMMIT;
