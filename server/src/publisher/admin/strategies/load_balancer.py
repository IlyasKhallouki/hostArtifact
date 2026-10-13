import hashlib

from publisher.admin.gcloud import CloudError

MARKER = "Managed by artifact-publisher; do not edit manually"


class LoadBalancer:
    def __init__(self, settings, cloud):
        self.s, self.cloud = settings, cloud
        self.prefix = (
            "apub-"
            + hashlib.sha256(
                f"{settings.gcp_project_id}/{settings.viewer_service}/{settings.region}".encode()
            ).hexdigest()[:10]
        )

    def names(self, hostname):
        suffix = hashlib.sha256(hostname.encode()).hexdigest()[:12]
        return {
            "shared": self.prefix,
            "authorization": f"{self.prefix}-{suffix}-auth",
            "certificate": f"{self.prefix}-{suffix}-cert",
            "entry": f"{self.prefix}-{suffix}",
        }

    def plan(self, hostname):
        n = self.names(hostname)
        p, region = self.prefix, self.s.region
        return [
            [
                "compute",
                "addresses",
                "create",
                p,
                "--global",
                "--ip-version=IPV4",
                "--network-tier=PREMIUM",
                f"--description={MARKER}",
            ],
            [
                "compute",
                "network-endpoint-groups",
                "create",
                p,
                f"--region={region}",
                "--network-endpoint-type=serverless",
                f"--cloud-run-service={self.s.viewer_service}",
            ],
            [
                "compute",
                "backend-services",
                "create",
                p,
                "--global",
                "--load-balancing-scheme=EXTERNAL_MANAGED",
                "--protocol=HTTP",
                f"--description={MARKER}",
            ],
            [
                "compute",
                "backend-services",
                "add-backend",
                p,
                "--global",
                f"--network-endpoint-group={p}",
                f"--network-endpoint-group-region={region}",
            ],
            [
                "compute",
                "url-maps",
                "create",
                p,
                f"--default-service={p}",
                "--global",
                f"--description={MARKER}",
            ],
            ["certificate-manager", "maps", "create", p, f"--description={MARKER}"],
            [
                "certificate-manager",
                "dns-authorizations",
                "create",
                n["authorization"],
                f"--domain={hostname}",
                "--location=global",
                "--type=per-project-record",
                f"--description={MARKER}",
            ],
            [
                "certificate-manager",
                "certificates",
                "create",
                n["certificate"],
                f"--domains={hostname}",
                f"--dns-authorizations={n['authorization']}",
                "--location=global",
                f"--description={MARKER}",
            ],
            [
                "certificate-manager",
                "maps",
                "entries",
                "create",
                n["entry"],
                f"--map={p}",
                f"--hostname={hostname}",
                f"--certificates={n['certificate']}",
                f"--description={MARKER}",
            ],
            [
                "compute",
                "target-https-proxies",
                "create",
                p,
                f"--url-map={p}",
                f"--certificate-map={p}",
                "--global",
                f"--description={MARKER}",
            ],
            [
                "compute",
                "forwarding-rules",
                "create",
                p,
                "--global",
                "--load-balancing-scheme=EXTERNAL_MANAGED",
                "--network-tier=PREMIUM",
                f"--address={p}",
                f"--target-https-proxy={p}",
                "--ports=443",
                f"--description={MARKER}",
            ],
        ]

    def apply(self, hostname):
        for command in self.plan(hostname):
            if "add-backend" in command:
                current = self.cloud.run(
                    "compute", "backend-services", "describe", self.prefix, "--global"
                )
                groups = [b["group"] for b in current.get("backends", [])]
                expected = (
                    f"/regions/{self.s.region}/networkEndpointGroups/{self.prefix}"
                )
                if groups and groups != [
                    next((g for g in groups if g.endswith(expected)), "")
                ]:
                    raise CloudError(
                        command,
                        "resource_conflict",
                        "Backend contains an unexpected service",
                    )
                if not groups:
                    self.cloud.run(*command)
                continue
            index = command.index("create")
            name = command[index + 1]
            flags = [
                arg
                for arg in command[index + 2 :]
                if arg == "--global"
                or arg.startswith(("--region=", "--location=", "--map="))
            ]
            describe = [*command[:index], "describe", name, *flags]
            self.cloud.ensure(
                describe,
                command,
                expected=lambda obj, command=command: self.matches(command, obj),
            )
        n = self.names(hostname)
        authorization = self.cloud.run(
            "certificate-manager",
            "dns-authorizations",
            "describe",
            n["authorization"],
            "--location=global",
        )
        ip = self.cloud.run(
            "compute", "addresses", "describe", self.prefix, "--global"
        )["address"]
        record = authorization["dnsResourceRecord"]
        return {
            "gcp_refs": n,
            "records_required": [
                {"name": hostname, "type": "A", "value": ip},
                {
                    "name": record["name"],
                    "type": record["type"],
                    "value": record["data"],
                },
            ],
        }

    def ready(self, domain):
        cert = self.cloud.run(
            "certificate-manager",
            "certificates",
            "describe",
            domain["gcp_refs"]["certificate"],
            "--location=global",
        )
        return cert.get("managed", {}).get("state") == "ACTIVE"

    def matches(self, command, obj):
        # gcloud does not support descriptions for serverless NEGs. Their
        # deterministic name, region, type and exact service identify ownership.
        if command[1] != "network-endpoint-groups" and obj.get("description") != MARKER:
            return False
        flags = dict(
            arg[2:].split("=", 1)
            for arg in command
            if arg.startswith("--") and "=" in arg
        )
        kind = command[1]

        def ref_matches(value, expected):
            return value == expected or value.endswith("/" + expected)

        if kind == "network-endpoint-groups":
            return (
                obj.get("networkEndpointType") == "SERVERLESS"
                and obj.get("cloudRun", {}).get("service") == self.s.viewer_service
            )
        if kind == "backend-services":
            return (
                obj.get("loadBalancingScheme") == "EXTERNAL_MANAGED"
                and obj.get("protocol") == "HTTP"
            )
        if kind == "url-maps":
            return ref_matches(
                obj.get("defaultService", ""), self.prefix
            ) and not obj.get("hostRules")
        if kind == "dns-authorizations":
            return obj.get("domain") == flags["domain"]
        if kind == "certificates":
            return obj.get("managed", {}).get("domains") == [flags["domains"]] and any(
                ref_matches(v, flags["dns-authorizations"])
                for v in obj.get("managed", {}).get("dnsAuthorizations", [])
            )
        if kind == "maps" and command[2] == "entries":
            return (
                obj.get("hostname") == flags["hostname"]
                and len(obj.get("certificates", [])) == 1
                and ref_matches(obj["certificates"][0], flags["certificates"])
            )
        if kind == "target-https-proxies":
            return ref_matches(obj.get("urlMap", ""), self.prefix) and ref_matches(
                obj.get("certificateMap", ""), self.prefix
            )
        if kind == "forwarding-rules":
            return (
                obj.get("loadBalancingScheme") == "EXTERNAL_MANAGED"
                and ref_matches(obj.get("target", ""), self.prefix)
                and (obj.get("portRange") == "443-443" or obj.get("ports") == ["443"])
            )
        if kind == "addresses":
            return (
                obj.get("ipVersion") == "IPV4" and obj.get("networkTier") == "PREMIUM"
            )
        return True
