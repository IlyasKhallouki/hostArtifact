# Design decisions

## Separate API and viewer origins

Published HTML can execute JavaScript. The API and viewer therefore use separate origins and service accounts. A page doesn't share its origin with Google OAuth endpoints. Both services come from the same codebase and image, with `APP_MODE` choosing which app starts.

## Persistent OAuth state

FastMCP's Google provider bridges Google sign-in to MCP clients. A named Firestore store keeps state available across replicas and restarts. Collection prefixes isolate OAuth data, and Fernet encrypts stored values. The store's key sanitizer handles URL-based client IDs while preserving ordinary generated keys.

## Shared catalog and immutable HTML

Metadata transactions read the small catalog and serialize writes through a shared lock document. This keeps uniqueness and version updates consistent. It is a deliberate small-deployment tradeoff; larger catalogs need a different query pattern.

GCS object paths include both a version and a random upload-attempt component. Transaction retries cannot overwrite another attempt's HTML. Failed writes can leave unreferenced objects for later cleanup. Permanent deletion removes public metadata before deleting blobs, so a blob-cleanup failure doesn't restore public access.

## Access changes take effect on the next request

The viewer reads metadata for every request and caches only immutable HTML. Password sessions include an access-version value; changing access settings invalidates older sessions. The cookie is secure in HTTPS deployments and scoped to its host.

Password attempt limits run in memory per instance. The viewer uses the rightmost platform forwarding address and disables uvicorn's proxy-header rewriting. Behind a load balancer this may group visitors conservatively. It is not a distributed abuse-prevention system.

## Domains belong to administrators

Publishers choose active domains through MCP tools. Administrators provision them, save DNS records and verify readiness. A shared load balancer serves multiple hosts, with separate certificate authorizations and certificates.

New pages snapshot the explicit domain, client default, global default or viewer fallback. Changing defaults doesn't move existing pages. Moving a page preserves redirects from other active hosts. Removing a host ends those redirects.

## Reproducible deployments

Deployments use a dedicated builder account, a private source bucket, an immutable image digest and pinned secret versions. Previous traffic assignments are saved for rollback. Health checks use `/health` on both services.

Serverless network endpoint groups don't accept the description flag used by other Compute resources. Provisioning verifies their deterministic name, region, type and target service before reusing them.

## Public templates and private configuration

The repository contains generic examples. Operators keep project settings in ignored `infra/config.env`; secrets live in Secret Manager. Plugin generation writes deployment-specific files to ignored `dist/` output. This keeps the public template usable without publishing an operator's contact details, cloud resource names or endpoint.

## Umami registration and tracking

Use one Umami website per artifact so every publish result can point to its own dashboard. Keep the website ID through page updates and link changes. Registration runs after the page transaction commits; a failed analytics request cannot roll back the page. A lease serializes registration, and a stable website-name prefix recovers creates whose responses were lost. This works with Umami v2, which assigns website IDs itself.

Inject tracking at serve time after access checks. Normalize pageview URLs to artifact IDs, omit referrers and discard identity/custom-event payloads so analytics never receives bearer links from the injected tracker. Keep historical statistics when a page is removed. The optional credential belongs only to the API service; the viewer needs no Umami API access.
