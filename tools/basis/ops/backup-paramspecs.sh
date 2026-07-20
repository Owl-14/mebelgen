#!/usr/bin/env bash
# Бэкап изделий посетителей демо (cron: 0 4 * * * root /opt/bazis/backup-paramspecs.sh)
set -euo pipefail
DST=/opt/bazis/backups
mkdir -p "$DST"
tar czf "$DST/paramspecs-$(date +%F).tgz" -C /opt/bazis/basis paramspecs
ls -1t "$DST"/paramspecs-*.tgz | tail -n +15 | xargs -r rm --   # храним 14 суток
