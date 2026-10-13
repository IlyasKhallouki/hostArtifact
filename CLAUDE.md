# Artifact Publisher
Python MCP API and isolated public HTML viewer, hosted on Google Cloud. The Claude plugin lets colleagues publish client pages.

Read docs/SPEC.md and docs/DECISIONS.md. Python code is in server/src/publisher; admin CLI manages permissions and custom domains. Infrastructure scripts are in infra; plugin files are in plugin.

Use `make setup`, `make check`, `make dev-api`, `make dev-viewer`, `make plugin-zip`. Local apps share server/.localdata. Run `uv run --project server publisher-admin preflight` before cloud changes. Deploy with infra/deploy.sh after bootstrap and OAuth setup; see docs/ADMIN_GUIDE.md.

Conventional commits. Never commit secrets, never change the released plugin name, preserve other assistants' configuration. Never modify unrelated cloud resources. Record decisions in docs/DECISIONS.md. Do not include AI attribution in commits.
