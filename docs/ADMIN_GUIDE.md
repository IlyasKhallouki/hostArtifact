# Administrator guide

Deploy the API and viewer once, then give users a configured plugin. They need an allowed Google account. They don't need Google Cloud roles or access to this repository.

## Configure the deployment

Install Python 3.12+, uv and the Google Cloud CLI. The Claude CLI is optional for local development, but `make plugin-validate` uses it to check plugin and marketplace manifests.

```bash
make setup
cp infra/config.env.example infra/config.env
```

Edit the copied file. Set the Google Cloud project ID and number, region, a globally unique bucket name, your account allowlist, administrator email and display names. Use a dedicated named Firestore database. The application refuses `(default)`.

`ALLOWED_EMAIL_DOMAINS` permits verified accounts on the listed domains. `ALLOWED_EMAILS` adds individual accounts. `ADMIN_EMAILS` permits permanent deletion, but it does not bypass the publishing allowlist. Choose the plugin name before distributing it and keep it stable for that deployment.

The example file contains no usable project or account settings. Keep the completed file local. Git ignores it.

## Check access

Authenticate the administrative CLI and application-default credentials:

```bash
gcloud auth login
gcloud auth application-default login
uv run --project server publisher-admin preflight --profile bootstrap
```

The CLI uses the active gcloud account for administrative APIs and application-default credentials for Firestore. Make sure both represent the intended operator. Preflight reports the effective principal and target project without changing the global gcloud project setting.

An exit code of 0 means the checks passed. Code 2 means a prerequisite is missing or a check could not be completed. The report distinguishes missing permissions from disabled APIs, authentication failures and unavailable policy checks. It tests existing buckets at the bucket level and tests service-account impersonation separately.

For missing permissions, the report gives the resource scope and commands an IAM administrator can review. It does not grant roles automatically. Treat an `unknown` result as unresolved, rather than proof that access is allowed or denied.

Other profiles cover deployment and domains:

```bash
uv run --project server publisher-admin preflight --profile deploy
uv run --project server publisher-admin preflight --profile domains
```

## Create the infrastructure

Review the plan, then apply it to your configured project:

```bash
infra/bootstrap.sh --dry-run
infra/bootstrap.sh --apply
```

Bootstrap creates the named database, private content and build-source buckets, Artifact Registry repository, service accounts and Secret Manager entries. It generates signing and encryption keys without printing them. Runtime roles are scoped to the publisher resources; the database has deletion protection.

The scripts inspect existing resources and refuse conflicting ones. Review cloud costs and resource names before applying. The API keeps one instance warm. The default custom-domain strategy adds a global HTTPS load balancer. Check [Cloud Run pricing](https://cloud.google.com/run/pricing), [load-balancing pricing](https://cloud.google.com/vpc/network-pricing) and the storage products for your region and expected usage.

## Set up Google sign-in

Open [Google Auth Platform](https://console.cloud.google.com/auth/overview) in your configured project and create a web OAuth client for this deployment. Configure its branding, audience and basic OpenID, email and profile scopes. Use an Internal audience when it fits your Workspace organization; otherwise configure an External audience for the people who will use the service. Check Google's requirements for your chosen audience before distributing the plugin.

Set the client's authorized redirect URI to:

```text
https://API_SERVICE-GCP_PROJECT_NUMBER.REGION.run.app/auth/callback
```

Replace those three values with your configuration. The API's domain stays on `run.app`; custom sharing domains apply to the viewer.

In [Secret Manager](https://console.cloud.google.com/security/secret-manager), add the OAuth client ID as a version of `publisher-oauth-client-id`, and its secret as a version of `publisher-oauth-client-secret`. Use the console's secret-value field or pipe values to `gcloud secrets versions add --data-file=-` from a secure local source. Don't put secret values in a shell command, chat, issue or Git file.

Bootstrap fills the other three secrets: `publisher-jwt-signing-key`, `publisher-storage-encryption-key` and `publisher-cookie-signing-key`.

A gcloud login doesn't create the OAuth web client. Browser sign-in happens again when a user connects the plugin. The OAuth proxy supports dynamic client registration and URL-based client metadata, so users do not enter your Google client credentials into Claude.

## Deploy and verify

```bash
make check
infra/deploy.sh --dry-run
infra/deploy.sh --apply
```

Deploy from a clean commit. The script builds one image, resolves its immutable digest, and deploys both services with pinned secret versions. Cloud Run allows public HTTP ingress; the API enforces OAuth before exposing publishing tools. Organization policies must allow this configuration.

The deployment runs smoke checks for health, commit identity, anonymous MCP rejection, OAuth discovery and viewer responses. To check Claude's authorization route through its consent page:

```bash
infra/smoke_test.sh --service api --claude-oauth
```

That check creates a temporary OAuth transaction. It doesn't sign a Google account in or publish a page. If testing an older deployment, add `--git-sha DEPLOYED_COMMIT`.

You can deploy services separately with `--service api` or `--service viewer`. To reuse an existing image, pass `--image` with a digest from this project's publisher repository. A viewer-only deployment doesn't require the Google OAuth client secrets.

## Build and distribute the plugin

```bash
make plugin-zip
claude plugin validate ./dist/plugin
```

The package is `dist/PLUGIN_NAME-VERSION.zip`. The renderer reads your local configuration and writes the API URL and branding into `dist/plugin`. It does not modify the committed template or marketplace. Both generated outputs are ignored by Git.

Give users that configured ZIP through the plugin installation or organization distribution options available in their Claude account. They then connect the publisher and sign in with an allowed Google account. The source template in this public repository points to an example address and won't connect to your deployment.

The MCP connector URL is:

```text
https://API_SERVICE-GCP_PROJECT_NUMBER.REGION.run.app/mcp
```

For Claude Code, add your deployed endpoint with:

```bash
claude mcp add --transport http publisher YOUR_MCP_URL
```

Open `/mcp` to authenticate. Keep deployment-specific endpoints and account details out of public commits when preparing a private organization marketplace.

## Accept the deployment

Use a real Claude conversation to publish a small test page. Open its URL, update it and confirm that the URL stays the same. Test password denial and success, expiry, unpublish and restore, rollback, and replacement links. Check `whoami` with an allowed account and, if available, a blocked account.

For custom domains, publish on each active host, move a page between them and check the old link's redirect. Changing a client or global default should affect future pages only. Unpublish and delete the test pages when finished.

Local tests and smoke checks cover different parts of the system. Repeat the browser and publishing checks after changes to authentication or deployment configuration.

## Add a custom domain

```bash
uv run --project server publisher-admin domains discover
uv run --project server publisher-admin domains add share.example.com --strategy load-balancer
uv run --project server publisher-admin domains add share.example.com --strategy load-balancer --apply
```

Use a domain you control in place of `share.example.com`. Discovery reads the selected project's Cloud Run mappings, Cloud DNS zones, URL maps and certificates, plus domains verified for the active account. The output reports failures and partial coverage. It can't discover inaccessible projects or prove access to your registrar account.

The default strategy uses a shared HTTPS load balancer and a managed certificate for each hostname. Cloud Run domain mapping is also available in supported regions. See [Google's custom-domain documentation](https://docs.cloud.google.com/run/docs/mapping-custom-domains) before choosing a strategy.

After provisioning, copy the DNS records returned by the CLI. For Squarespace, open the domain's DNS settings and add the records under Custom records. The CLI gives relative Host names, Type, Data and TTL. Use those exact values; example IPs or certificate targets won't work.

Replace conflicting A, AAAA or CNAME records only for the hostname being connected. Preserve mail records, unrelated TXT records and existing website routes. Keep certificate-authorization CNAME records for renewal. See [Squarespace's DNS instructions](https://support.squarespace.com/hc/en-us/articles/360002101888-Edit-your-domain-s-DNS-records).

```bash
uv run --project server publisher-admin domains status share.example.com --apply
uv run --project server publisher-admin domains default share.example.com --apply
```

Status compares records through Google and Cloudflare resolvers, checks certificate readiness, and verifies HTTPS against the deployed viewer. Only active domains are available to publishers. DNS propagation and certificate issuance may take time.

Progress appears on stderr; stdout contains JSON. Google read commands have a 30-second limit. Ctrl-C exits with code 130. A failed observation returns `check_complete: false` and code 2, preserving the last known registry state.

Changing a default doesn't move existing pages. Use `set_artifact_domain` to move a page. Other active hosts redirect to its canonical URL; visitors must enter the password again after a host change because cookies are scoped to each host.

`domains remove HOST --apply` refuses domains still referenced by pages, clients or the global default. Removal detaches the registry entry and ends redirects from that host. DNS and cloud resources remain for separate cleanup.

## Operations and rollback

To change publishing access, edit the local allowlist and redeploy the API. A removed account is blocked on its next tool call. `whoami` remains available to explain the reason. The Google OAuth audience can impose additional sign-in restrictions before the publisher allowlist runs.

Read application logs through Cloud Run. The application records tool name, actor, timing and commit identity. It avoids tool arguments, HTML and passwords. Keep raw cloud and OAuth diagnostic logs private; provider logs may contain URLs and identifiers.

Each deployment saves previous traffic assignments in `dist/rollback-COMMIT-SERVICE.json`. To roll back, inspect that file and restore the relevant revision or traffic split:

```bash
gcloud run services update-traffic SERVICE --to-revisions=PREVIOUS_REVISION=100 --region=YOUR_REGION --project=YOUR_PROJECT
```

A two-service deployment isn't atomic. If one service fails, inspect both and restore traffic as needed. Retain the old image, revision and pinned secret versions until rollback is no longer needed.

Adding a new secret version doesn't update a running revision; redeploy to use it. JWT signing-key rotation signs publishers out. Cookie-key rotation signs page visitors out. Encryption-key rotation needs an OAuth-state migration or an intentional session reset; replacing it without either makes existing encrypted state unreadable.

Metadata transactions scan the shared catalog. Password rate limits apply per instance, and a load balancer can cause visitors to share a conservative IP bucket. Review these limits before scaling. Version history is useful for undoing edits but does not replace backups. Plan Firestore exports and GCS retention around your own retention requirements.

Existing separately hosted pages aren't imported automatically. Republish their original HTML if you want the publisher to manage them.

## Teardown

Export anything you need to retain, inventory shared dependencies and account for links that will stop working. Remove only publisher-owned resources. Delete the content bucket and database last, after reviewing the retained data and disabling database deletion protection deliberately. Remove publisher-specific IAM bindings without replacing unrelated bindings. The repository does not include an automatic teardown command.
