#!/bin/sh
set -e

# Fix ownership on bind-mounted paths so the non-root "app" user can
# read and write them.  The entrypoint runs as root; gosu drops to "app"
# before exec-ing the actual command.

# Ensure workspace directories exist
mkdir -p /app/workspaces/repos

for p in /app/ai-agent/.agent \
         /app/ai-agent/github_app \
         /app/ai-agent/logs \
         /app/ai-agent/state.json \
         /app/ai-agent/db \
         /app/workspaces; do
    [ -e "$p" ] && chown -R app:app "$p" 2>/dev/null || true
done

exec gosu app "$@"