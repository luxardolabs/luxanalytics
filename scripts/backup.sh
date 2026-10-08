#!/bin/sh
# The luxanalytics_backup service's loop (compose.yml, profile `backup`; LUXANALYTI-14): a daily
# gzipped pg_dump into BACKUP_DIR, keeping the newest BACKUP_KEEP. Dumps are written 0600 (they
# hold the apps' DSN public ids).
#
# Rotation runs only after a dump succeeded, and only ever touches this loop's own dumps
# (luxanalytics-<UTC stamp>Z.sql.gz, directly in BACKUP_DIR): a run of failed dumps keeps the last
# good ones, and a manual or pre-migration dump (`luxanalytics-backup-…`, a subdirectory) is never
# deleted. The service's healthcheck reds when the newest dump is older than two days.
#
# BACKUP_ONCE=1 runs one iteration and exits (the tests).
umask 077
dir=${BACKUP_DIR:-/backups}
keep=${BACKUP_KEEP:-14}
interval=${BACKUP_INTERVAL:-86400}

while :; do
  # A dump interrupted by a restart leaves its .part behind.
  rm -f "$dir"/*.part
  f="$dir/luxanalytics-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  # pg_dump compresses itself (-Z): no pipe, so its exit status is the dump's.
  if pg_dump -h luxanalytics_db -U luxanalytics -Z 6 -f "$f.part" luxanalytics; then
    mv "$f.part" "$f"
    echo "backup written: $f"
    # The glob expands oldest first (the stamp sorts by time): drop from the front past $keep.
    set -- "$dir"/luxanalytics-[0-9]*Z.sql.gz
    while [ "$#" -gt "$keep" ]; do
      rm -f "$1"
      shift
    done
  else
    rm -f "$f.part"
    echo "backup FAILED: kept the existing dumps" >&2
  fi
  [ "${BACKUP_ONCE:-}" = 1 ] && exit 0
  sleep "$interval"
done
