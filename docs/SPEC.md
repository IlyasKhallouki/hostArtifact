# Behavior reference

Artifact Publisher lets an allowed Google user publish and manage HTML pages through MCP. It runs an authenticated API and a public viewer in separate Cloud Run services. This reference describes the application contract; the [administrator guide](ADMIN_GUIDE.md) covers deployment.

## Scope

A page is a UTF-8 HTML document, with embedded assets or assets available at public URLs. The default size limit is 2,000,000 bytes. The publisher stores the page and serves it as HTML. It doesn't upload separate asset files, run a page backend, create client portal indexes or import existing hosted pages.

Publishers share one catalog. Client names group pages; they do not create tenant access boundaries. An allowed publisher can manage the shared catalog. Permanent deletion additionally requires administrator status.

## Configuration

Copy `infra/config.env.example` to the ignored `infra/config.env` file for a deployment. Pydantic settings also accept environment variables. Custom domains are stored in Firestore and managed through the CLI.

| Setting | Purpose |
| --- | --- |
| `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `REGION` | Target project and location; project number determines the Cloud Run URLs |
| `API_SERVICE`, `VIEWER_SERVICE` | Separate Cloud Run service names |
| `FIRESTORE_DATABASE` | Dedicated named database; `(default)` is rejected |
| `BUCKET`, `AR_REPO` | HTML bucket and container repository |
| `ORG_DISPLAY_NAME`, `ADMIN_CONTACT` | Viewer branding and access-help contact |
| `ALLOWED_EMAIL_DOMAINS`, `ALLOWED_EMAILS` | Verified Google accounts permitted to publish |
| `ADMIN_EMAILS` | Allowed publishers who may permanently delete unpublished pages |
| `PLUGIN_NAME`, `PLUGIN_DISPLAY_NAME` | Stable deployment-specific plugin identity and label |
| `DEFAULT_ACCESS_MODE` | `link` or `password` for new pages |
| `MAX_HTML_BYTES` | Maximum HTML size in UTF-8 bytes |
| `DOMAIN_STRATEGY` | `load-balancer` or `domain-mapping` |
| `DNS_PROVIDER_DEFAULT`, `SUGGESTED_SUBDOMAIN` | DNS instruction provider and suggested hostname prefix |

`GOOGLE_WORKSPACE` records an operator's audience choice for setup. It doesn't configure Google's OAuth audience automatically.

The API and viewer URLs normally derive from their service names, project number and region. The MCP endpoint is the API URL plus `/mcp`. Google redirects to the API URL plus `/auth/callback`. Custom domains apply to the viewer.

## Authentication and authorization

The API uses FastMCP's Google OAuth proxy, with dynamic client registration, URL-based client metadata, PKCE and consent. It requests OpenID, email and profile scopes. Google credentials, the JWT signing key and the OAuth-state encryption key come from Secret Manager.

OAuth state is shared across instances in the named Firestore database. Its collections use an `oauth_` prefix and values are encrypted with Fernet. URL-based client IDs are encoded as valid Firestore document IDs. Existing generated keys retain their storage IDs.

On each tool-listing or tool-call request, middleware requires a verified email whose exact address or domain appears in the allowlist. A blocked account can discover and call only `whoami`, which reports the account, reason and next step. Administrator status does not override the allowlist.

Development mode uses `dev@local` without OAuth. It is available only for local development. The app refuses development authentication or local storage when Cloud Run's `K_SERVICE` variable is present.

The API uses stateless Streamable HTTP at `/mcp`. Anonymous requests receive HTTP 401 with a `WWW-Authenticate` header pointing to protected-resource metadata. Discovery advertises the exact MCP resource, authorization server, registration endpoint and S256 PKCE support.

## MCP tools

Tools accept an artifact ID or a full page URL wherever the parameter is named `artifact`. Results contain metadata rather than HTML unless HTML was requested. Errors start with a code such as `NOT_FOUND`, `INVALID_INPUT`, `CONFLICT`, `FORBIDDEN` or `TOO_LARGE`, followed by an explanation.

| Tool | Behavior |
| --- | --- |
| `whoami` | Reports signed-in email, allowed/admin status, refusal reason, next step and server version |
| `list_clients` | Lists client names, slugs, live page counts and latest updates |
| `list_artifacts` | Searches and filters pages, with pagination |
| `get_artifact` | Returns metadata and, optionally, current HTML in chunks |
| `publish_artifact` | Creates a page and its first version |
| `update_artifact` | Updates HTML or applies exact edits while keeping the URL |
| `set_access` | Changes password/link access, expiry or the URL token |
| `list_versions` | Lists versions newest first |
| `rollback_artifact` | Copies an earlier version into a new current version |
| `unpublish_artifact` | Takes a page offline without removing its history |
| `restore_artifact` | Restores an unpublished page |
| `delete_artifact_permanently` | Deletes an unpublished page after administrator and exact-title checks |
| `list_domains` | Lists active domains, the global default and viewer fallback |
| `set_artifact_domain` | Moves one page to an active domain |
| `set_client_domain` | Changes the default for a client's future pages |

`publish_artifact` takes a client, title and HTML, plus optional slug, domain, access mode, password, expiry and note. A matching existing client is reused. A duplicate live client/slug pair returns a conflict unless `allow_duplicate` is set.

`update_artifact` accepts full HTML or sequential exact find/replace edits. Each edit must match its expected count; a mismatch fails the whole update. A title-only update is also allowed. HTML changes create a new immutable version. Updates to an unpublished page don't publish it again.

`get_artifact(include_html=true)` supports `offset` and `max_chars`; callers continue with `next_offset` while `truncated` is true. The result caps HTML chunks at 120,000 characters.

`set_access` generates a password when password mode is requested without one. It returns that password once. Password and mode changes invalidate existing viewer sessions. `clear_expiry=true` clears expiry; a missing or null expiry preserves it. `rotate_link=true` replaces the URL token and invalidates the old link.

Rollback creates a new version, preserving a linear history. Permanent deletion requires the page to be unpublished and `confirm_title` to match exactly. Audit records remain after deletion.

## HTML and storage

The service rejects empty or oversized HTML. It wraps fragments in an HTML document with a title, charset and viewport. It rejects known Claude-only runtime APIs, such as `window.claude` and `window.storage`, that won't work in a standalone page.

It does not otherwise sanitize HTML. Scripts and public CDN resources can run in the visitor's browser. Keep publishing access limited to trusted authors.

Firestore contains these metadata collections:

| Collection | Contents |
| --- | --- |
| `clients` | Client identity and future-page domain default |
| `artifacts` | Current page metadata, token, access settings, domain and version |
| `versions` | Version metadata, blob path, hash, author and note |
| `domains` | Hostname, provisioning state, DNS requirements and resource references |
| `audit` | Actor, action, time and limited event details |

Version documents use an artifact/version identifier. HTML lives in immutable GCS objects under the artifact and version, with a unique component for each upload attempt. Passwords are stored as Argon2id hashes. Generated passwords are returned once and aren't stored in plaintext.

Transactions read the small catalog and serialize writes with a shared lock document. HTML uploads precede metadata commits; failed attempts can leave unreferenced objects. Permanent deletion revokes the public metadata before blob cleanup.

## Viewer contract

Page URLs contain a client slug, page slug and random token. A request must match the stored path and token. The viewer serves only live pages on known hosts.

| Condition | Response |
| --- | --- |
| Unknown host, missing page, invalid token or unpublished page | 404 |
| Expired page | 410 |
| Password required or incorrect | 401 |
| Too many failed password attempts | 429 |
| Correct password submission | Signed cookie and 303 redirect to the page |
| Valid access | Current HTML with 200 |

The `__session` cookie is signed, HttpOnly and SameSite=Lax, with a 12-hour lifetime. It is Secure for HTTPS deployments and has no Domain attribute. Sessions carry the artifact's access-version value, so changed access settings invalidate old sessions.

Password attempts are limited per IP/page, per instance. This is a local safeguard, not a distributed rate limiter. The forwarding-address choice can cause visitors behind a load balancer to share a bucket.

The viewer reads metadata on each request and caches immutable HTML in a bounded LRU. Responses include noindex directives, `nosniff`, a no-referrer policy and a CSP restricting framing to the same origin. Password pages and protected content use `no-store`. The viewer's `robots.txt` disallows indexing.

`/health` on either service returns `ok`, app mode and deployed commit identity. The viewer also serves `robots.txt` and an empty favicon response; it has no page directory or client index.

## Domains

Known hosts are the configured viewer hostname and active registered domains. Arbitrary hosts ending in `run.app` are not trusted.

New pages choose a domain in this order: explicit selection, client default, global default, viewer fallback. The chosen domain is stored with the page. Later changes to defaults affect future pages. Clearing a page's domain override resolves the current default again.

Requests on another active host redirect to the page's canonical URL. GET uses 301; password POST uses 307. If a domain becomes unavailable, page URLs fall back to the configured viewer host and tools report a warning. Removing a domain ends its redirects.

The admin CLI discovers domains from the selected project's Cloud Run mappings, DNS zones, URL maps and certificates, plus the active account's verified domains. It reports source coverage and failures; it doesn't claim to list inaccessible domains.

Provisioning supports a shared global HTTPS load balancer with separate certificate authorizations per host, or Cloud Run domain mapping in supported regions. Resource names are deterministic, and existing resources must match the publisher's expected configuration before reuse.

The CLI prints DNS records returned by Google as relative Squarespace Host, Type, Data and TTL rows. An account owner saves them. Activation requires matching expected records through Google and Cloudflare resolvers, a ready certificate, and valid HTTPS serving the deployed viewer's health response.

A transient DNS, certificate or HTTPS observation failure leaves the previous registry status intact and marks the check incomplete. Domain removal refuses page, client and global-default references. It detaches the registry entry but retains DNS and infrastructure for separate cleanup.

## Infrastructure and delivery

Bootstrap provisions private buckets, the named database, the container repository, service accounts and secrets. API, viewer and builder accounts have separate responsibilities. Runtime Firestore roles are scoped to the named database; GCS and secret access are resource-specific.

Preflight checks the active identity, required permissions, enabled APIs, service-account access and relevant organization policies. It reports missing permissions with their scopes and suggested administrator commands. It never grants operator permissions to itself.

Infrastructure and domain commands plan by default and apply only with `--apply`. `--dry-run` takes precedence. Deployment requires a clean commit unless the operator explicitly chooses `--allow-dirty` for diagnostics. It pins image and secret versions and records previous traffic for rollback.

The committed plugin is a generic template. Packaging renders a configured copy under ignored `dist/`, leaving the template and marketplace unchanged. Plugin manifest, marketplace and changelog versions must agree.

## Verification

`make check` runs lint, formatting, tests, plugin consistency checks and the staged secret scanner. It runs Claude's plugin and marketplace validators when that CLI is installed.

`local_acceptance.py` exercises the local HTTP API and viewer. Deployment smoke tests check health and OAuth discovery; `--claude-oauth` also checks Claude's metadata-based client ID through consent. Neither replaces a real Google login and publishing session in Claude.

Before distributing a deployment, verify publishing and updating at the same URL, password access, expiry, version rollback, unpublish/restore and replacement links. Verify blocked-account behavior where an account is available. For custom domains, check publishing, movement, old-link redirects and default isolation. Delete temporary test pages after acceptance.
