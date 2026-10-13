.PHONY: setup check format dev-api dev-viewer plugin-validate plugin-zip
setup:
	cd server && uv sync --frozen
	git config core.hooksPath .githooks
check:
	cd server && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run pytest -q
	python3 scripts/check_plugin.py
	bash scripts/check_secrets.sh
	$(MAKE) plugin-validate
format:
	cd server && uv run ruff check --fix . ../scripts && uv run ruff format . ../scripts
dev-api:
	cd server && APP_MODE=api AUTH_MODE=dev STORAGE_BACKEND=local uv run uvicorn publisher.main:create_app --factory --host 127.0.0.1 --port 8080
dev-viewer:
	cd server && APP_MODE=viewer AUTH_MODE=dev STORAGE_BACKEND=local uv run uvicorn publisher.main:create_app --factory --host 127.0.0.1 --port 8081
plugin-validate:
	@if command -v claude >/dev/null; then claude plugin validate ./plugin && claude plugin validate .; else echo 'WARNING: claude CLI missing; plugin validation skipped'; fi
plugin-zip:
	python3 scripts/package_plugin.py
