from urllib.parse import urlsplit

from starlette.testclient import TestClient

from publisher.viewer.app import create_viewer


def test_public_health_uses_cloud_run_compatible_path(service):
    with TestClient(create_viewer(service)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "mode": "viewer",
            "git_sha": service.settings.git_sha,
        }


def test_password_expiry_headers_and_rotation(service):
    page = service.publish_artifact(
        "dev@local",
        "Client",
        "Title",
        "<p>private</p>",
        access="password",
        password="correct-password",
    )
    path = urlsplit(page["url"]).path
    with TestClient(
        create_viewer(service),
        base_url=service.settings.viewer_url,
        follow_redirects=False,
    ) as client:
        assert client.get("/no-page").status_code == 404
        assert client.get(path).status_code == 401
        assert client.post(path, data={"password": "wrong"}).status_code == 401
        login = client.post(path, data={"password": "correct-password"})
        assert login.status_code == 303
        assert (
            "HttpOnly" in login.headers["set-cookie"]
            and "Secure" in login.headers["set-cookie"]
        )
        result = client.get(path)
        assert result.status_code == 200 and "private" in result.text
        assert result.headers["cache-control"] == "no-store"
        assert "noindex" in result.headers["x-robots-tag"]
        service.set_access(
            "dev@local", page["id"], mode="password", password="new-password"
        )
        assert client.get(path).status_code == 401
        service.set_access("dev@local", page["id"], expires_at="2020-01-01")
        assert client.get(path).status_code == 410
        service.unpublish_artifact("dev@local", page["id"])
        assert client.get(path).status_code == 404
        assert client.get("/robots.txt").text.endswith("Disallow: /\n")
        assert client.get("/", headers={"host": "untrusted.run.app"}).status_code == 404


def test_rate_limit(service):
    page = service.publish_artifact(
        "dev@local", "Client", "Title", "hello", access="password"
    )
    path = urlsplit(page["url"]).path
    with TestClient(
        create_viewer(service), base_url=service.settings.viewer_url
    ) as client:
        for _ in range(10):
            assert client.post(path, data={"password": "wrong"}).status_code == 401
        assert client.post(path, data={"password": "wrong"}).status_code == 429


def test_canonical_redirects_and_etag(service):
    service.metadata.transaction(
        lambda s: s["domains"].update(
            {"share.example.com": {"status": "active", "is_default": True}}
        )
    )
    page = service.publish_artifact("dev@local", "Client", "Title", "hello")
    path = urlsplit(page["url"]).path
    with TestClient(
        create_viewer(service),
        base_url=service.settings.viewer_url,
        follow_redirects=False,
    ) as client:
        assert client.get(path + "?a=1").headers["location"] == page["url"] + "?a=1"
        assert client.post(path).status_code == 307
        response = client.get(page["url"])
        assert response.status_code == 200
        assert (
            client.get(
                page["url"], headers={"if-none-match": response.headers["etag"]}
            ).status_code
            == 304
        )
        assert client.get(path.replace("title-", "wrong-")).status_code == 404
