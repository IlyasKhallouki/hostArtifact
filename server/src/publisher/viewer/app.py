import html
import time
from collections import OrderedDict, defaultdict, deque
from urllib.parse import urlsplit

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from publisher.domains import artifact_url, known_hosts
from publisher.models import now
from publisher.passwords import verify

HEADERS = {
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "frame-ancestors 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def create_viewer(service):
    settings = service.settings
    if not settings.cookie_signing_key:
        raise ValueError("COOKIE_SIGNING_KEY is required")
    signer = URLSafeTimedSerializer(
        settings.cookie_signing_key, salt="publisher-viewer"
    )
    failures = defaultdict(deque)
    html_cache = OrderedDict()
    cache_bytes = 0

    def system(message, status, password=False, error=False):
        form = (
            '<form method="post"><label>Password <input name="password" type="password" autocomplete="current-password" maxlength="1024" required></label><button>Open page</button></form>'
            if password
            else ""
        )
        body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(settings.org_display_name)}</title><style>:root{{color-scheme:light dark;font:16px system-ui}}body{{max-width:28rem;margin:12vh auto;padding:2rem}}h1{{font-size:1.5rem}}input,button{{box-sizing:border-box;display:block;width:100%;padding:.8rem;margin-top:1rem;font:inherit}}p[role=alert]{{color:#b34335}}</style></head><body><h1>{html.escape(settings.org_display_name)}</h1><p {'role="alert"' if error else ""}>{html.escape(message)}</p>{form}</body></html>"""
        return HTMLResponse(body, status_code=status, headers=HEADERS)

    async def health(request):
        return JSONResponse({"ok": True, "mode": "viewer", "git_sha": settings.git_sha})

    async def serve(request: Request):
        nonlocal cache_bytes
        state = await run_in_threadpool(service.metadata.read)
        host = request.url.hostname
        if host not in known_hosts(state, settings):
            return system("This page isn't available.", 404)
        path = request.url.path
        if path == "/robots.txt":
            return Response(
                "User-agent: *\nDisallow: /\n", media_type="text/plain", headers=HEADERS
            )
        if path == "/favicon.ico":
            return Response(status_code=204, headers=HEADERS)
        if path == "/":
            target = state["domains"].get(host, {}).get("root_redirect")
            if target:
                return RedirectResponse(target, 302, headers=HEADERS)
        item = next(
            (
                p
                for p in state["artifacts"].values()
                if path == f"/{p['client_slug']}/{p['slug']}-{p['url_token']}"
            ),
            None,
        )
        if not item or item["status"] != "live":
            return system("This page isn't available.", 404)
        canonical = artifact_url(state, item, settings)
        if host != urlsplit(canonical).hostname:
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(
                canonical + query,
                307 if request.method == "POST" else 301,
                headers=HEADERS,
            )
        if item.get("expires_at") and item["expires_at"] < now():
            return system(
                f"This link has expired. Contact {settings.org_display_name}.", 410
            )
        sessions = {}
        try:
            sessions = signer.loads(request.cookies.get("__session", ""), max_age=43200)
            if not isinstance(sessions, dict):
                sessions = {}
        except BadSignature:
            pass
        if item["access_mode"] == "password":
            # Cloud Run appends its verified client address; behind a Google LB it is the proxy.
            # Using the rightmost entry prevents spoofing but may conservatively share LB quotas.
            forwarded = request.headers.get("x-forwarded-for", "").split(",")
            ip = forwarded[-1].strip() or (
                request.client.host if request.client else "unknown"
            )
            key = (ip, item["id"])
            stamp = time.monotonic()
            if len(failures) > 10000:
                for old_key in list(failures):
                    if not failures[old_key] or failures[old_key][-1] < stamp - 900:
                        del failures[old_key]
                if len(failures) > 10000 and key not in failures:
                    return system("Too many attempts. Try again later.", 429)
            attempts = failures[key]
            while attempts and attempts[0] < stamp - 900:
                attempts.popleft()
            if request.method == "POST":
                if len(attempts) >= 10:
                    return system("Too many attempts. Try again in 15 minutes.", 429)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 8192:
                        return system("Password form is too large.", 413)
                from urllib.parse import parse_qs

                password = parse_qs(body.decode("utf-8", errors="replace")).get(
                    "password", [""]
                )[0]
                attempts.append(
                    stamp
                )  # Reserve before the expensive verification await.
                if await run_in_threadpool(verify, item["password_hash"], password):
                    attempts.clear()
                    sessions[item["id"]] = item["access_version"]
                    sessions = dict(list(sessions.items())[-20:])
                    response = RedirectResponse(canonical, 303, headers=HEADERS)
                    response.set_cookie(
                        "__session",
                        signer.dumps(sessions),
                        max_age=43200,
                        secure=urlsplit(settings.viewer_url).scheme == "https",
                        httponly=True,
                        samesite="lax",
                    )
                    return response
                return system(
                    "That password didn't match. Try again.",
                    401,
                    password=True,
                    error=True,
                )
            if sessions.get(item["id"]) != item["access_version"]:
                return system(
                    "Enter the password to view this page.", 401, password=True
                )
        version = state["versions"][f"{item['id']}_{item['current_version']}"]
        blob_path = version["blob_path"]
        if blob_path not in html_cache:
            content = await run_in_threadpool(service.blobs.get, blob_path)
            size = len(content.encode())
            while html_cache and cache_bytes + size > 50_000_000:
                _, removed = html_cache.popitem(last=False)
                cache_bytes -= len(removed.encode())
            html_cache[blob_path] = content
            cache_bytes += size
        html_cache.move_to_end(blob_path)
        headers = dict(HEADERS)
        if item["access_mode"] == "link":
            headers.update(
                {"Cache-Control": "private, no-cache", "ETag": f'"{version["sha256"]}"'}
            )
            if request.headers.get("if-none-match") == headers["ETag"]:
                return Response(status_code=304, headers=headers)
        return HTMLResponse(html_cache[blob_path], headers=headers)

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/{path:path}", serve, methods=["GET", "POST"]),
        ]
    )
