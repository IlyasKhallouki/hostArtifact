import argparse
import json
import sys

from publisher.admin.domains import DomainAdmin, discover
from publisher.admin.gcloud import CloudError, Gcloud
from publisher.admin.preflight import check
from publisher.config import Settings


def progress(message):
    print(f"[publisher] {message}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Artifact publisher administration. Defaults to read-only plans; --apply changes cloud state."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument(
        "--profile",
        choices=["bootstrap", "deploy", "domains", "domain-mapping", "discover"],
        default="deploy",
    )
    pre.add_argument(
        "--json", action="store_true", help="Output is always structured JSON"
    )
    analytics = sub.add_parser("analytics")
    analytics.add_argument("action", choices=["sync"])
    analytics.add_argument("--artifact", help="Sync one page ID; defaults to all pages")
    analytics.add_argument("--apply", action="store_true")
    domains = sub.add_parser("domains")
    actions = domains.add_subparsers(dest="action", required=True)
    actions.add_parser("discover")
    actions.add_parser("list")
    for name in ("add", "status", "default", "remove"):
        action = actions.add_parser(name)
        action.add_argument("hostname")
        action.add_argument(
            "--apply", action="store_true", help="Apply changes after preflight"
        )
        action.add_argument("--dry-run", action="store_true")
        if name == "add":
            action.add_argument(
                "--strategy", choices=["load-balancer", "domain-mapping"]
            )
            action.add_argument("--root-redirect")
    args = parser.parse_args()
    settings = Settings()
    cloud = Gcloud(settings.gcp_project_id, progress=progress)
    try:
        if args.command == "analytics":
            from publisher.analytics import sync_artifact
            from publisher.service import ArtifactService
            from publisher.storage import create_stores

            if not settings.umami_enabled:
                raise ValueError(
                    "Set UMAMI_ENABLED=true and UMAMI_URL before syncing analytics."
                )
            metadata, blobs = create_stores(settings)
            service = ArtifactService(metadata, blobs, settings)
            state = metadata.read()
            identifiers = [args.artifact] if args.artifact else list(state["artifacts"])
            if any(identifier not in state["artifacts"] for identifier in identifiers):
                raise ValueError("Unknown artifact ID")
            if not args.apply:
                print(
                    json.dumps(
                        {
                            "artifacts": identifiers,
                            "action": "Register or update Umami websites. Pass --apply to save.",
                        },
                        indent=2,
                    )
                )
                return 0
            results = []
            for identifier in identifiers:
                progress(f"Syncing analytics for {identifier}")
                sync_artifact(service, identifier)
                results.append(
                    {"id": identifier, **service.get_artifact(identifier)["analytics"]}
                )
            print(json.dumps({"analytics": results}, indent=2))
            return 0 if all(row["status"] == "active" for row in results) else 2
        if args.command == "preflight":
            result = check(settings, cloud, args.profile)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 2
        if args.action == "discover":
            print(json.dumps(discover(settings, cloud), indent=2))
            return 0
        from publisher.storage.firestore_store import FirestoreMetadataStore
        from publisher.storage.local_store import LocalMetadataStore

        store = (
            LocalMetadataStore(settings.local_data)
            if settings.storage_backend == "local"
            else FirestoreMetadataStore(
                settings.gcp_project_id, settings.firestore_database
            )
        )
        admin = DomainAdmin(settings, cloud, store, progress=progress)
        if args.action == "list":
            result = {"domains": list(store.read()["domains"].values())}
        elif not args.apply or args.dry_run:
            result = (
                admin.plan(args.hostname, args.strategy, args.root_redirect)
                if args.action == "add"
                else {
                    "action": args.action,
                    "hostname": args.hostname,
                    "note": "Pass --apply to update the registry. status checks DNS, certificate and HTTPS before updating readiness.",
                }
            )
        else:
            strategy = getattr(args, "strategy", None) or settings.domain_strategy
            if args.action in {"default", "remove"}:
                profile = "registry"
            elif args.action == "status":
                progress("Reading the domain registry")
                existing = store.read()["domains"].get(args.hostname, {})
                profile = (
                    "domain-status"
                    if existing.get("strategy", strategy) == "load-balancer"
                    else "domain-mapping-status"
                )
            else:
                profile = "domains" if strategy == "load-balancer" else "domain-mapping"
            progress(f"Checking required permissions ({profile})")
            report = check(settings, cloud, profile)
            if not report["ok"]:
                print(json.dumps(report, indent=2))
                return 2
            if args.action == "add":
                result = admin.add(
                    args.hostname, report["account"], args.strategy, args.root_redirect
                )
            elif args.action == "status":
                result = admin.status(args.hostname)
            elif args.action == "default":
                result = admin.set_default(args.hostname)
            else:
                result = admin.remove(args.hostname)
        print(json.dumps(result, indent=2))
        return 2 if result.get("check_complete") is False else 0
    except KeyboardInterrupt:
        print(
            "Cancelled. Rerun the command to check the current state.", file=sys.stderr
        )
        return 130
    except (CloudError, ValueError) as exc:
        print(
            json.dumps({"error": str(exc), "detail": getattr(exc, "detail", "")}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
