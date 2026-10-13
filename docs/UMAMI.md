# Umami analytics

When Umami is enabled, publishing a page creates a website in Umami and returns its dashboard URL in `analytics.dashboard_url`. Each page has its own entry. Updating content, changing the page's domain, rotating its link, and restoring it keep the same analytics history.

Existing pages can be registered with the sync command below. Tracking starts when the updated viewer is deployed and the page has an Umami website ID. Earlier visits cannot be recovered.

## Configure it

Create a dedicated Umami user with the `user` role. To share dashboards with colleagues, create a team, add the publisher user as a **member**, and invite the colleagues who need access. A view-only user cannot register websites. The publisher does not need an administrator account.

Set these values in your local `infra/config.env`:

```dotenv
UMAMI_ENABLED=true
UMAMI_URL=https://analytics.example.com
UMAMI_TEAM_ID=your-team-uuid
```

Leave `UMAMI_TEAM_ID` empty to create websites owned by the configured user. By default, the API is `${UMAMI_URL}/api` and the tracker is `${UMAMI_URL}/script.js`. Set `UMAMI_API_URL` or `UMAMI_SCRIPT_URL` if your installation uses different addresses. Use a tracker that supports `data-before-send`; this integration has tests for the Umami v2 website API.

Create a private JSON file outside the repository with either:

```json
{"username":"publisher","password":"REPLACE_ME"}
```

or, for an installation that supports API keys:

```json
{"api_key":"REPLACE_ME"}
```

For local commands, load that JSON into `UMAMI_CREDENTIALS`. For Cloud Run, store it in Secret Manager:

```bash
# Bootstrap creates this secret and grants access to the API service only.
infra/bootstrap.sh --apply
gcloud secrets versions add publisher-umami-credentials \
  --project=YOUR_PROJECT --data-file=/absolute/path/umami-credentials.json
infra/deploy.sh --apply
```

The viewer receives the tracker URL and website IDs, but no Umami credentials. Deployments pin the secret version, so deploy the API again after rotating the credential.

## Register existing pages or retry a failed registration

Load the credential without printing it:

```bash
export UMAMI_CREDENTIALS="$(gcloud secrets versions access latest \
  --project=YOUR_PROJECT --secret=publisher-umami-credentials)"
uv run --project server publisher-admin analytics sync
uv run --project server publisher-admin analytics sync --apply
unset UMAMI_CREDENTIALS
```

The first command shows the page IDs it will sync. Add `--artifact PAGE_ID` to target one page. Running it again updates existing entries. The command exits with code 2 if any page remains pending.

An Umami outage does not undo a successful publish. The result shows `analytics.status: pending` and an error explaining what to check. Updating or restoring that page retries registration; the sync command can retry it directly. A timed-out creation is recovered by looking up its stable name before trying another create. Keep the `Publisher … |` prefix in Umami website names so this recovery lookup remains reliable.

## What visitors send

The viewer adds the tracker to the served HTML after access checks. It leaves stored HTML and version history unchanged. Password prompts, expired links, missing pages and redirects do not load the tracker. Password-protected pages track visits after a successful unlock.

Pageviews contain the page title, hostname, language, screen dimensions and a stable path such as `/artifacts/abc234`. Private link tokens, query strings, URL fragments and referrers are removed. The injected hook discards custom events and visitor-identification calls. Umami still receives the visitor's network request and derives its usual browser, device and location statistics. Browser tracking protection and Do Not Track can prevent visits from being counted.

Remove any manually embedded analytics scripts before relying on automatic tracking. Those scripts are part of the page's own content and are not controlled by this integration.

Unpublishing or deleting a page stops its viewer traffic but preserves historical statistics in Umami. Delete the website in Umami separately if you want to remove that history. Set `UMAMI_ENABLED=false` and redeploy both services to stop automatic registration and tracking.
