import functools
import inspect
import json
import logging
import time

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from publisher.auth import AccessPolicy, google_provider, identity
from publisher.models import Edit, PublisherError

READ_ONLY = {
    "list_clients",
    "list_artifacts",
    "get_artifact",
    "list_versions",
    "list_domains",
}
DESTRUCTIVE = {"unpublish_artifact", "delete_artifact_permanently"}
DESCRIPTIONS = {
    "list_clients": "List existing clients before creating a new page.",
    "list_artifacts": "Find shared pages by client, title or status.",
    "get_artifact": "Get page details and optionally the current HTML in chunks.",
    "publish_artifact": "Publish a self-contained HTML page and return its shareable link.",
    "update_artifact": "Update a page using full HTML or exact find/replace edits, keeping its link.",
    "set_access": "Change password or expiry, or rotate a leaked link; use clear_expiry=true to clear expiry.",
    "list_versions": "List a page's immutable history, newest first.",
    "rollback_artifact": "Restore an older page version as a new version.",
    "unpublish_artifact": "Take a page offline on explicit user request; it can be restored.",
    "restore_artifact": "Restore an unpublished page at its existing link.",
    "delete_artifact_permanently": "Permanently delete an unpublished page; admin and exact title confirmation required.",
    "list_domains": "List active domains; ask an administrator to add any new domain.",
    "set_artifact_domain": "Move a page to an active domain; old attached domains redirect.",
    "set_client_domain": "Set a client's default domain for future pages; existing pages stay put.",
}
TYPES = {
    "client": str,
    "title": str,
    "html": str,
    "slug": str,
    "domain": str,
    "access": str,
    "password": str,
    "expires_at": str,
    "note": str,
    "artifact": str,
    "search": str,
    "status": str,
    "cursor": str,
    "mode": str,
    "confirm_title": str,
    "edits": list[Edit],
    "limit": int,
    "offset": int,
    "max_chars": int,
    "version": int,
    "include_html": bool,
    "allow_duplicate": bool,
    "rotate_link": bool,
    "clear_expiry": bool,
}


def create_mcp(service):
    settings = service.settings
    mcp = FastMCP(
        settings.plugin_display_name,
        instructions="Publish self-contained HTML pages. Call list_clients before creating a client. Prefer edits for small updates. Never unpublish without explicit user request.",
        auth=google_provider(settings) if settings.auth_mode == "google" else None,
    )
    mcp.add_middleware(AccessPolicy(settings))

    def register(name):
        method = getattr(service, name)
        original = inspect.signature(method)
        has_actor = "actor" in original.parameters
        parameters = []
        for key, param in original.parameters.items():
            if key == "actor":
                continue
            annotation = TYPES[key]
            if param.default is None or key == "domain":
                annotation = annotation | None
            parameters.append(param.replace(annotation=annotation))

        async def invoke(**kwargs):
            actor = identity(settings)["email"]
            stamp = time.monotonic()
            try:
                if has_actor:
                    kwargs["actor"] = actor
                return await run_in_threadpool(functools.partial(method, **kwargs))
            except PublisherError as exc:
                raise ToolError(str(exc)) from None
            except Exception:
                raise ToolError(
                    f"UNAVAILABLE: The publisher could not complete this operation. Contact {settings.admin_contact} before retrying a publish."
                ) from None
            finally:
                logging.getLogger("publisher").info(
                    json.dumps(
                        {
                            "event": "tool_call",
                            "actor": actor,
                            "tool": name,
                            "duration_ms": round((time.monotonic() - stamp) * 1000),
                            "git_sha": settings.git_sha,
                        }
                    )
                )

        invoke.__name__ = name
        invoke.__annotations__ = {p.name: p.annotation for p in parameters}
        invoke.__signature__ = original.replace(
            parameters=parameters,
            return_annotation=list[dict]
            if name in {"list_clients", "list_versions"}
            else dict,
        )
        mcp.tool(
            name=name,
            title=name.replace("_", " ").title(),
            description=DESCRIPTIONS[name],
            annotations={
                "readOnlyHint": name in READ_ONLY,
                "destructiveHint": name in DESTRUCTIVE,
                "idempotentHint": name in READ_ONLY
                or name in {"set_artifact_domain", "set_client_domain"},
                "openWorldHint": False,
            },
        )(invoke)

    for name in DESCRIPTIONS:
        register(name)

    @mcp.tool(
        title="Account and access",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def whoami() -> dict:
        """Show the signed-in account and exactly why publishing access is allowed or blocked."""
        return identity(settings)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"ok": True, "mode": "api", "git_sha": settings.git_sha})

    return mcp
