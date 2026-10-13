"""Reviewed plans by default; scoped idempotent provisioning and digest deployments."""

import argparse
import json
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

import yaml
from cryptography.fernet import Fernet

from publisher.admin.gcloud import CloudError, Gcloud
from publisher.admin.preflight import BASE_APIS, check
from publisher.config import ROOT, Settings

MARKER = "Managed by artifact-publisher"
SECRET_ENV = {
    "GOOGLE_CLIENT_ID": "publisher-oauth-client-id",
    "GOOGLE_CLIENT_SECRET": "publisher-oauth-client-secret",
    "JWT_SIGNING_KEY": "publisher-jwt-signing-key",
    "STORAGE_ENCRYPTION_KEY": "publisher-storage-encryption-key",
    "COOKIE_SIGNING_KEY": "publisher-cookie-signing-key",
}


def secret_env(settings):
    result = dict(SECRET_ENV)
    if settings.umami_enabled:
        result["UMAMI_CREDENTIALS"] = "publisher-umami-credentials"
    return result


def bootstrap(s, cloud, apply):
    p, r = s.gcp_project_id, s.region
    resources = [
        (
            ["artifacts", "repositories", "describe", s.ar_repo, f"--location={r}"],
            [
                "artifacts",
                "repositories",
                "create",
                s.ar_repo,
                f"--location={r}",
                "--repository-format=docker",
                f"--description={MARKER}",
            ],
        ),
        (
            ["storage", "buckets", "describe", f"gs://{s.bucket}"],
            [
                "storage",
                "buckets",
                "create",
                f"gs://{s.bucket}",
                f"--location={r}",
                "--uniform-bucket-level-access",
                "--public-access-prevention",
            ],
        ),
        (
            [
                "firestore",
                "databases",
                "describe",
                f"--database={s.firestore_database}",
            ],
            [
                "firestore",
                "databases",
                "create",
                f"--database={s.firestore_database}",
                f"--location={r}",
                "--type=firestore-native",
                "--delete-protection",
            ],
        ),
    ]
    for name in ("publisher-api", "publisher-viewer", "publisher-builder"):
        resources.append(
            (
                [
                    "iam",
                    "service-accounts",
                    "describe",
                    f"{name}@{p}.iam.gserviceaccount.com",
                ],
                ["iam", "service-accounts", "create", name, f"--display-name={MARKER}"],
            )
        )
    for secret in secret_env(s).values():
        resources.append(
            (
                ["secrets", "describe", secret],
                [
                    "secrets",
                    "create",
                    secret,
                    "--replication-policy=automatic",
                    "--labels=app=artifact-publisher",
                ],
            )
        )
    commands = [["services", "enable", *[f"{api}.googleapis.com" for api in BASE_APIS]]]
    bindings = []
    for name, role in (
        ("publisher-api", "roles/datastore.user"),
        ("publisher-viewer", "roles/datastore.viewer"),
        ("publisher-builder", "roles/logging.logWriter"),
    ):
        condition = (
            f'expression=resource.name == "projects/{p}/databases/{s.firestore_database}",title=publisher-database'
            if name != "publisher-builder"
            else "None"
        )
        bindings.append(
            [
                "projects",
                "add-iam-policy-binding",
                p,
                f"--member=serviceAccount:{name}@{p}.iam.gserviceaccount.com",
                f"--role={role}",
                f"--condition={condition}",
            ]
        )
    for name, role in (
        ("publisher-api", "roles/storage.objectAdmin"),
        ("publisher-viewer", "roles/storage.objectViewer"),
    ):
        bindings.append(
            [
                "storage",
                "buckets",
                "add-iam-policy-binding",
                f"gs://{s.bucket}",
                f"--member=serviceAccount:{name}@{p}.iam.gserviceaccount.com",
                f"--role={role}",
            ]
        )
    bindings.append(
        [
            "artifacts",
            "repositories",
            "add-iam-policy-binding",
            s.ar_repo,
            f"--location={r}",
            f"--member=serviceAccount:publisher-builder@{p}.iam.gserviceaccount.com",
            "--role=roles/artifactregistry.writer",
        ]
    )
    # A dedicated source bucket avoids changing project-wide default build buckets.
    source_bucket = f"{s.bucket}-build"
    resources.append(
        (
            ["storage", "buckets", "describe", f"gs://{source_bucket}"],
            [
                "storage",
                "buckets",
                "create",
                f"gs://{source_bucket}",
                f"--location={r}",
                "--uniform-bucket-level-access",
                "--public-access-prevention",
            ],
        )
    )
    bindings.append(
        [
            "storage",
            "buckets",
            "add-iam-policy-binding",
            f"gs://{source_bucket}",
            f"--member=serviceAccount:publisher-builder@{p}.iam.gserviceaccount.com",
            "--role=roles/storage.objectViewer",
        ]
    )
    for env, secret in secret_env(s).items():
        name = "publisher-viewer" if env == "COOKIE_SIGNING_KEY" else "publisher-api"
        bindings.append(
            [
                "secrets",
                "add-iam-policy-binding",
                secret,
                f"--member=serviceAccount:{name}@{p}.iam.gserviceaccount.com",
                "--role=roles/secretmanager.secretAccessor",
            ]
        )
    if not apply:
        return {
            "commands": commands + [create for _, create in resources] + bindings,
            "secrets": "Generate signing/encryption secrets through stdin; OAuth secrets remain empty until the administrator creates the client.",
            "note": "Existing resources are inspected before creation. No unrelated resource is changed.",
        }
    for command in commands:
        cloud.run(*command)
    for describe, create in resources:

        def expected(obj, describe=describe):
            if describe[0] == "storage":
                iam = obj.get("iamConfiguration", obj.get("iam_config", {}))
                return (
                    obj.get("location", "").lower() == r
                    and obj.get("projectNumber", obj.get("project_number"))
                    in {s.gcp_project_number, int(s.gcp_project_number)}
                    and (
                        iam.get("publicAccessPrevention") == "enforced"
                        or iam.get("public_access_prevention") == "enforced"
                        or obj.get("public_access_prevention") == "enforced"
                    )
                )
            if describe[0] == "firestore":
                return (
                    obj.get("locationId") == r and obj.get("type") == "FIRESTORE_NATIVE"
                )
            if describe[0] == "iam":
                return obj.get("displayName") == MARKER
            if describe[0] == "secrets":
                return obj.get("labels", {}).get("app") == "artifact-publisher"
            return obj.get("description") == MARKER

        cloud.ensure(describe, create, expected=expected)
    for command in bindings:
        cloud.run(*command)
    for env, secret in secret_env(s).items():
        if env.startswith("GOOGLE_") or env == "UMAMI_CREDENTIALS":
            continue
        versions = cloud.run(
            "secrets", "versions", "list", secret, "--filter=state:ENABLED", "--limit=1"
        )
        if not versions:
            value = (
                Fernet.generate_key().decode()
                if env == "STORAGE_ENCRYPTION_KEY"
                else secrets.token_urlsafe(48)
            )
            result = subprocess.run(
                [
                    "gcloud",
                    "secrets",
                    "versions",
                    "add",
                    secret,
                    "--data-file=-",
                    f"--project={p}",
                    "--quiet",
                ],
                check=False,
                input=value,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Could not write generated secret {secret}; inspect Secret Manager permissions."
                )
    return {
        "ok": True,
        "next": f"Create a Google OAuth web client with redirect {s.api_url}/auth/callback, then add client ID and secret as enabled versions in Secret Manager. See docs/ADMIN_GUIDE.md. Do not paste secrets in chat.",
    }


def deployment(s, cloud, apply, allow_dirty, service="all", pinned_image=None):
    modes = [
        ("api", s.api_service, 1, 2, 40),
        ("viewer", s.viewer_service, 0, 5, 80),
    ]
    modes = [row for row in modes if service in {"all", row[0]}]
    repository = f"{s.region}-docker.pkg.dev/{s.gcp_project_id}/{s.ar_repo}/server"
    if pinned_image and not re.fullmatch(
        re.escape(repository) + r"@sha256:[a-f0-9]{64}", pinned_image
    ):
        raise ValueError("Supply a sha256 digest from this publisher image repository.")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True
    ).strip()
    if dirty and not allow_dirty:
        raise ValueError(
            "Commit changes before deploying, or explicitly pass --allow-dirty."
        )
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True
    ).strip() + ("-dirty" if dirty else "")
    image = f"{repository}:{sha}"
    builder = f"projects/{s.gcp_project_id}/serviceAccounts/publisher-builder@{s.gcp_project_id}.iam.gserviceaccount.com"
    build = {
        "steps": [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": ["build", "-t", image, "."],
            }
        ],
        "images": [image],
        "serviceAccount": builder,
        "options": {"logging": "CLOUD_LOGGING_ONLY"},
    }
    env = {
        key.upper(): str(getattr(s, key))
        for key in (
            "gcp_project_id",
            "gcp_project_number",
            "region",
            "firestore_database",
            "bucket",
            "api_service",
            "viewer_service",
            "api_url",
            "viewer_url",
            "org_display_name",
            "plugin_display_name",
            "admin_contact",
            "allowed_email_domains",
            "allowed_emails",
            "admin_emails",
            "default_access_mode",
            "max_html_bytes",
            "umami_enabled",
            "umami_url",
            "umami_api_url",
            "umami_script_url",
            "umami_team_id",
        )
    }
    env.update(GIT_SHA=sha, STORAGE_BACKEND="gcp", AUTH_MODE="google")
    if not apply:
        return {
            "image": pinned_image or image,
            "build": None if pinned_image else build,
            "services": [row[1] for row in modes],
            "runtime_env": env,
            "note": "Resolve built image digest once, pin secret versions, deploy that same digest to both services. Save previous revisions for rollback.",
        }
    secret_versions = {}
    deploy_secrets = secret_env(s)
    for env_name, secret in deploy_secrets.items():
        if service != "all" and (env_name == "COOKIE_SIGNING_KEY") != (
            service == "viewer"
        ):
            continue
        versions = cloud.run(
            "secrets",
            "versions",
            "list",
            secret,
            "--filter=state:ENABLED",
            "--sort-by=~createTime",
            "--limit=1",
        )
        if not versions:
            raise ValueError(
                f"Missing enabled secret version: {secret}. Complete OAuth/secret setup first."
            )
        secret_versions[env_name] = f"{secret}:{versions[0]['name'].rsplit('/', 1)[-1]}"
    for _, name, *_ in modes:
        try:
            existing = cloud.run(
                "run", "services", "describe", name, f"--region={s.region}"
            )
            if (
                existing.get("metadata", {}).get("labels", {}).get("app")
                != "artifact-publisher"
            ):
                raise ValueError(
                    f"Refusing to overwrite unrelated Cloud Run service {name}."
                )
        except CloudError as exc:
            if exc.reason != "not_found":
                raise
    previous = {}
    for _, name, *_ in modes:
        try:
            previous[name] = (
                cloud.run("run", "services", "describe", name, f"--region={s.region}")
                .get("status", {})
                .get("traffic", [])
            )
        except CloudError as exc:
            if exc.reason != "not_found":
                raise
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    rollback = out / f"rollback-{sha}-{service}.json"
    rollback.write_text(json.dumps(previous, indent=2))
    with tempfile.TemporaryDirectory() as directory:
        build_file = Path(directory) / "build.yaml"
        build_file.write_text(yaml.safe_dump(build))
        if not pinned_image:
            cloud.run(
                "builds",
                "submit",
                str(ROOT / "server"),
                f"--config={build_file}",
                f"--gcs-source-staging-dir=gs://{s.bucket}-build/source",
            )
            digest = cloud.run("artifacts", "docker", "images", "describe", image)[
                "image_summary"
            ]["digest"]
            pinned_image = repository + "@" + digest
        for mode, name, minimum, maximum, concurrency in modes:
            env_file = Path(directory) / f"{mode}.yaml"
            env_file.write_text(yaml.safe_dump({**env, "APP_MODE": mode}))
            selected = {
                k: v
                for k, v in secret_versions.items()
                if (k == "COOKIE_SIGNING_KEY") == (mode == "viewer")
            }
            args = [
                "run",
                "deploy",
                name,
                f"--region={s.region}",
                f"--image={pinned_image}",
                f"--service-account=publisher-{mode}@{s.gcp_project_id}.iam.gserviceaccount.com",
                f"--env-vars-file={env_file}",
                "--set-secrets=" + ",".join(f"{k}={v}" for k, v in selected.items()),
                f"--min-instances={minimum}",
                f"--max-instances={maximum}",
                f"--concurrency={concurrency}",
                "--cpu=1",
                "--memory=512Mi",
                "--timeout=300",
                "--cpu-boost",
                "--ingress=all",
                "--no-invoker-iam-check",
                f"--labels=app=artifact-publisher,git-sha={sha}",
            ]
            cloud.run(*args)
    return {
        "ok": True,
        "git_sha": sha,
        "image": pinned_image,
        "api_url": s.api_url,
        "viewer_url": s.viewer_url,
        "services": [row[1] for row in modes],
        "rollback": str(rollback),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap", "deploy"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--service", choices=["all", "api", "viewer"], default="all")
    parser.add_argument(
        "--image", help="Reuse an already-built publisher sha256 digest."
    )
    args = parser.parse_args()
    s = Settings()
    cloud = Gcloud(s.gcp_project_id)
    apply = args.apply and not args.dry_run
    if apply:
        report = check(s, cloud, args.action)
        if not report["ok"]:
            print(json.dumps(report, indent=2))
            return 2
    try:
        result = (
            bootstrap(s, cloud, apply)
            if args.action == "bootstrap"
            else deployment(s, cloud, apply, args.allow_dirty, args.service, args.image)
        )
        print(json.dumps(result, indent=2))
        return 0
    except (CloudError, ValueError, RuntimeError) as exc:
        print(
            json.dumps({"error": str(exc), "detail": getattr(exc, "detail", "")}),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
