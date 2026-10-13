"""Live storage/viewer validation using the operator identity; does not test OAuth."""

import argparse
import json
import secrets
import uuid

import httpx

from publisher.config import Settings
from publisher.service import ArtifactService
from publisher.storage import create_stores

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--apply",
    action="store_true",
    help="Create and delete a temporary acceptance page in the configured cloud project.",
)
args = parser.parse_args()
if not args.apply:
    parser.error("Pass --apply to run the live storage/viewer acceptance sequence.")
s = Settings()
if s.storage_backend != "gcp":
    parser.error("This check requires STORAGE_BACKEND=gcp.")
metadata, blobs = create_stores(s)
service = ArtifactService(metadata, blobs, s)
actor = s.admin_contact
client = "Acceptance " + uuid.uuid4().hex[:12]
identifier = None
checks = []
with httpx.Client(timeout=30, follow_redirects=False) as browser:
    try:
        response = browser.get(s.viewer_url + "/health")
        response.raise_for_status()
        assert response.json()["mode"] == "viewer"
        checks.append("live viewer health")
        item = service.publish_artifact(
            actor, client, "Cloud acceptance", "<h1>Before</h1>"
        )
        identifier, url = item["id"], item["url"]
        response = browser.get(url)
        assert response.status_code == 200, (response.status_code, response.text[:200])
        assert "Before" in response.text
        checks.append("GCS/Firestore publish and Cloud Run viewer read")
        updated = service.update_artifact(
            actor, identifier, edits=[{"find": "Before", "replace": "After"}]
        )
        assert updated["url"] == url and "After" in browser.get(url).text
        checks.append("update preserves URL")
        password = secrets.token_urlsafe(24)
        service.set_access(actor, identifier, mode="password", password=password)
        assert browser.get(url).status_code == 401
        assert browser.post(url, data={"password": "incorrect"}).status_code == 401
        assert browser.post(url, data={"password": password}).status_code == 303
        assert browser.get(url).status_code == 200
        checks.append("password denial and signed cookie success")
        service.set_access(actor, identifier, expires_at="2020-01-01")
        assert browser.get(url).status_code == 410
        service.set_access(actor, identifier, clear_expiry=True, mode="link")
        service.rollback_artifact(actor, identifier, 1)
        assert "Before" in browser.get(url).text
        checks.append("expiry and rollback")
        service.unpublish_artifact(actor, identifier)
        assert browser.get(url).status_code == 404
        service.restore_artifact(actor, identifier)
        assert browser.get(url).status_code == 200
        checks.append("unpublish and restore")
        rotated = service.set_access(actor, identifier, rotate_link=True)
        assert browser.get(url).status_code == 404
        assert browser.get(rotated["url"]).status_code == 200
        checks.append("link rotation")
    finally:
        if identifier:
            service.unpublish_artifact(actor, identifier)
            service.delete_artifact_permanently(actor, identifier, "Cloud acceptance")
            assert browser.get(url).status_code == 404
            checks.append("permanent deletion")

        def cleanup(state):
            for key, value in list(state["clients"].items()):
                if value["name"] == client and not any(
                    x["client_slug"] == key for x in state["artifacts"].values()
                ):
                    del state["clients"][key]

        metadata.transaction(cleanup)
print(
    json.dumps(
        {
            "ok": True,
            "checks": checks,
            "auth_note": "Operator ADC writes; public Cloud Run viewer reads. Google OAuth/MCP acceptance remains separate.",
        },
        indent=2,
    )
)
