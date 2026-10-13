import json
import subprocess
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient

from publisher.analytics import Umami, inject, sync_artifact
from publisher.viewer.app import create_viewer


@pytest.fixture
def analytics(service):
    service.settings.umami_enabled = True
    service.settings.umami_url = "https://analytics.example.com"
    service.settings.umami_api_url = "https://analytics.example.com/api"
    service.settings.umami_script_url = "https://analytics.example.com/script.js"
    service.settings.umami_credentials = json.dumps(
        {"username": "publisher", "password": "private-password"}
    )
    rows, calls = [], []
    flags = {"fail": False, "timeout_after_create": False}

    def handler(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, path, body))
        if flags["fail"]:
            return httpx.Response(403, text="private-password")
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "private-token"})
        assert request.headers["Authorization"] == "Bearer private-token"
        if request.method == "GET":
            return httpx.Response(200, json={"data": rows})
        if path == "/api/websites":
            row = {"id": str(uuid4()), **body}
            rows.append(row)
            if flags["timeout_after_create"]:
                flags["timeout_after_create"] = False
                raise httpx.ReadTimeout("private-password")
            return httpx.Response(200, json=row)
        row = next(row for row in rows if path.endswith(row["id"]))
        row.update(body)
        return httpx.Response(200, json=row)

    service.umami = Umami(
        service.settings, httpx.Client(transport=httpx.MockTransport(handler))
    )
    return service, rows, calls, flags


def publish(service, **kwargs):
    return service.publish_artifact(
        "dev@local", "Example", "Report", "<h1>Hello</h1>", **kwargs
    )


def test_registration_updates_and_link_rotation_preserve_history(analytics):
    service, rows, calls, _ = analytics
    page = publish(service)
    identifier = page["analytics"]["website_id"]
    assert page["analytics"]["status"] == "active"
    assert page["analytics"]["dashboard_url"].endswith(identifier)
    service.update_artifact("dev@local", page["id"], title="New title")
    service.set_access("dev@local", page["id"], rotate_link=True)
    service.set_artifact_domain("dev@local", page["id"], None)
    service.rollback_artifact("dev@local", page["id"], 1)
    assert len(rows) == 1
    assert rows[0]["id"] == identifier
    assert rows[0]["name"].endswith("New title")
    assert (
        sum(method == "POST" and path == "/api/websites" for method, path, _ in calls)
        == 1
    )


def test_outage_does_not_lose_page_or_password_and_retry_is_idempotent(analytics):
    service, rows, _, flags = analytics
    flags["fail"] = True
    page = publish(service, access="password", password="page-password")
    assert page["password"] == "page-password"
    assert page["analytics"]["status"] == "pending"
    assert "private-password" not in json.dumps(page)
    flags["fail"] = False
    flags["timeout_after_create"] = True
    sync_artifact(service, page["id"])
    assert len(rows) == 1
    assert service.get_artifact(page["id"])["analytics"]["status"] == "pending"
    sync_artifact(service, page["id"])
    assert len(rows) == 1
    assert service.get_artifact(page["id"])["analytics"]["status"] == "active"


def test_lease_prevents_concurrent_registration(analytics):
    service, rows, calls, _ = analytics
    page = publish(service)
    service.metadata.transaction(
        lambda state: state["artifacts"][page["id"]]["analytics"].update(
            lease_until=9999999999
        )
    )
    before = len(calls)
    sync_artifact(service, page["id"])
    assert len(calls) == before


def test_viewer_tracks_only_unlocked_content_and_stored_html_is_unchanged(analytics):
    service, _, _, _ = analytics
    page = publish(service, access="password", password="page-password")
    viewer = TestClient(create_viewer(service), base_url=service.settings.viewer_url)
    path = urlsplit(page["url"]).path
    assert "data-website-id" not in viewer.get(path).text
    response = viewer.post(path, data={"password": "page-password"})
    assert response.status_code == 200
    assert response.text.count("data-website-id=") == 1
    assert "private-password" not in response.text
    assert "private-token" not in response.text
    assert (
        "data-website-id"
        not in service.get_artifact(page["id"], include_html=True)["html"]
    )
    service.unpublish_artifact("dev@local", page["id"])
    assert "data-website-id" not in viewer.get(path).text


def test_tracking_etag_changes_on_backfill_and_disable(analytics):
    service, _, _, flags = analytics
    flags["fail"] = True
    page = publish(service)
    viewer = TestClient(create_viewer(service), base_url=service.settings.viewer_url)
    first = viewer.get(page["url"])
    assert "data-website-id" not in first.text
    flags["fail"] = False
    sync_artifact(service, page["id"])
    second = viewer.get(page["url"], headers={"if-none-match": first.headers["etag"]})
    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert "data-website-id" in second.text
    service.settings.umami_enabled = False
    third = viewer.get(page["url"], headers={"if-none-match": second.headers["etag"]})
    assert third.status_code == 200
    assert "data-website-id" not in third.text


def test_privacy_hook_removes_access_secrets_and_identity(analytics):
    service, _, _, _ = analytics
    page = publish(service)
    item = service.metadata.read()["artifacts"][page["id"]]
    item["title"] = "</script><script>alert(1)</script>"
    html = inject(service.settings, item, "<head></head>")
    script = html.split('privacy">', 1)[1].split("</script>", 1)[0]
    program = (
        """global.window={}; global.location={hostname:'viewer.example.com'};
    global.navigator={language:'en'}; global.screen={width:100,height:200};
    """
        + script
        + """
    const input={url:'/private-link?password=secret#key',referrer:'https://private',id:'email',data:{password:'secret'}};
    const clean=window.publisherAnalyticsFilter('event',input);
    if(JSON.stringify(clean).includes('secret') || clean.id || clean.data || clean.referrer) throw Error('leak');
    if(window.publisherAnalyticsFilter('identify',input)!==false) throw Error('identity');
    if(window.publisherAnalyticsFilter('event',{name:'secret'})!==false) throw Error('event');
    console.log(JSON.stringify(clean));"""
    )
    result = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout)["url"] == "/artifacts/" + page["id"]


def test_disabled_requires_no_credentials_or_tracker(service):
    page = publish(service)
    assert page["analytics"] == {"status": "disabled"}
    viewer = TestClient(create_viewer(service), base_url=service.settings.viewer_url)
    assert "data-website-id" not in viewer.get(page["url"]).text


def test_expired_api_token_reauthenticates_without_duplicate_create(analytics):
    service, rows, calls, _ = analytics
    original = service.umami.client
    rejected = []

    def handler(request):
        if request.headers.get("Authorization") == "Bearer expired":
            rejected.append(True)
            return httpx.Response(401)
        return original.send(request)

    service.umami.client = httpx.Client(transport=httpx.MockTransport(handler))
    service.umami.token = "expired"
    page = publish(service)
    assert page["analytics"]["status"] == "active"
    assert len(rows) == len(rejected) == 1


def test_team_lookup_finds_previously_created_page(analytics):
    service, rows, calls, _ = analytics
    service.settings.umami_team_id = str(uuid4())
    page = publish(service)
    identifier = page["analytics"]["website_id"]
    service.metadata.transaction(
        lambda state: state["artifacts"][page["id"]].pop("analytics")
    )
    sync_artifact(service, page["id"])
    assert len(rows) == 1
    assert service.get_artifact(page["id"])["analytics"]["website_id"] == identifier
    assert any("/teams/" in path for _, path, _ in calls)


def test_expired_and_redirected_pages_do_not_track(analytics):
    service, _, _, _ = analytics
    page = publish(service)
    viewer = TestClient(
        create_viewer(service),
        base_url=service.settings.viewer_url,
        follow_redirects=False,
    )
    service.set_access("dev@local", page["id"], expires_at="2020-01-01")
    response = viewer.get(page["url"])
    assert response.status_code == 410
    assert "data-website-id" not in response.text
    service.metadata.transaction(
        lambda state: state["domains"].update(
            {"share.example.com": {"status": "active", "is_default": True}}
        )
    )
    service.set_artifact_domain("dev@local", page["id"], "share.example.com")
    response = viewer.get(page["url"])
    assert response.status_code == 301
    assert "data-website-id" not in response.text


def test_analytics_metadata_failure_preserves_committed_publish(analytics, monkeypatch):
    from publisher import analytics as module

    service, _, _, _ = analytics

    def fail(*args):
        raise RuntimeError("private-password")

    monkeypatch.setattr(module, "sync_artifact", fail)
    page = publish(service, access="password", password="page-password")
    assert page["password"] == "page-password"
    assert page["analytics"]["status"] == "pending"
    assert "private-password" not in json.dumps(page)
    assert service.get_artifact(page["id"])["status"] == "live"
