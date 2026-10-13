import subprocess
from copy import deepcopy

import httpx
import pytest

from publisher.admin import dns_check
from publisher.admin.dns_providers.squarespace import instructions
from publisher.admin.domains import DomainAdmin, discover, validate_hostname
from publisher.admin.gcloud import CloudError, Gcloud, classify
from publisher.admin.preflight import PROFILE_APIS, check


class FakeCloud:
    def __init__(self):
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        if args[:2] == ("auth", "list"):
            return [{"account": "admin@example.com", "status": "ACTIVE"}]
        if args[:2] == ("services", "list"):
            return [
                {"config": {"name": f"{name}.googleapis.com"}}
                for name in PROFILE_APIS["bootstrap"]
            ]
        if args[0] == "org-policies":
            return {"spec": {"rules": [{"enforce": False}]}}
        if args[:2] == ("domains", "list-user-verified"):
            return [{"id": "example.co.uk"}, {"id": "example.com"}]
        if args[:2] == ("dns", "managed-zones"):
            raise CloudError(list(args), "api_disabled")
        return []

    def access_token(self):
        return "test-token"


def test_read_timeout_is_bounded_and_reported_without_raw_output(monkeypatch):
    messages = []

    def stalled(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(command, 30, stderr="private provider output")

    monkeypatch.setattr(subprocess, "run", stalled)
    cloud = Gcloud("example", progress=messages.append)
    with pytest.raises(CloudError) as caught:
        cloud.run("certificate-manager", "certificates", "describe", "example")
    assert caught.value.reason == "timeout"
    assert "30 seconds" in caught.value.detail
    assert "private provider output" not in str(caught.value) + caught.value.detail
    assert messages and "30s" in messages[0]


def test_cli_interrupt_is_concise(monkeypatch, capsys):
    from publisher.admin import cli

    monkeypatch.setattr("sys.argv", ["publisher-admin", "domains", "discover"])
    monkeypatch.setattr(
        cli, "discover", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert cli.main() == 130
    captured = capsys.readouterr()
    assert "Cancelled" in captured.err and "Traceback" not in captured.err


def test_status_preflight_does_not_require_deployment_policy_reads(
    service, monkeypatch
):
    monkeypatch.setattr("google.auth.default", lambda: (object(), "test-project"))
    service.settings.gcp_project_id = "test-project"
    cloud = FakeCloud()
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(403))
    ) as client:
        report = check(service.settings, cloud, "domain-status", client)
    assert not report["ok"]  # Real permission checks still run and fail closed.
    assert not any(c[0] == "org-policies" for c in cloud.calls)


def test_preflight_exact_missing_and_resource_scope(service, monkeypatch):
    monkeypatch.setattr("google.auth.default", lambda: (object(), "test-project"))
    service.settings.gcp_project_id = "test-project"
    service.settings.bucket = "test-bucket"

    def respond(request):
        if request.method == "GET":
            return httpx.Response(404)
        import json

        wanted = json.loads(request.content)["permissions"]
        return httpx.Response(
            200,
            json={
                "permissions": [p for p in wanted if p != "iam.serviceAccounts.create"]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        report = check(service.settings, FakeCloud(), "bootstrap", client)
    assert not report["ok"]
    assert [r["permission"] for r in report["missing_permissions"]] == [
        "iam.serviceAccounts.create"
    ]
    assert "--permissions=iam.serviceAccounts.create" in " ".join(report["fixes"])
    assert any(c["status"] == "deferred" for c in report["checks"])
    assert "roles/owner" not in str(report)


def test_permission_api_failure_is_unknown(service, monkeypatch):
    monkeypatch.setattr("google.auth.default", lambda: (object(), "test-project"))
    service.settings.gcp_project_id = "test-project"
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(403))
    ) as client:
        report = check(service.settings, FakeCloud(), "deploy", client)
    assert not report["ok"] and not report["missing_permissions"]
    assert report["checks"][0]["status"] == "unknown"


def test_permission_api_retries_throttling_without_reporting_missing(
    service, monkeypatch
):
    monkeypatch.setattr("google.auth.default", lambda: (object(), "test-project"))
    monkeypatch.setattr("publisher.admin.preflight.time.sleep", lambda _: None)
    service.settings.gcp_project_id = "test-project"
    attempts = 0

    def respond(request):
        nonlocal attempts
        import json

        if request.method == "GET":
            return httpx.Response(404)
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=json.loads(request.content))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        report = check(service.settings, FakeCloud(), "bootstrap", client)
    assert attempts == 3
    assert report["ok"] and not report["missing_permissions"]


def test_discovery_reports_partial_coverage(service):
    cloud = FakeCloud()
    data = discover(service.settings, cloud)
    assert not data["complete"]
    assert data["domains"][0]["suggested_hostname"] == "share.example.co.uk"
    assert any(s["status"] == "api_disabled" for s in data["sources"])
    assert not any("create" in c or "enable" in c for c in cloud.calls)


def test_dns_exact_sets_and_conflicts():
    records = [{"name": "share.example.com", "type": "A", "value": "192.0.2.1"}]
    assert dns_check.diff(
        records, query=lambda n, t: ["192.0.2.1"] if t == "A" else []
    )["ok"]
    result = dns_check.diff(
        records, query=lambda n, t: ["192.0.2.1", "192.0.2.2"] if t == "A" else []
    )
    assert not result["ok"] and result["records"][0]["extra"] == ["192.0.2.2"]
    result = dns_check.diff(
        records,
        query=lambda n, t: (
            ["192.0.2.1"] if t == "A" else ["old.example.com"] if t == "CNAME" else []
        ),
    )
    assert not result["ok"] and result["records"][0]["conflicts"]


def test_squarespace_relative_records():
    data = instructions(
        "example.co.uk",
        [
            {"name": "example.co.uk.", "type": "A", "value": "192.0.2.1"},
            {
                "name": "_acme-challenge.share.example.co.uk.",
                "type": "CNAME",
                "value": "token.authorize.certificatemanager.goog.",
            },
        ],
    )
    assert data["records"][0]["host"] == "@"
    assert data["records"][1]["host"] == "_acme-challenge.share"


@pytest.mark.parametrize(
    "hostname",
    [
        "https://example.com",
        "*.example.com",
        "127.0.0.1",
        "-bad.example.com",
        "foo.run.app",
    ],
)
def test_invalid_hostname(hostname):
    with pytest.raises(ValueError):
        validate_hostname(hostname)


def test_planning_no_cloud_mutations_and_default_removal(service):
    cloud = FakeCloud()
    admin = DomainAdmin(service.settings, cloud, service.metadata)
    before = deepcopy(service.metadata.read())
    plan = admin.plan("share.example.com")
    assert plan["commands"] and not cloud.calls
    assert service.metadata.read() == before
    service.metadata.transaction(
        lambda state: state["domains"].update(
            {
                "share.example.com": {"status": "active"},
                "other.example.com": {"status": "active"},
            }
        )
    )
    admin.set_default("share.example.com")
    with pytest.raises(ValueError, match="still referenced"):
        admin.remove("share.example.com")
    admin.set_default("other.example.com")
    assert (
        sum(
            d.get("is_default", False)
            for d in service.metadata.read()["domains"].values()
        )
        == 1
    )
    assert admin.remove("share.example.com")["removed"] == "share.example.com"


def test_ensure_never_creates_on_denial(monkeypatch):
    cloud = Gcloud("example")
    calls = []

    def run(*args):
        calls.append(args)
        raise CloudError(list(args), "permission_denied")

    monkeypatch.setattr(cloud, "run", run)
    with pytest.raises(CloudError):
        cloud.ensure(["describe"], ["create"])
    assert calls == [("describe",)]
    assert classify("SERVICE_DISABLED") == "api_disabled"
    assert (
        classify("ERROR: (gcloud.run.services.describe) Cannot find service [share]")
        == "not_found"
    )


def test_domain_mapping_relative_record_names():
    from publisher.admin.strategies.domain_mapping import DomainMapping

    result = DomainMapping.records(
        "share.example.co.uk",
        {
            "status": {
                "resourceRecords": [
                    {
                        "name": "share",
                        "type": "CNAME",
                        "rrdata": "ghs.googlehosted.com.",
                    }
                ]
            }
        },
    )
    assert result[0]["name"] == "share.example.co.uk"
    result = DomainMapping.records(
        "example.co.uk",
        {
            "status": {
                "resourceRecords": [{"name": "", "type": "A", "rrdata": "192.0.2.1"}]
            }
        },
    )
    assert result[0]["name"] == "example.co.uk"


def test_domain_status_requires_dns_certificate_and_own_https(service, monkeypatch):
    import publisher.admin.domains as module

    domain = {
        "hostname": "share.example.com",
        "strategy": "load-balancer",
        "status": "pending_dns",
        "records_required": [
            {"name": "share.example.com", "type": "A", "value": "192.0.2.1"}
        ],
        "gcp_refs": {"certificate": "test-cert"},
    }
    service.metadata.transaction(
        lambda state: state["domains"].update({"share.example.com": domain})
    )
    admin = DomainAdmin(service.settings, FakeCloud(), service.metadata)

    class Strategy:
        def ready(self, domain):
            return True

    monkeypatch.setattr(admin, "strategy", lambda name: Strategy())
    monkeypatch.setattr(module.dns_check, "diff", lambda *args, **kwargs: {"ok": False})
    assert admin.status("share.example.com")["status"] == "pending_dns"
    monkeypatch.setattr(module.dns_check, "diff", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        module.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200, json={"ok": True, "mode": "unrelated"}
        ),
    )
    assert admin.status("share.example.com")["status"] != "active"
    monkeypatch.setattr(
        module.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200, json={"ok": True, "mode": "viewer", "git_sha": "test-sha"}
        ),
    )
    assert admin.status("share.example.com")["status"] == "active"


def test_load_balancer_rejects_owned_but_misrouted_resource(service):
    from publisher.admin.strategies.load_balancer import LoadBalancer

    strategy = LoadBalancer(service.settings, FakeCloud())
    command = next(
        c
        for c in strategy.plan("share.example.com")
        if c[1] == "network-endpoint-groups"
    )
    assert not any(arg.startswith("--description=") for arg in command)
    assert not strategy.matches(
        command,
        {
            "networkEndpointType": "SERVERLESS",
            "cloudRun": {"service": "unrelated"},
        },
    )
    assert strategy.matches(
        command,
        {
            "networkEndpointType": "SERVERLESS",
            "cloudRun": {"service": service.settings.viewer_service},
        },
    )


def test_status_timeout_preserves_active_domain_and_returns_partial_checks(
    service, monkeypatch
):
    import publisher.admin.domains as module

    original = {
        "hostname": "share.example.com",
        "strategy": "load-balancer",
        "status": "active",
        "records_required": [],
    }
    service.metadata.transaction(
        lambda state: state["domains"].update({original["hostname"]: original})
    )
    admin = DomainAdmin(service.settings, FakeCloud(), service.metadata)

    class Unreachable:
        def ready(self, domain):
            raise CloudError(["certificate-manager"], "timeout", "Request timed out")

    monkeypatch.setattr(admin, "strategy", lambda _: Unreachable())
    monkeypatch.setattr(module.dns_check, "diff", lambda *args, **kwargs: {"ok": True})
    result = admin.status(original["hostname"])
    assert result["check_complete"] is False
    assert result["certificate_ready"] is None
    assert result["https_verified"] is None
    assert "timeout" in result["last_error"]
    assert service.metadata.read()["domains"][original["hostname"]] == original
