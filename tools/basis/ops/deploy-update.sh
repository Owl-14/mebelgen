#!/usr/bin/env bash
# Обновление кода демо с дев-машины (запускать из tools/basis).
# НЕ трогает на сервере: paramspecs/ (изделия посетителей), .env, /opt/bazis/out.
set -euo pipefail
HOST=root@80.66.89.3
KEY="${BAZIS_DEPLOY_KEY:-$HOME/.ssh/bazis_deploy}"
SHA=$(git rev-parse --short HEAD)
echo "$SHA" > DEPLOY_SHA
tar czf /tmp/basis_update.tgz --exclude='__pycache__' --exclude='.previews' \
    DEPLOY_SHA main.py requirements.txt README.md AGENTS.md RULES.md STUDIO.md \
    src schema prompts rules materials scripts tests qa landing vendor assets ops
scp -i "$KEY" /tmp/basis_update.tgz "$HOST":/tmp/
ssh -i "$KEY" "$HOST" 'cd /opt/bazis/basis && tar xzf /tmp/basis_update.tgz \
  && chown -R bazis:bazis . && systemctl restart bazis && sleep 3 \
  && systemctl is-active bazis && curl -s http://127.0.0.1:8765/version'
echo
echo "deployed $SHA"
