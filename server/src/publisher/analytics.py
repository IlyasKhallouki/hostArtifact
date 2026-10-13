"""Optional Umami registration and privacy-preserving pageview tracking."""

import hashlib
import html
import json
import re
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from publisher.domains import artifact_url


class AnalyticsError(Exception):
    pass


class Umami:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=8, follow_redirects=False)
        self.token = None

    def request(self, method, path, **kwargs):
        try:
            credentials = json.loads(self.settings.umami_credentials or "{}")
            for attempt in range(2):
                if not self.token:
                    self.token = credentials.get("api_key")
                    if not self.token:
                        if not credentials.get("username") or not credentials.get(
                            "password"
                        ):
                            raise AnalyticsError(
                                "Set UMAMI_CREDENTIALS to an API key or username/password JSON object."
                            )
                        login = self.client.post(
                            self.settings.umami_api_url.rstrip("/") + "/auth/login",
                            json={k: credentials[k] for k in ("username", "password")},
                        )
                        login.raise_for_status()
                        self.token = login.json()["token"]
                response = self.client.request(
                    method,
                    self.settings.umami_api_url.rstrip("/") + path,
                    headers={"Authorization": f"Bearer {self.token}"},
                    **kwargs,
                )
                if response.status_code == 401 and attempt == 0:
                    self.token = None
                    continue
                response.raise_for_status()
                return response.json()
        except AnalyticsError:
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise AnalyticsError(
                    "Umami denied access. Check the API credential and team website create/update permissions."
                ) from None
            raise AnalyticsError(
                f"Umami returned HTTP {status}; retry analytics sync."
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise AnalyticsError(
                "Umami could not be reached or returned an invalid response; retry analytics sync."
            ) from None

    def sync(self, item, domain):
        scope = hashlib.sha256(self.settings.api_url.encode()).hexdigest()[:10]
        marker = f"Publisher {scope}:{item['id']} | "
        name = (marker + item["client_slug"] + " / " + item["title"])[:100]
        website_id = item.get("analytics", {}).get("website_id")
        if not website_id:
            team = self.settings.umami_team_id
            route = f"/teams/{team}/websites" if team else "/websites"
            result = self.request(
                "GET", route, params={"query": marker, "pageSize": 100}
            )
            rows = result.get("data", []) if isinstance(result, dict) else result
            matches = [row for row in rows if row.get("name", "").startswith(marker)]
            if len(matches) > 1:
                raise AnalyticsError(
                    "Multiple Umami websites match this page; resolve duplicates before syncing."
                )
            if matches:
                website_id = matches[0]["id"]
        body = {"name": name, "domain": domain}
        if website_id:
            website_id = str(UUID(website_id))
            self.request("POST", f"/websites/{website_id}", json=body)
        else:
            if self.settings.umami_team_id:
                body["teamId"] = self.settings.umami_team_id
            result = self.request("POST", "/websites", json=body)
            website_id = str(UUID(result["id"]))
        return website_id


def summary(settings, item):
    if not settings.umami_enabled:
        return {"status": "disabled"}
    data = item.get("analytics", {})
    result = {"status": data.get("status", "pending")}
    if data.get("website_id"):
        result.update(
            website_id=data["website_id"],
            dashboard_url=settings.umami_url.rstrip("/")
            + "/websites/"
            + data["website_id"],
        )
    if data.get("error"):
        result["error"] = data["error"]
    return result


def sync_artifact(service, identifier):
    """Lease metadata first; never call an external API inside a retried transaction."""
    if not service.settings.umami_enabled:
        return
    lease = str(uuid4())

    def claim(state):
        item = state["artifacts"].get(identifier)
        if not item:
            return None
        data = item.setdefault("analytics", {})
        if data.get("lease_until", 0) > time.time():
            return None
        data.update(lease=lease, lease_until=time.time() + 180)
        return dict(item), urlsplit(
            artifact_url(state, item, service.settings)
        ).hostname

    claimed = service.metadata.transaction(claim)
    if claimed is None:
        return
    item, domain = claimed
    try:
        website_id = service.umami.sync(item, domain)
        outcome = {"status": "active", "website_id": website_id}
    except AnalyticsError as exc:
        outcome = {"status": "pending", "error": str(exc)}
    except Exception:
        # Publishing has already committed. No credentials or upstream response body
        # may escape through tool responses, metadata or logs.
        outcome = {
            "status": "pending",
            "error": "Analytics setup failed. Check Umami access and run publisher-admin analytics sync --apply.",
        }

    def finish(state):
        current = state["artifacts"].get(identifier)
        if current and current.get("analytics", {}).get("lease") == lease:
            data = current["analytics"]
            data.pop("lease", None)
            data.pop("lease_until", None)
            data.pop("error", None)
            data.update(outcome)

    service.metadata.transaction(finish)


def inject(settings, item, content):
    website_id = item.get("analytics", {}).get("website_id")
    if not settings.umami_enabled or not website_id:
        return content
    website_id = str(UUID(website_id))
    # Whitelist pageview fields. Never send bearer-link paths, query strings,
    # referrers, visitor identities or arbitrary user-authored event data.
    safe = json.dumps(
        {
            "website": website_id,
            "url": "/artifacts/" + item["id"],
            "title": item["title"],
        }
    ).replace("<", "\\u003c")
    script = f'''<script data-publisher-analytics="privacy">
window.publisherAnalyticsFilter = function(type, payload) {{
  if (type !== 'event' || payload.name) return false;
  return Object.assign({safe}, {{hostname: location.hostname, language: navigator.language, screen: screen.width + 'x' + screen.height, referrer: ''}});
}};
</script><script defer data-publisher-analytics="tracker" src="{html.escape(settings.umami_script_url, quote=True)}" data-website-id="{website_id}" data-before-send="publisherAnalyticsFilter" data-exclude-search="true" data-exclude-hash="true" data-do-not-track="true"></script>'''
    # Append after user head scripts, before body content is painted.
    match = re.search(r"</head\s*>", content, flags=re.I)
    return (
        content[: match.start()] + script + content[match.start() :]
        if match
        else script + content
    )
