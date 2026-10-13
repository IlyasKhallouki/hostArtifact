import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publisher_cloud_infra",
    Path(__file__).resolve().parents[2] / "scripts/cloud_infra.py",
)
infra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(infra)


def test_bootstrap_plan_scopes_roles_and_does_not_mutate(service):
    class Cloud:
        def run(self, *args):
            raise AssertionError("Dry-run must not call mutation APIs")

    settings = service.settings
    settings.gcp_project_id = "test-project"
    settings.bucket = "test-publisher"
    plan = infra.bootstrap(settings, Cloud(), False)
    commands = plan["commands"]
    assert any("--public-access-prevention" in c for c in commands)
    assert any("--database=artifact-publisher" in c for c in commands)
    datastore = [
        c
        for c in commands
        if any(arg.startswith("--role=roles/datastore.") for arg in c)
    ]
    assert len(datastore) == 2
    assert all(
        any("/databases/artifact-publisher" in arg for arg in c) for c in datastore
    )
    secret_grants = [
        c for c in commands if c[:2] == ["secrets", "add-iam-policy-binding"]
    ]
    assert len(secret_grants) == 5
    assert all(
        ("publisher-viewer" in " ".join(c)) == (c[2] == "publisher-cookie-signing-key")
        for c in secret_grants
    )
    assert "roles/owner" not in str(commands)


def test_viewer_can_deploy_pinned_image_without_oauth(service, monkeypatch, tmp_path):
    monkeypatch.setattr(infra, "ROOT", tmp_path)
    monkeypatch.setattr(
        infra.subprocess,
        "check_output",
        lambda command, **kwargs: "" if command[1] == "status" else "abc123456789",
    )
    calls = []

    class Cloud:
        def run(self, *args):
            calls.append(args)
            if args[:3] == ("secrets", "versions", "list"):
                assert args[3] == "publisher-cookie-signing-key"
                return [{"name": "versions/1"}]
            if args[:3] == ("run", "services", "describe"):
                raise infra.CloudError(list(args), "not_found")
            return {}

    s = service.settings
    s.gcp_project_id = "test-project"
    image = (
        f"{s.region}-docker.pkg.dev/test-project/{s.ar_repo}/server@sha256:" + "a" * 64
    )
    result = infra.deployment(s, Cloud(), True, False, "viewer", image)
    assert result["image"] == image
    deploys = [c for c in calls if c[:2] == ("run", "deploy")]
    assert len(deploys) == 1 and deploys[0][2] == s.viewer_service
    assert not any(c[:2] == ("builds", "submit") for c in calls)
    with pytest.raises(ValueError, match="digest"):
        infra.deployment(
            s, Cloud(), True, False, "viewer", image.split("@")[0] + ":latest"
        )
