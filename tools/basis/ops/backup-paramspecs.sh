#!/usr/bin/env bash
# Бэкап legacy-каталога, tenant-каталогов и identity DB.
# cron: 0 4 * * * root /opt/bazis/backup-paramspecs.sh
set -euo pipefail
DST=/opt/bazis/backups
mkdir -p "$DST"
tar czf "$DST/paramspecs-$(date +%F).tgz" -C /opt/bazis/basis paramspecs

SNAPSHOT=$(mktemp -d "$DST/.studio-data.XXXXXX")
trap 'rm -rf -- "$SNAPSHOT"' EXIT
if [[ -f /opt/bazis/data/identity.sqlite3 ]]; then
  /opt/bazis/venv/bin/python - "$SNAPSHOT/identity.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect("file:/opt/bazis/data/identity.sqlite3?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[1])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
fi

archive="$DST/studio-data-$(date +%F).tgz"
tar_args=()
if [[ -d /opt/bazis/tenants ]]; then
  tar_args+=(-C /opt/bazis tenants)
fi
if [[ -f "$SNAPSHOT/identity.sqlite3" ]]; then
  tar_args+=(-C "$SNAPSHOT" identity.sqlite3)
fi
if ((${#tar_args[@]})); then
  tar czf "$archive" "${tar_args[@]}"
fi

# Храним 14 ежедневных копий каждого вида.
ls -1t "$DST"/paramspecs-*.tgz | tail -n +15 | xargs -r rm --
ls -1t "$DST"/studio-data-*.tgz 2>/dev/null | tail -n +15 | xargs -r rm --
