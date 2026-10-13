import copy
import ipaddress
import re
from urllib.parse import urlsplit

import httpx

from publisher.admin import dns_check
from publisher.admin.dns_providers.squarespace import base_domain, instructions
from publisher.admin.gcloud import CloudError
from publisher.admin.strategies.domain_mapping import DomainMapping
from publisher.admin.strategies.load_balancer import LoadBalancer
from publisher.models import now


def validate_hostname(value):
    value = value.strip().rstrip(".").lower().encode("idna").decode()
    if (
        len(value) > 253
        or "." not in value
        or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        )
    ):
        raise ValueError(
            "Use a hostname such as share.example.com, without a scheme, path or wildcard."
        )
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("Use a registered domain, not an IP address.")
    if not base_domain(value) or value.endswith(".run.app"):
        raise ValueError("Use your own registered domain.")
    return value


def discover(settings, cloud):
    found = {}
    sources = []

    def query(name, args, extract):
        try:
            data = cloud.run(*args)
            for hostname in extract(data):
                hostname = hostname.removeprefix("*.").rstrip(".").lower()
                if hostname:
                    found.setdefault(hostname, set()).add(name)
            sources.append({"source": name, "status": "ok"})
            return data
        except CloudError as exc:
            sources.append(
                {"source": name, "status": exc.reason, "command": exc.command}
            )
            return []

    query(
        "account_verified_domains",
        ["domains", "list-user-verified"],
        lambda data: [d["id"] for d in data],
    )
    query(
        "cloud_dns",
        ["dns", "managed-zones", "list"],
        lambda data: [d["dnsName"] for d in data],
    )
    query(
        "load_balancer_hosts",
        ["compute", "url-maps", "list"],
        lambda data: [
            h
            for d in data
            for rule in d.get("hostRules", [])
            for h in rule.get("hosts", [])
            if h != "*"
        ],
    )
    query(
        "compute_certificates",
        ["compute", "ssl-certificates", "list"],
        lambda data: [
            h
            for d in data
            for h in d.get("managed", {}).get(
                "domains", d.get("subjectAlternativeNames", [])
            )
        ],
    )
    query(
        "certificate_manager",
        ["certificate-manager", "certificates", "list", "--location=-"],
        lambda data: [
            h
            for d in data
            for h in d.get("managed", {}).get("domains", d.get("sanDnsnames", []))
        ],
    )
    query(
        "dns_authorizations",
        ["certificate-manager", "dns-authorizations", "list", "--location=-"],
        lambda data: [d["domain"] for d in data],
    )
    maps = query(
        "certificate_maps",
        ["certificate-manager", "maps", "list", "--location=global"],
        lambda data: [],
    )
    for item in maps:
        name = item["name"].rsplit("/", 1)[-1]
        query(
            f"certificate_map_entries:{name}",
            ["certificate-manager", "maps", "entries", "list", f"--map={name}"],
            lambda data: [entry["hostname"] for entry in data if entry.get("hostname")],
        )
    services = query("cloud_run_services", ["run", "services", "list"], lambda data: [])
    regions = {
        s.get("metadata", {}).get("labels", {}).get("cloud.googleapis.com/location")
        for s in services
    } | {settings.region}
    # Also query all regions that support domain mapping, including those with no current services.
    from publisher.admin.strategies.domain_mapping import SUPPORTED_REGIONS

    for region in sorted((regions | SUPPORTED_REGIONS) - {None}):
        query(
            f"run_mappings:{region}",
            ["beta", "run", "domain-mappings", "list", f"--region={region}"],
            lambda data: [d["metadata"]["name"] for d in data],
        )
    return {
        "project": settings.gcp_project_id,
        "scope": "Selected project plus domains verified for the active Google account; inaccessible sources are listed explicitly.",
        "complete": all(s["status"] == "ok" for s in sources),
        "sources": sources,
        "domains": [
            {
                "hostname": host,
                "base_domain": base_domain(host),
                "sources": sorted(provenance),
                "suggested_hostname": f"{settings.suggested_subdomain}.{base_domain(host)}",
            }
            for host, provenance in sorted(found.items())
        ],
    }


class DomainAdmin:
    def __init__(self, settings, cloud, store, progress=None):
        self.s, self.cloud, self.store = settings, cloud, store
        self.progress = progress or (lambda message: None)

    def strategy(self, name):
        return (LoadBalancer if name == "load-balancer" else DomainMapping)(
            self.s, self.cloud
        )

    def plan(self, hostname, strategy=None, root_redirect=None):
        hostname = validate_hostname(hostname)
        if root_redirect and (
            urlsplit(root_redirect).scheme != "https"
            or not urlsplit(root_redirect).hostname
            or urlsplit(root_redirect).username
        ):
            raise ValueError(
                "Root redirects must be an absolute HTTPS URL without credentials."
            )
        strategy = strategy or self.s.domain_strategy
        return {
            "hostname": hostname,
            "strategy": strategy,
            "root_redirect": root_redirect,
            "commands": self.strategy(strategy).plan(hostname),
            "dns_provider": self.s.dns_provider_default,
            "note": "Exact DNS values are returned after Google reserves the address and creates the authorization; dry-run never invents values.",
        }

    def add(self, hostname, actor, strategy=None, root_redirect=None):
        plan = self.plan(hostname, strategy, root_redirect)
        hostname, strategy = plan["hostname"], plan["strategy"]
        existing = self.store.read()["domains"].get(hostname)
        if existing and existing["strategy"] != strategy:
            raise ValueError(
                "Domain already registered with another strategy; migration requires an explicit plan."
            )
        before = copy.deepcopy(existing)
        domain = existing or {
            "hostname": hostname,
            "base_domain": base_domain(hostname),
            "strategy": strategy,
            "status": "pending_dns",
            "dns_provider": self.s.dns_provider_default,
            "is_default": False,
            "created_at": now(),
            "created_by": actor,
        }
        domain.update(root_redirect=root_redirect, last_checked_at=now())
        try:
            domain.update(self.strategy(strategy).apply(hostname))
            domain.update(
                status="active"
                if before and before.get("status") == "active"
                else "pending_dns",
                last_error=None,
            )
        except CloudError as exc:
            domain.update(
                status="pending_verification"
                if exc.reason == "domain_verification_required"
                else "error",
                last_error=str(exc),
                last_checked_at=now(),
            )
            self._save(domain, before)
            raise
        self._save(domain, before)
        return {
            **domain,
            "dns_instructions": instructions(hostname, domain["records_required"]),
            "dns_check": dns_check.diff(domain["records_required"]),
        }

    def status(self, hostname):
        hostname = validate_hostname(hostname)
        self.progress(f"Reading registry entry for {hostname}")
        domain = self.store.read()["domains"].get(hostname)
        if not domain or domain["status"] == "removed":
            raise ValueError("Domain is not attached. Use domains add first.")
        before = copy.deepcopy(domain)
        # Mappings can return DNS records asynchronously after creation.
        if (
            not domain.get("records_required")
            and domain["strategy"] == "domain-mapping"
        ):
            data = self.strategy("domain-mapping").describe(hostname)
            domain["records_required"] = DomainMapping.records(hostname, data)
        self.progress("Checking DNS records with Google")
        dns = dns_check.diff(domain.get("records_required", []))
        self.progress("Checking DNS records with Cloudflare")
        second_dns = dns_check.diff(
            domain.get("records_required", []),
            query=lambda name, kind: dns_check.lookup(
                name, kind, resolver="https://cloudflare-dns.com/dns-query"
            ),
        )
        ready = None
        error = None
        self.progress("Checking Google certificate state")
        try:
            ready = self.strategy(domain["strategy"]).ready(domain)
        except (CloudError, KeyError) as exc:
            error = f"{exc}. {getattr(exc, 'detail', '')}".strip()
        https_ok = False
        if dns["ok"] and second_dns["ok"] and ready:
            self.progress("Checking valid HTTPS against the deployed viewer")
            try:
                response = httpx.get(
                    f"https://{hostname}/health", timeout=15, follow_redirects=False
                )
                data = response.json()
                expected = httpx.get(self.s.viewer_url + "/health", timeout=15).json()
                https_ok = (
                    response.status_code == 200
                    and data.get("mode") == "viewer"
                    and data.get("git_sha") == expected.get("git_sha")
                    and data.get("ok") is True
                )
            except (httpx.HTTPError, ValueError):
                error = "HTTPS check failed; TLS must be valid and reach this publisher viewer."
        dns_errors = any(
            record.get("error")
            for result in (dns, second_dns)
            for record in result.get("records", [])
        )
        if error or dns_errors:
            # A failed observation is not evidence that an active domain broke.
            # Preserve the registry and return partial checks with a failure exit.
            return {
                **domain,
                "check_complete": False,
                "last_error": error
                or "A DNS resolver could not be reached; retry the check.",
                "dns_check": dns,
                "second_resolver": second_dns,
                "certificate_ready": ready,
                "https_verified": None,
            }
        status = (
            "active"
            if https_ok
            else "pending_dns"
            if not dns["ok"] or not second_dns["ok"]
            else "provisioning_cert"
        )
        domain.update(status=status, last_checked_at=now(), last_error=error)
        self.progress(f"Saving domain status: {status}")
        self._save(domain, before)
        return {
            **domain,
            "check_complete": True,
            "dns_check": dns,
            "second_resolver": second_dns,
            "certificate_ready": ready,
            "https_verified": https_ok,
            "dns_instructions": instructions(
                hostname, domain.get("records_required", [])
            ),
        }

    def _save(self, domain, before):
        def change(state):
            if state["domains"].get(domain["hostname"]) != before:
                raise ValueError(
                    "Domain changed during this operation. Rerun to avoid overwriting another administrator's changes."
                )
            state["domains"][domain["hostname"]] = domain

        self.store.transaction(change)

    def set_default(self, hostname):
        def change(state):
            if (
                hostname not in state["domains"]
                or state["domains"][hostname]["status"] != "active"
            ):
                raise ValueError("Only an active domain can be the default.")
            for host, domain in state["domains"].items():
                domain["is_default"] = host == hostname
            return {
                "default": hostname,
                "note": "Existing pages retain their assigned domain.",
            }

        return self.store.transaction(change)

    def remove(self, hostname):
        def change(state):
            assigned = sum(
                p.get("domain") == hostname for p in state["artifacts"].values()
            )
            defaults = sum(
                c.get("default_domain") == hostname for c in state["clients"].values()
            )
            domain = state["domains"].get(hostname)
            if not domain:
                raise ValueError("Unknown domain.")
            if assigned or defaults or domain.get("is_default"):
                raise ValueError(
                    f"Domain is still referenced: {assigned} pages, {defaults} clients, global default={domain.get('is_default', False)}. Move them first."
                )
            domain.update(status="removed", is_default=False, last_checked_at=now())
            return {
                "removed": hostname,
                "note": "Host detached from the viewer. Old links on it stop working. DNS and cloud resources are retained; remove them manually only after checking references.",
            }

        return self.store.transaction(change)
