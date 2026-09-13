#!/usr/bin/env bash
# Бэкап legacy-каталога, tenant-каталогов и identity DB.
# cron: 0 4 * * * root /opt/bazis/backup-paramspecs.sh
set -euo pipefail
DST=/opt/bazis/backups
mkdir -p "$DST"
tar czf "$DST/paramspecs-$(date +%F).tgz" -C /opt/bazis/basis paramspecs

SNAPSHOT=$(mktemp -d "$DST/.studio-data.XXXXXX")
trap 'rm -rf -- "$SNAPSHOT"' EXIT
# identity (учётки) и ai_journal (ТЗ, ответы нейросетей, ошибки) — consistent SQLite backup
for db in identity ai_journal; do
  if [[ -f "/opt/bazis/data/$db.sqlite3" ]]; then
    /opt/bazis/venv/bin/python - "/opt/bazis/data/$db.sqlite3" "$SNAPSHOT/$db.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
  fi
done

archive="$DST/studio-data-$(date +%F).tgz"
tar_args=()
if [[ -d /opt/bazis/tenants ]]; then
  tar_args+=(-C /opt/bazis tenants)
fi
for db in identity ai_journal; do
  if [[ -f "$SNAPSHOT/$db.sqlite3" ]]; then
    tar_args+=(-C "$SNAPSHOT" "$db.sqlite3")
  fi
done
if [[ -d /opt/bazis/data/ai_journal_files ]]; then
  tar_args+=(-C /opt/bazis/data ai_journal_files)
fi
if ((${#tar_args[@]})); then
  tar czf "$archive" "${tar_args[@]}"
fi

# Храним 14 ежедневных копий каждого вида.
ls -1t "$DST"/paramspecs-*.tgz | tail -n +15 | xargs -r rm --
ls -1t "$DST"/studio-data-*.tgz 2>/dev/null | tail -n +15 | xargs -r rm --
