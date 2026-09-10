#!/usr/bin/env bash
# Обновление кода демо с дев-машины (запускать из tools/basis).
# НЕ трогает на сервере: paramspecs/ (изделия посетителей), .env, /opt/bazis/out.
set -euo pipefail
HOST=root@80.66.89.3
KEY="${BAZIS_DEPLOY_KEY:-$HOME/.ssh/bazis_deploy}"
SHA=$(git rev-parse --short HEAD)
META_DIR=$(mktemp -d)
trap 'rm -rf -- "$META_DIR"' EXIT
printf '%s\n' "$SHA" > "$META_DIR/DEPLOY_SHA"
COPYFILE_DISABLE=1 tar czf /tmp/basis_update.tgz \
    --exclude='__pycache__' --exclude='.previews' \
    -C "$META_DIR" DEPLOY_SHA -C "$PWD" \
    main.py requirements.txt requirements-server.txt README.md AGENTS.md RULES.md STUDIO.md \
    src schema prompts rules materials scripts tests qa landing vendor assets ops
scp -i "$KEY" /tmp/basis_update.tgz "$HOST":/tmp/
ssh -i "$KEY" "$HOST" 'set -eu
  release_dir=$(mktemp -d /tmp/basis-release.XXXXXX)
  trap '\''rm -rf -- "$release_dir"'\'' EXIT
  tar xzf /tmp/basis_update.tgz -C "$release_dir"
  /opt/bazis/venv/bin/python "$release_dir/scripts/validate_catalogs.py" \
    --legacy-root /opt/bazis/basis/paramspecs \
    --tenant-root /opt/bazis/tenants --build
  cd /opt/bazis/basis
  tar xzf /tmp/basis_update.tgz
  chown -R bazis:bazis .
  systemctl restart bazis
  sleep 3
  systemctl is-active bazis
  curl -s http://127.0.0.1:8765/version'
echo
echo "deployed $SHA"
