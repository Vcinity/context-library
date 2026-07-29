#!/usr/bin/env bash
set -euo pipefail

runtime_dir="$(mktemp -d /tmp/clm-e2e.XXXXXX)"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "${runtime_dir}/server.key" -out "${runtime_dir}/server.crt" \
  -subj "/CN=127.0.0.1" >/dev/null 2>&1

export CLM_DATABASE_URL="sqlite:///${runtime_dir}/runtime.db"
export CLM_LIBRARY_ROOT="${runtime_dir}/library"
export CLM_STATE_ROOT="${runtime_dir}/state"
export CLM_PROJECT="example"
export CLM_REQUIRE_OIDC="false"
export CLM_ALLOW_LOCAL_DEV_IDENTITY="true"
export CLM_DEVELOPMENT_MODE="true"
export CLM_SESSION_SECRET="e2e-session-secret-with-sufficient-entropy"
export CLM_AGENT_COMMAND="poetry run python scripts/fake_e2e_agent.py"
export CLM_PROCESS_INTERVAL_SECONDS="1"

poetry run python scripts/seed_e2e.py
git -C "${CLM_LIBRARY_ROOT}" init -q
git -C "${CLM_LIBRARY_ROOT}" config user.name "Context Library E2E"
git -C "${CLM_LIBRARY_ROOT}" config user.email "e2e@example.invalid"
git -C "${CLM_LIBRARY_ROOT}" add .
git -C "${CLM_LIBRARY_ROOT}" commit -qm "Synthetic E2E baseline"

poetry run python -m context_library_manager.processes worker &
worker_pid=$!
trap 'kill "${worker_pid}" 2>/dev/null || true' EXIT

poetry run uvicorn context_library_manager.api:create_app --factory \
  --host 127.0.0.1 --port 8445 \
  --ssl-keyfile "${runtime_dir}/server.key" --ssl-certfile "${runtime_dir}/server.crt"
