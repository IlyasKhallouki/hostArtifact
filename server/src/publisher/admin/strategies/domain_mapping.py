from publisher.admin.dns_providers.squarespace import base_domain
from publisher.admin.gcloud import CloudError

SUPPORTED_REGIONS = {
    "asia-east1",
    "asia-northeast1",
    "asia-southeast1",
    "europe-north1",
    "europe-west1",
    "europe-west4",
    "us-central1",
    "us-east1",
    "us-east4",
    "us-west1",
}


class DomainMapping:
    def __init__(self, settings, cloud):
        self.s, self.cloud = settings, cloud

    def plan(self, hostname):
        if self.s.region not in SUPPORTED_REGIONS:
            raise ValueError(
                "Cloud Run domain mappings are unavailable in this region; use load-balancer."
            )
        return [
            [
                "beta",
                "run",
                "domain-mappings",
                "create",
                f"--service={self.s.viewer_service}",
                f"--domain={hostname}",
                f"--region={self.s.region}",
            ]
        ]

    def describe(self, hostname):
        return self.cloud.run(
            "beta",
            "run",
            "domain-mappings",
            "describe",
            f"--domain={hostname}",
            f"--region={self.s.region}",
        )

    def apply(self, hostname):
        verified = self.cloud.run("domains", "list-user-verified")
        if not any(
            hostname == d["id"] or hostname.endswith("." + d["id"]) for d in verified
        ):
            raise CloudError(
                [],
                "domain_verification_required",
                "Verify the base domain at https://search.google.com/search-console/welcome with this Google account; then retry.",
            )
        self.cloud.ensure(
            [
                "beta",
                "run",
                "domain-mappings",
                "describe",
                f"--domain={hostname}",
                f"--region={self.s.region}",
            ],
            self.plan(hostname)[0],
            expected=lambda obj: (
                obj.get("spec", {}).get("routeName") == self.s.viewer_service
            ),
        )
        data = self.describe(hostname)
        return {
            "gcp_refs": {"mapping": hostname},
            "records_required": self.records(hostname, data),
        }

    @staticmethod
    def records(hostname, data):
        base = base_domain(hostname)
        records = []
        for record in data.get("status", {}).get("resourceRecords", []):
            name = record.get("name", "").rstrip(".")
            # Verified against a live Cloud Run mapping: 'demo' for demo.example.com.
            name = (
                base
                if name in {"", "@"}
                else name
                if name == base or name.endswith("." + base)
                else f"{name}.{base}"
            )
            records.append(
                {"name": name, "type": record["type"], "value": record["rrdata"]}
            )
        return records

    def ready(self, domain):
        return any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in self.describe(domain["hostname"])
            .get("status", {})
            .get("conditions", [])
        )
