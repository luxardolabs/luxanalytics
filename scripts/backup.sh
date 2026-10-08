#!/bin/sh
# The luxanalytics_backup service's loop (compose.yml, profile `backup`; LUXANALYTI-14): a daily
# gzipped pg_dump into BACKUP_DIR, keeping BACKUP_KEEP days of them. Dumps are written 0600 (they
# hold the apps' DSN public ids).
#
# Rotation runs only after a dump succeeded, and only ever touches this loop's own dumps
# (luxanalytics-<UTC stamp>Z.sql.gz, directly in BACKUP_DIR): a run of failed dumps keeps the last
# good ones, and a manual or pre-migration dump (`luxanalytics-backup-…`, a subdirectory) is never
# deleted. A dump goes only when there are more than BACKUP_KEEP AND it is BACKUP_KEEP days old,
# so a burst of container restarts (each dumps at start) never pushes out the daily history, and a
# dump stamped by a clock running ahead is never old enough to take a real one's place. The
# service's healthcheck reds when no dump from the last two days exists.
#
# BACKUP_ONCE=1 runs one iteration and exits (the tests).
umask 077
dir=${BACKUP_DIR:-/backups}
keep=${BACKUP_KEEP:-14}
interval=${BACKUP_INTERVAL:-86400}
case $keep in
  '' | *[!0-9]* | 0)
    echo "BACKUP_KEEP must be a whole number of days >= 1, got '$keep'" >&2
    exit 1
    ;;
esac

while :; do
  # A dump interrupted by a restart leaves its .part behind.
  rm -f "$dir"/*.part
  f="$dir/luxanalytics-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  # pg_dump compresses itself (-Z): no pipe, so its exit status is the dump's.
  if pg_dump -h luxanalytics_db -U luxanalytics -Z 6 -f "$f.part" luxanalytics; then
    mv "$f.part" "$f"
    echo "backup written: $f"
    # The glob expands oldest first (the stamp sorts by time): past $keep dumps, drop from the
    # front while the front one is $keep days old; stop at the first younger one.
    set -- "$dir"/luxanalytics-[0-9]*Z.sql.gz
    while [ "$#" -gt "$keep" ] && [ -n "$(find "$1" -mtime +"$((keep - 1))")" ]; do
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
