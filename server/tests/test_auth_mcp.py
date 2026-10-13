import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from publisher.auth import identity
from publisher.config import Settings
from publisher.mcp_server import create_mcp


def test_policy_and_cloud_dev_guard(monkeypatch):
    s = Settings(
        _env_file=None,
        allowed_email_domains="example.com",
        allowed_emails="other@test.com",
    )
    for email in ("person@example.com", "other@test.com"):
        assert identity(s, {"email": email, "email_verified": True})["allowed"]
    assert not identity(s, {"email": "person@example.com", "email_verified": "false"})[
        "allowed"
    ]
    blocked = identity(s, {"email": "person@evil.com", "email_verified": True})
    assert not blocked["allowed"] and "ALLOWED_EMAILS" in blocked["fix"]
    monkeypatch.setenv("K_SERVICE", "service")
    with pytest.raises(ValueError, match="forbidden"):
        Settings(_env_file=None, auth_mode="dev")


async def test_mcp_tool_lifecycle(service):
    async with Client(create_mcp(service)) as client:
        tools = await client.list_tools()
        assert len(tools) == 15 and all(t.annotations is not None for t in tools)
        assert (await client.call_tool("whoami")).data["allowed"]
        result = await client.call_tool(
            "publish_artifact",
            {"client": "Acme", "title": "Sample", "html": "<p>hello</p>"},
        )
        page = result.data
        assert page["version"] == 1
        with pytest.raises(ToolError, match="CONFLICT"):
            await client.call_tool(
                "publish_artifact",
                {"client": "Acme", "title": "Sample", "html": "again"},
            )
        updated = (
            await client.call_tool(
                "update_artifact",
                {
                    "artifact": page["id"],
                    "edits": [{"find": "hello", "replace": "world"}],
                },
            )
        ).data
        assert updated["version"] == 2 and updated["url"] == page["url"]
        assert (
            await client.call_tool(
                "get_artifact", {"artifact": page["url"], "include_html": True}
            )
        ).data["html"]
        await client.call_tool("list_clients")
        await client.call_tool("list_artifacts")
        await client.call_tool("list_domains")
        await client.call_tool("set_client_domain", {"client": "Acme", "domain": None})
        await client.call_tool(
            "set_artifact_domain", {"artifact": page["id"], "domain": None}
        )
        await client.call_tool(
            "set_access", {"artifact": page["id"], "mode": "password"}
        )
        await client.call_tool("list_versions", {"artifact": page["id"]})
        await client.call_tool(
            "rollback_artifact", {"artifact": page["id"], "version": 1}
        )
        await client.call_tool("unpublish_artifact", {"artifact": page["id"]})
        await client.call_tool("restore_artifact", {"artifact": page["id"]})
        await client.call_tool("unpublish_artifact", {"artifact": page["id"]})
        await client.call_tool(
            "delete_artifact_permanently",
            {"artifact": page["id"], "confirm_title": "Sample"},
        )


async def test_blocked_account_can_only_discover_whoami(service, monkeypatch):
    from types import SimpleNamespace

    import publisher.auth as auth

    server = create_mcp(service)
    service.settings.auth_mode = "google"
    monkeypatch.setattr(
        auth,
        "get_access_token",
        lambda: SimpleNamespace(
            claims={"email": "blocked@evil.com", "email_verified": True}
        ),
    )
    async with Client(server) as client:
        assert [t.name for t in await client.list_tools()] == ["whoami"]
        result = (await client.call_tool("whoami")).data
        assert not result["allowed"] and result["fix"]
        with pytest.raises(ToolError, match="FORBIDDEN"):
            await client.call_tool("list_clients")


def test_oauth_discovery_and_encrypted_named_store(service, monkeypatch):
    import key_value.aio.stores.firestore as firestore_store
    from cryptography.fernet import Fernet
    from key_value.aio.stores.memory import MemoryStore
    from starlette.testclient import TestClient

    seen = {}

    def store(**kwargs):
        seen.update(kwargs)
        return MemoryStore()

    monkeypatch.setattr(firestore_store, "FirestoreStore", store)
    service.settings.auth_mode = "google"
    service.settings.google_client_id = "test.apps.googleusercontent.com"
    service.settings.google_client_secret = "local-test-client-value"
    service.settings.jwt_signing_key = Fernet.generate_key().decode()
    service.settings.storage_encryption_key = Fernet.generate_key().decode()
    service.settings.api_url = "https://api.example.com"
    app = create_mcp(service).http_app(path="/mcp", stateless_http=True)
    assert seen["database"] == "artifact-publisher"
    with TestClient(app, base_url=service.settings.api_url) as client:
        response = client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1}
        )
        assert response.status_code == 401
        url = (
            response.headers["www-authenticate"]
            .split('resource_metadata="')[1]
            .split('"')[0]
        )
        metadata = client.get(url).json()
        assert metadata["resource"] == "https://api.example.com/mcp"
        auth = client.get("/.well-known/oauth-authorization-server").json()
        assert auth["registration_endpoint"]
        assert "S256" in auth["code_challenge_methods_supported"]


def test_claude_url_client_authorization_with_firestore(service, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cryptography.fernet import Fernet
    from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher
    from google.cloud import firestore
    from starlette.testclient import TestClient

    documents = {}
    reads = []

    class Collection:
        def __init__(self, name):
            self.name = name

        def document(self, key):
            # Model Firestore's document-ID restrictions at the SDK boundary;
            # keep the real store, encryption and provider code in the test.
            assert "/" not in key, "Firestore document IDs cannot contain slashes"
            path = (self.name, key)
            reads.append(path)

            async def get():
                return SimpleNamespace(
                    exists=path in documents,
                    to_dict=lambda: documents.get(path),
                )

            async def set_value(value):
                documents[path] = value

            return SimpleNamespace(get=get, set=set_value)

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        def collection(self, name):
            return Collection(name)

        def close(self):
            pass

    monkeypatch.setattr(firestore, "AsyncClient", FakeAsyncClient)
    client_id = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    callback = "https://claude.ai/api/mcp/auth_callback"
    monkeypatch.setattr(
        CIMDFetcher,
        "fetch",
        AsyncMock(
            return_value=CIMDDocument(
                client_id=client_id, client_name="Claude", redirect_uris=[callback]
            )
        ),
    )
    service.settings.auth_mode = "google"
    service.settings.google_client_id = "test.apps.googleusercontent.com"
    service.settings.google_client_secret = "local-test-client-value"
    service.settings.jwt_signing_key = Fernet.generate_key().decode()
    service.settings.storage_encryption_key = Fernet.generate_key().decode()
    service.settings.api_url = "https://api.example.com"
    app = create_mcp(service).http_app(path="/mcp", stateless_http=True)
    with TestClient(app, base_url=service.settings.api_url) as client:
        params = {
            "client_id": client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
            "state": "regression-test",
        }
        response = client.get("/authorize", params=params, follow_redirects=False)
        assert response.status_code == 302
        assert "/consent?" in response.headers["location"]
        consent = client.get(response.headers["location"])
        assert consent.status_code == 200 and "Claude" in consent.text
        assert documents  # The consent transaction persisted through encryption.
        assert any(name == "oauth___mcp-oauth-proxy-clients" for name, _ in reads)
        assert all("local-test-client-value" not in str(v) for v in documents.values())

        # Previously issued DCR client IDs must remain readable after the fix.
        registered = client.post(
            "/register",
            json={"redirect_uris": [callback], "token_endpoint_auth_method": "none"},
        )
        assert registered.status_code == 201
        old_id = registered.json()["client_id"]
        assert ("oauth___mcp-oauth-proxy-clients", old_id) in documents
        params["client_id"] = old_id
        response = client.get("/authorize", params=params, follow_redirects=False)
        assert response.status_code == 302
        assert "/consent?" in response.headers["location"]
