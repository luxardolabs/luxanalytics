-- Streamed after the dump by `make restore` and `make prod-restore`, inside the one transaction the
-- recipe opens (BEGIN, then SET LOCAL luxanalytics.restore). The recipe follows it with the
-- run's own token as the database's comment and the only COMMIT: a stream cut short (Ctrl-C, a
-- dropped ssh hop, a failed read) ends without it, and nothing is kept.
--
-- A restored database carries no session generation: a dump taken before dumps left it out holds
-- one a later logout may have revoked, so its row is deleted here, and the app writes a fresh one
-- when it starts.
--
-- A dump is trusted input: it runs as the database's owner and can do anything. The recipe's
-- checks catch a dump damaged or edited by mistake, not one written to deceive it.
DO $$
BEGIN
  -- SET LOCAL ends with its transaction: gone, the dump ended the restore's transaction early
  IF current_setting('luxanalytics.restore', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'the dump ended the restore''s transaction early';
  END IF;
  IF to_regclass('public.dashboard_session_state') IS NOT NULL THEN
    DELETE FROM public.dashboard_session_state;
  END IF;
END
$$;
