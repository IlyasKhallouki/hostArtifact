import argparse
import subprocess
import time
from urllib.parse import urlparse

import httpx

from publisher.config import ROOT, Settings

parser = argparse.ArgumentParser()
parser.add_argument("--service", choices=["all", "api", "viewer"], default="all")
parser.add_argument("--git-sha", help="Expected deployed commit; defaults to HEAD.")
parser.add_argument(
    "--claude-oauth",
    action="store_true",
    help="Check Claude's URL client ID through consent (creates an expiring OAuth transaction).",
)
args, _ = parser.parse_known_args()
s = Settings()
expected = (
    args.git_sha
    or subprocess.check_output(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True
    ).strip()
)
with httpx.Client(timeout=15) as client:
    for mode, url in (("api", s.api_url), ("viewer", s.viewer_url)):
        if args.service not in {"all", mode}:
            continue
        response = client.get(url + "/health")
        response.raise_for_status()
        assert response.json() == {"ok": True, "mode": mode, "git_sha": expected}
    if args.service in {"all", "api"}:
        response = client.post(
            s.api_url + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers.get("www-authenticate", "")
        metadata_url = (
            response.headers["www-authenticate"]
            .split('resource_metadata="', 1)[1]
            .split('"')[0]
        )
        start = time.monotonic()
        metadata = client.get(metadata_url).json()
        assert time.monotonic() - start < 2
        assert metadata["resource"] == s.api_url + "/mcp"
        start = time.monotonic()
        auth = client.get(
            metadata["authorization_servers"][0].rstrip("/")
            + "/.well-known/oauth-authorization-server"
        ).json()
        assert time.monotonic() - start < 2
        assert (
            auth["registration_endpoint"]
            and "S256" in auth["code_challenge_methods_supported"]
        )
        if args.claude_oauth:
            response = client.get(
                s.api_url + "/authorize",
                params={
                    "client_id": "https://claude.ai/oauth/mcp-oauth-client-metadata",
                    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                    "response_type": "code",
                    "code_challenge": "a" * 43,
                    "code_challenge_method": "S256",
                    "state": "publisher-smoke-test",
                },
            )
            assert response.status_code == 302, "Claude authorization did not redirect"
            location = response.headers["location"]
            assert urlparse(location).netloc == urlparse(s.api_url).netloc
            assert urlparse(location).path == "/consent"
            consent = client.get(location)
            assert consent.status_code == 200 and "Claude" in consent.text
            print("Claude URL client authorization and consent checks passed")
    if args.service in {"all", "viewer"}:
        missing = client.get(s.viewer_url + "/missing-smoke-test")
        assert (
            missing.status_code == 404 and "noindex" in missing.headers["x-robots-tag"]
        )
        assert "Disallow: /" in client.get(s.viewer_url + "/robots.txt").text
print(f"Deployed {args.service} smoke checks passed")
