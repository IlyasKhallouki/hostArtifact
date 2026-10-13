# Security and privacy

The public repository contains source code and example configuration. Deployment settings belong in `infra/config.env`, which Git ignores. Credentials belong in Secret Manager or an ignored local `secrets/` directory. Generated plugin bundles in `dist/` contain your deployment URL and branding; review their audience before sharing them.

## Data stored by a deployment

Firestore stores client names, page titles and notes, publisher email addresses, version metadata, domain settings and audit events. OAuth state is encrypted with Fernet before storage. The private GCS bucket contains the HTML for each page version. Pages may load external assets chosen by their author.

The API checks the verified Google account against its allowlist on every tool call. All allowed publishers share a catalog; client names are organizational labels, not tenant boundaries. Removing an account from the allowlist and redeploying blocks its next publishing request. Administrators can permanently delete unpublished pages after confirming the title.

Public page links contain random tokens. Passwords add an access check, and expiry dates stop further viewing. The viewer reads metadata on each request so access changes don't wait for a metadata cache to expire. Downloads or copies already made by a visitor cannot be recalled.

Page scripts run on the viewer origin, separate from the OAuth API. Published HTML is not sanitized. Only grant publishing access to people you trust to put content on that origin.

## Before pushing changes

Review staged files and commit metadata. Don't commit production domains, personal contact details, OAuth client downloads, service-account keys, exported pages or deployment evidence. Ignoring a file does not remove it from existing Git history.

Run `make check`. If Gitleaks is installed, also scan the complete Git history with `gitleaks git --redact`. The fallback staged scanner catches several common credential formats but is not a complete secret audit.

## Reporting a problem

Don't paste credentials, private page URLs, page contents or personal data into a public issue. If you've exposed a credential, revoke or rotate it at its provider. Removing it from a later commit does not invalidate it.
