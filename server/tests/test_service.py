from concurrent.futures import ThreadPoolExecutor

import pytest

from publisher.models import PublisherError


def publish(service, **kwargs):
    return service.publish_artifact(
        "dev@local", "Acme", "Dashboard", "<p>one</p>", **kwargs
    )


def test_lifecycle(service):
    page = publish(service)
    identifier, url = page["id"], page["url"]
    with pytest.raises(PublisherError, match="CONFLICT"):
        publish(service)
    duplicate = publish(service, allow_duplicate=True)
    assert duplicate["id"] != identifier
    assert service.get_artifact(url)["id"] == identifier
    updated = service.update_artifact(
        "dev@local", identifier, edits=[{"find": "one", "replace": "two"}]
    )
    assert updated["url"] == url and updated["version"] == 2
    with pytest.raises(PublisherError, match="matched 0"):
        service.update_artifact(
            "dev@local",
            identifier,
            edits=[
                {"find": "two", "replace": "three"},
                {"find": "absent", "replace": "x"},
            ],
        )
    assert service.get_artifact(identifier, True)["html"].count("two") == 1
    assert len(service.list_versions(identifier)) == 2
    service.rollback_artifact("dev@local", identifier, 1)
    assert "one" in service.get_artifact(identifier, True)["html"]
    assert service.get_artifact(identifier)["version"] == 3
    service.unpublish_artifact("dev@local", identifier)
    service.restore_artifact("dev@local", identifier)
    with pytest.raises(PublisherError, match="Unpublish first"):
        service.delete_artifact_permanently("dev@local", identifier, "Dashboard")
    service.unpublish_artifact("dev@local", identifier)
    with pytest.raises(PublisherError, match="FORBIDDEN"):
        service.delete_artifact_permanently(
            "other@example.com", identifier, "Dashboard"
        )
    assert service.delete_artifact_permanently("dev@local", identifier, "Dashboard")[
        "deleted"
    ]
    with pytest.raises(PublisherError, match="NOT_FOUND"):
        service.get_artifact(identifier)
    assert any(
        e["action"] == "delete" for e in service.metadata.read()["audit"].values()
    )


def test_password_rotation_and_expiry(service):
    page = publish(service, access="password")
    assert page["password"]
    assert "password" not in service.get_artifact(page["id"])
    item = service.metadata.read()["artifacts"][page["id"]]
    assert page["password"] not in str(service.metadata.read())
    assert item["password_hash"].startswith("$argon2id$")
    change = service.set_access(
        "dev@local", page["id"], expires_at="2020-01-01", rotate_link=True
    )
    assert change["warnings"] and change["url"] != page["url"]
    with pytest.raises(PublisherError, match="NOT_FOUND"):
        service.get_artifact(page["url"])
    assert (
        service.set_access("dev@local", page["id"], clear_expiry=True)["expires_at"]
        is None
    )


def test_domains_snapshot_and_defaults(service):
    def seed(state):
        state["domains"] = {
            "share.example.com": {
                "hostname": "share.example.com",
                "status": "active",
                "is_default": True,
            },
            "other.example.com": {
                "hostname": "other.example.com",
                "status": "active",
                "is_default": False,
            },
            "pending.example.com": {
                "hostname": "pending.example.com",
                "status": "pending_dns",
            },
        }

    service.metadata.transaction(seed)
    page = publish(service)
    assert page["url"].startswith("https://share.example.com/")
    service.set_client_domain("dev@local", "Acme", "other.example.com")
    assert service.get_artifact(page["id"])["url"] == page["url"]
    second = publish(service, allow_duplicate=True)
    assert second["url"].startswith("https://other.example.com/")
    with pytest.raises(PublisherError, match="active domain"):
        service.set_artifact_domain("dev@local", page["id"], "pending.example.com")
    moved = service.set_artifact_domain("dev@local", page["id"], "other.example.com")
    assert moved["previous_url"] == page["url"]
    assert service.get_artifact(page["url"])["id"] == page["id"]


def test_concurrent_updates_keep_linear_history(service):
    page = publish(service)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda n: service.update_artifact(
                    "dev@local", page["id"], html=f"<p>{n}</p>"
                ),
                range(8),
            )
        )
    assert sorted(r["version"] for r in results) == list(range(2, 10))
    assert len(service.list_versions(page["id"])) == 9


def test_html_chunking_and_cursor(service):
    page = publish(service)
    first = service.get_artifact(page["id"], True, max_chars=10)
    assert first["truncated"] and first["next_offset"] == 10
    publish(service, allow_duplicate=True)
    result = service.list_artifacts(limit=1)
    assert result["next_cursor"]
    next_page = service.list_artifacts(limit=1, cursor=result["next_cursor"])
    assert next_page["items"][0]["id"] != result["items"][0]["id"]


@pytest.mark.parametrize("password", ["", "short"])
def test_supplied_password_is_validated_not_silently_replaced(service, password):
    with pytest.raises(PublisherError, match="8–1024"):
        publish(service, access="password", password=password)
    assert service.metadata.read()["artifacts"] == {}
