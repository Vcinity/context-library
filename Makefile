SHELL := /bin/sh

MARKDOWN_FILES := AGENTS.md ARCHITECTURE.md CHANGELOG.md IMPLEMENTATION_PROMPT.md MIGRATION.md README.md SPEC.md docs/DEPLOYMENT.md docs/PLUGIN_DEPLOYMENT.md docs/RECOVERY.md docs/TOOL_USE_CASES.md

.PHONY: install plugin-install test lint format check contracts contracts-check plugin-build plugin-check ui-build ui-test e2e smoke package run openapi openapi-check

install:
	poetry sync --no-interaction
	npm ci --ignore-scripts

plugin-install:
	@test -n "$(PLUGIN_DEST)" || { echo "PLUGIN_DEST is required" >&2; exit 2; }
	@test -n "$(LIBRARY_ROOT)" || { echo "LIBRARY_ROOT is required" >&2; exit 2; }
	poetry run python scripts/install_plugin.py \
		--destination "$(PLUGIN_DEST)" \
		--library-root "$(LIBRARY_ROOT)" \
		$(if $(MARKETPLACE_NAME),--marketplace-name "$(MARKETPLACE_NAME)") \
		$(if $(PROJECT),--project "$(PROJECT)") \
		$(if $(CONTEXT_REQUIREMENT),--context-requirement "$(CONTEXT_REQUIREMENT)") \
		$(if $(STAGE_ONLY),--stage-only)

test: install
	poetry run pytest
	npm test
	$(MAKE) plugin-check

lint:
	poetry run ruff check src tests scripts
	poetry run ruff format --check src tests scripts
	mdl $(MARKDOWN_FILES)
	npm run lint

format:
	poetry run ruff format src tests scripts
	poetry run ruff check --fix src tests scripts

contracts:
	poetry run python scripts/generate_contracts.py

contracts-check:
	poetry run python scripts/generate_contracts.py --check
	poetry run python scripts/generate_plugin_runtime.py --check

plugin-build:
	poetry run python scripts/build_plugin.py

plugin-check:
	poetry run python scripts/plugin/validate_plugin_repo.py
	poetry run python scripts/plugin/smoke_mcp_server.py
	poetry run python scripts/plugin/test_projection.py
	poetry run python scripts/plugin/test_activation_hook.py
	poetry run python scripts/generate_plugin_runtime.py --check

ui-test:
	npm ci --ignore-scripts
	npm test

ui-build:
	npm ci --ignore-scripts
	npm run build

openapi:
	poetry run python scripts/generate_openapi.py

openapi-check:
	poetry run python scripts/generate_openapi.py --check

e2e: ui-build
	npx playwright test

smoke:
	poetry run python scripts/smoke_context_library.py

package: contracts-check plugin-build ui-build
	poetry build --clean
	poetry run python scripts/smoke_package.py

check: lint test contracts-check ui-build openapi-check
	git diff --check

run:
	poetry run uvicorn context_library_manager.api:create_app --factory --host 127.0.0.1 --port 8000
