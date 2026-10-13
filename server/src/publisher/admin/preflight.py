import shlex
import time
from pathlib import Path

import httpx
import yaml

from publisher.admin.gcloud import CloudError

BASE_APIS = [
    "run",
    "cloudbuild",
    "artifactregistry",
    "firestore",
    "storage",
    "secretmanager",
    "iam",
    "logging",
    "serviceusage",
    "cloudresourcemanager",
    "orgpolicy",
]
PROFILE_APIS = {
    "bootstrap": BASE_APIS,
    "deploy": BASE_APIS,
    "domains": ["run", "compute", "certificatemanager", "firestore"],
    "domain-mapping": ["run", "firestore"],
    "discover": ["run", "dns", "compute", "certificatemanager"],
    "registry": ["firestore"],
    "domain-status": ["firestore", "certificatemanager"],
    "domain-mapping-status": ["firestore", "run"],
}


def permission_request(http, method, url, **kwargs):
    """Retry transient read-only IAM probes without treating outages as denial."""
    for attempt in range(4):
        response = http.request(method, url, **kwargs)
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            return response
        try:
            delay = float(response.headers.get("Retry-After", 2**attempt))
        except ValueError:
            delay = 2**attempt
        time.sleep(max(0, min(delay, 30)))


def check(settings, cloud, profile="deploy", client=None):
    permissions = yaml.safe_load(
        Path(__file__).with_name("permissions.yaml").read_text()
    )[profile]
    report = {
        "project": settings.gcp_project_id,
        "profile": profile,
        "checks": [],
        "missing_permissions": [],
        "fixes": [],
    }
    checks = report["checks"]
    if not settings.gcp_project_id:
        report.update(
            ok=False,
            fixes=["Set GCP_PROJECT_ID in infra/config.env or the environment."],
        )
        checks.append({"name": "configuration", "status": "blocked"})
        return report
    bucket_permissions = {
        p: permissions.pop(p)
        for p in list(permissions)
        if p in {"storage.buckets.get", "storage.buckets.setIamPolicy"}
    }

    try:
        accounts = cloud.run("auth", "list")
        account = next(
            (a["account"] for a in accounts if a.get("status") == "ACTIVE"), None
        )
        if not account:
            raise CloudError([], "authentication_required")
        config = cloud.run("config", "list")
        impersonation = (
            config.get("auth", {}).get("impersonate_service_account")
            if isinstance(config, dict)
            else None
        )
        report["signed_in_account"] = account
        if impersonation:
            account = impersonation.split(",")[-1].strip()
        report["account"] = account
        principal = f"{'serviceAccount' if account.endswith('.gserviceaccount.com') else 'user'}:{account}"
        token = cloud.access_token()
    except CloudError as exc:
        checks.append(
            {"name": "authentication", "status": "blocked", "reason": exc.reason}
        )
        report.update(
            ok=False,
            fixes=["gcloud auth login", "gcloud auth application-default login"],
        )
        return report
    http = client or httpx.Client(timeout=30)
    try:
        response = permission_request(
            http,
            "POST",
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{settings.gcp_project_id}:testIamPermissions",
            headers={"Authorization": f"Bearer {token}"},
            json={"permissions": list(permissions)},
        )
        if response.status_code != 200:
            checks.append(
                {
                    "name": "project_permissions",
                    "status": "unknown",
                    "http_status": response.status_code,
                    "fix": "Ask a project IAM administrator to run preflight; permission-test failure is not proof of denial.",
                }
            )
        else:
            granted = set(response.json().get("permissions", []))
            missing = sorted(set(permissions) - granted)
            report["missing_permissions"] = [
                {
                    "permission": p,
                    "candidate_role": permissions[p],
                    "scope": f"projects/{settings.gcp_project_id}",
                }
                for p in missing
            ]
            checks.append(
                {
                    "name": "project_permissions",
                    "status": "blocked" if missing else "pass",
                }
            )
            if missing:
                # An administrator can create a narrowly scoped role; never grant it automatically.
                role_id = f"publisher_{profile.replace('-', '_')}_operator"
                report["fixes"] += [
                    "Ask a project IAM administrator to review and run these commands. Candidate predefined roles may grant more than the permissions listed.",
                    shlex.join(
                        [
                            "gcloud",
                            "iam",
                            "roles",
                            "create",
                            role_id,
                            f"--project={settings.gcp_project_id}",
                            "--title=Publisher missing permissions",
                            f"--permissions={','.join(missing)}",
                            "--stage=GA",
                        ]
                    ),
                    shlex.join(
                        [
                            "gcloud",
                            "projects",
                            "add-iam-policy-binding",
                            settings.gcp_project_id,
                            f"--member={principal}",
                            f"--role=projects/{settings.gcp_project_id}/roles/{role_id}",
                        ]
                    ),
                    "If this custom role already exists, review it and use roles update with --add-permissions for the listed missing permissions. IAM conditions, deny policies and unsupported custom-role permissions may require administrator review.",
                ]
        for bucket in (
            [settings.bucket, settings.bucket + "-build"] if bucket_permissions else []
        ):
            result = permission_request(
                http,
                "GET",
                f"https://storage.googleapis.com/storage/v1/b/{bucket}/iam/testPermissions",
                headers={"Authorization": f"Bearer {token}"},
                params=[("permissions", p) for p in bucket_permissions],
            )
            if result.status_code == 404 and profile == "bootstrap":
                checks.append(
                    {
                        "name": f"bucket:{bucket}",
                        "status": "deferred",
                        "reason": "Bucket not created yet; bootstrap checks create permissions and the API enforces bucket permissions after creation.",
                    }
                )
            elif result.status_code != 200:
                checks.append(
                    {
                        "name": f"bucket:{bucket}",
                        "status": "unknown",
                        "http_status": result.status_code,
                    }
                )
            else:
                missing_bucket = sorted(
                    set(bucket_permissions) - set(result.json().get("permissions", []))
                )
                checks.append(
                    {
                        "name": f"bucket:{bucket}",
                        "status": "blocked" if missing_bucket else "pass",
                        "missing": missing_bucket,
                    }
                )
                for permission in missing_bucket:
                    report["missing_permissions"].append(
                        {
                            "permission": permission,
                            "candidate_role": bucket_permissions[permission],
                            "scope": f"gs://{bucket}",
                        }
                    )
                if missing_bucket:
                    report["fixes"].append(
                        f"Ask the bucket IAM administrator to grant {', '.join(missing_bucket)} on gs://{bucket}; do not grant project-wide storage access."
                    )
        if profile == "deploy":
            for name in ("publisher-api", "publisher-viewer", "publisher-builder"):
                email = f"{name}@{settings.gcp_project_id}.iam.gserviceaccount.com"
                result = permission_request(
                    http,
                    "POST",
                    f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{email}:testIamPermissions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"permissions": ["iam.serviceAccounts.actAs"]},
                )
                allowed = (
                    result.status_code == 200
                    and "iam.serviceAccounts.actAs"
                    in result.json().get("permissions", [])
                )
                checks.append(
                    {
                        "name": f"actAs:{email}",
                        "status": "pass"
                        if allowed
                        else "blocked"
                        if result.status_code == 200
                        else "unknown",
                        "http_status": result.status_code,
                        "fix": None
                        if allowed
                        else shlex.join(
                            [
                                "gcloud",
                                "iam",
                                "service-accounts",
                                "add-iam-policy-binding",
                                email,
                                f"--member={principal}",
                                "--role=roles/iam.serviceAccountUser",
                                f"--project={settings.gcp_project_id}",
                            ]
                        ),
                    }
                )
    finally:
        if client is None:
            http.close()
    try:
        enabled = cloud.run("services", "list", "--enabled")
        enabled = {
            s.get("config", {}).get("name", s.get("name", "").split("/")[-1])
            for s in enabled
        }
        missing_apis = [
            f"{api}.googleapis.com"
            for api in PROFILE_APIS[profile]
            if f"{api}.googleapis.com" not in enabled
        ]
        checks.append(
            {
                "name": "enabled_apis",
                "status": "action_required" if missing_apis else "pass",
                "missing": missing_apis,
            }
        )
        if missing_apis:
            report["fixes"].append(
                shlex.join(
                    [
                        "gcloud",
                        "services",
                        "enable",
                        *missing_apis,
                        f"--project={settings.gcp_project_id}",
                    ]
                )
            )
    except CloudError as exc:
        checks.append(
            {"name": "enabled_apis", "status": "unknown", "reason": exc.reason}
        )
    constraints = (
        (
            "iam.allowedPolicyMemberDomains",
            "run.managed.requireInvokerIam",
            "gcp.resourceLocations",
        )
        if profile in {"bootstrap", "deploy", "domains", "domain-mapping"}
        else ()
    )
    for constraint in constraints:
        try:
            policy = cloud.run("org-policies", "describe", constraint, "--effective")
            rules = policy.get("spec", {}).get("rules", [])
            checks.append(
                {
                    "name": constraint,
                    "status": policy_status(constraint, rules),
                    "rules": rules,
                }
            )
        except CloudError as exc:
            checks.append(
                {
                    "name": constraint,
                    "status": "pass" if exc.reason == "not_found" else "unknown",
                    "reason": exc.reason,
                    "fix": f"gcloud services enable orgpolicy.googleapis.com --project={settings.gcp_project_id}"
                    if exc.reason == "api_disabled"
                    else "Ask an organization policy viewer to inspect the effective policy.",
                }
            )
    try:
        import google.auth

        credentials, _ = google.auth.default()
        checks.append(
            {
                "name": "application_default_credentials",
                "status": "pass" if credentials else "blocked",
            }
        )
    except Exception:
        checks.append(
            {
                "name": "application_default_credentials",
                "status": "blocked",
                "fix": "gcloud auth application-default login",
            }
        )
    report["ok"] = all(
        c["status"] == "pass"
        or (profile == "bootstrap" and c["status"] == "deferred")
        or (
            profile == "bootstrap"
            and c["name"] == "enabled_apis"
            and c["status"] == "action_required"
        )
        for c in checks
    )
    return report


def policy_status(constraint, rules):
    if not rules:
        return "pass"
    if constraint == "iam.allowedPolicyMemberDomains":
        # Cloud Run is exposed by disabling the invoker check, not an allUsers IAM grant.
        return "pass"
    if all(rule.get("allowAll") or rule.get("enforce") is False for rule in rules):
        return "pass"
    return "review"
