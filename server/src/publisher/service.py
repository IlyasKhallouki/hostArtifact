import hashlib
from urllib.parse import urlsplit

from publisher import analytics, domains, passwords
from publisher.config import csv
from publisher.html_utils import apply_edits, normalize
from publisher.models import Edit, PublisherError, expiry, now
from publisher.slugs import slugify, token, validate_slug


class ArtifactService:
    def __init__(self, metadata, blobs, settings):
        self.metadata, self.blobs, self.settings = metadata, blobs, settings
        self.umami = analytics.Umami(settings) if settings.umami_enabled else None

    def _sync_analytics_result(self, result):
        if not self.settings.umami_enabled:
            return result
        try:
            analytics.sync_artifact(self, result["id"])
            result["analytics"] = self.get_artifact(result["id"])["analytics"]
        except Exception:
            # The page was committed before analytics started. Preserve its URL
            # and any one-time password even if the secondary metadata write fails.
            result["analytics"] = {
                "status": "pending",
                "error": "Analytics sync could not finish; retry publisher-admin analytics sync --apply.",
            }
        return result

    def _find(self, state, artifact):
        if artifact in state["artifacts"]:
            return state["artifacts"][artifact]
        parsed = urlsplit(artifact)
        if parsed.hostname in domains.known_hosts(state, self.settings):
            for item in state["artifacts"].values():
                if (
                    parsed.path
                    == urlsplit(domains.artifact_url(state, item, self.settings)).path
                ):
                    return item
        raise PublisherError(
            "NOT_FOUND: Page not found. Use list_artifacts to find it."
        )

    def _client(self, state, client, actor=None):
        slug = slugify(client)
        for item in state["clients"].values():
            if item["slug"] == slug or item["name"].casefold() == client.casefold():
                return item
        if actor is None:
            raise PublisherError("NOT_FOUND: Client not found. Use list_clients.")
        item = {"slug": slug, "name": client, "created_at": now(), "created_by": actor}
        state["clients"][slug] = item
        return item

    def _public(self, state, item):
        result = {
            key: item.get(key)
            for key in (
                "id",
                "title",
                "domain",
                "expires_at",
                "status",
                "updated_at",
                "updated_by",
                "created_at",
                "created_by",
                "size_bytes",
                "notes",
            )
        }
        result["analytics"] = analytics.summary(self.settings, item)
        result.update(
            client=item["client_slug"],
            access=item["access_mode"],
            version=item["current_version"],
            url=domains.artifact_url(state, item, self.settings),
        )
        if item.get("domain") and item["domain"] not in domains.active_domains(state):
            result["warnings"] = [
                f"Domain {item['domain']} is unavailable; using the viewer fallback."
            ]
        return result

    def _audit(self, state, actor, action, item=None, details=None):
        state["audit"][token(24)] = {
            "ts": now(),
            "actor": actor,
            "action": action,
            "artifact_id": item["id"] if item else None,
            "details": details or {},
        }
        if item:
            item.update(updated_at=now(), updated_by=actor)

    def _version(self, state, item, html, actor, note=None):
        version = item["current_version"] + 1
        # Unique immutable object per transaction attempt; failed commits leave harmless orphan objects.
        path = f"artifacts/{item['id']}/v{version}/{token(16)}/index.html"
        self.blobs.put(path, html)
        data = {
            "artifact_id": item["id"],
            "version": version,
            "blob_path": path,
            "sha256": hashlib.sha256(html.encode()).hexdigest(),
            "size_bytes": len(html.encode()),
            "created_at": now(),
            "created_by": actor,
            "note": note,
        }
        state["versions"][f"{item['id']}_{version}"] = data
        item.update(current_version=version, size_bytes=data["size_bytes"])

    def list_clients(self):
        state = self.metadata.read()
        result = []
        for client in state["clients"].values():
            pages = [
                p
                for p in state["artifacts"].values()
                if p["client_slug"] == client["slug"]
            ]
            result.append(
                {
                    "slug": client["slug"],
                    "name": client["name"],
                    "live_pages": sum(p["status"] == "live" for p in pages),
                    "last_updated": max((p["updated_at"] for p in pages), default=None),
                }
            )
        return result

    def list_artifacts(
        self, client=None, search=None, status="live", limit=25, cursor=None
    ):
        if status not in {"live", "unpublished", "all"} or not 1 <= limit <= 100:
            raise PublisherError(
                "INVALID_INPUT: Use live/unpublished/all and limit 1–100."
            )
        state = self.metadata.read()
        client_slug = self._client(state, client)["slug"] if client else None
        items = sorted(
            state["artifacts"].values(),
            key=lambda p: (p["updated_at"], p["id"]),
            reverse=True,
        )
        items = [
            p
            for p in items
            if (not client_slug or p["client_slug"] == client_slug)
            and (status == "all" or p["status"] == status)
            and (
                not search
                or search.lower()
                in f"{p['title']} {p['slug']} {p['client_slug']}".lower()
            )
        ]
        start = 0
        if cursor:
            matches = [i for i, p in enumerate(items) if p["id"] == cursor]
            if not matches:
                raise PublisherError("INVALID_INPUT: Cursor expired; list pages again.")
            start = matches[0] + 1
        page = items[start : start + limit]
        return {
            "items": [self._public(state, p) for p in page],
            "next_cursor": page[-1]["id"] if start + limit < len(items) else None,
        }

    def get_artifact(self, artifact, include_html=False, offset=0, max_chars=100000):
        state = self.metadata.read()
        item = self._find(state, artifact)
        result = self._public(state, item)
        if include_html:
            if offset < 0 or not 1 <= max_chars <= 120000:
                raise PublisherError(
                    "INVALID_INPUT: Use offset >= 0 and max_chars 1–120000."
                )
            html = self.blobs.get(
                state["versions"][f"{item['id']}_{item['current_version']}"][
                    "blob_path"
                ]
            )
            end = min(len(html), offset + max_chars)
            result.update(
                html=html[offset:end],
                offset=offset,
                next_offset=end if end < len(html) else None,
                truncated=end < len(html),
            )
        return result

    def publish_artifact(
        self,
        actor,
        client,
        title,
        html,
        slug=None,
        domain=None,
        access=None,
        password=None,
        expires_at=None,
        note=None,
        allow_duplicate=False,
    ):
        if not client.strip() or not title.strip():
            raise PublisherError("INVALID_INPUT: Provide a client and title.")
        html = normalize(html, title, self.settings.max_html_bytes)
        slug = validate_slug(slug) if slug else slugify(title)
        mode = access or self.settings.default_access_mode
        if mode not in {"link", "password"} or (
            password is not None and mode != "password"
        ):
            raise PublisherError(
                "INVALID_INPUT: Choose link or password access; passwords require password mode."
            )
        secret = (
            (password if password is not None else passwords.generate())
            if mode == "password"
            else None
        )
        encoded = passwords.hash_password(secret) if secret is not None else None
        expires = expiry(expires_at)

        def change(state):
            c = self._client(state, client, actor)
            for page in state["artifacts"].values():
                if (
                    page["client_slug"] == c["slug"]
                    and page["slug"] == slug
                    and page["status"] == "live"
                    and not allow_duplicate
                ):
                    raise PublisherError(
                        f"CONFLICT: {page['title']} already exists at {domains.artifact_url(state, page, self.settings)}. Use update_artifact."
                    )
            domains.require_domain(state, domain)
            identifier = token(12, "abcdefghijklmnopqrstuvwxyz234567")
            item = {
                "id": identifier,
                "client_slug": c["slug"],
                "title": title,
                "slug": slug,
                "url_token": token(),
                "status": "live",
                "access_mode": mode,
                "password_hash": encoded,
                "access_version": 1,
                "expires_at": expires,
                "domain": domain or domains.default_domain(state, c["slug"]),
                "current_version": 0,
                "created_at": now(),
                "created_by": actor,
                "notes": note,
            }
            state["artifacts"][identifier] = item
            self._version(state, item, html, actor, note)
            self._audit(state, actor, "publish", item)
            result = self._public(state, item)
            if secret:
                result["password"] = secret
            return result

        result = self.metadata.transaction(change)
        return self._sync_analytics_result(result)

    def update_artifact(
        self, actor, artifact, html=None, edits=None, title=None, note=None
    ):
        if (html is not None and edits is not None) or (
            html is None and edits is None and title is None
        ):
            raise PublisherError(
                "INVALID_INPUT: Provide either HTML or edits, or a title-only change."
            )
        if title is not None and not title.strip():
            raise PublisherError("INVALID_INPUT: Title cannot be empty.")

        def change(state):
            item = self._find(state, artifact)
            content = html
            if edits is not None:
                current = state["versions"][f"{item['id']}_{item['current_version']}"]
                content = apply_edits(
                    self.blobs.get(current["blob_path"]),
                    [Edit.model_validate(e) for e in edits],
                )
            if content is not None:
                content = normalize(
                    content, title or item["title"], self.settings.max_html_bytes
                )
                self._version(state, item, content, actor, note)
            if title is not None:
                item["title"] = title
            self._audit(state, actor, "update", item)
            return self._public(state, item)

        result = self.metadata.transaction(change)
        return self._sync_analytics_result(result)

    def set_access(
        self,
        actor,
        artifact,
        mode=None,
        password=None,
        expires_at=None,
        rotate_link=False,
        clear_expiry=False,
    ):
        if mode is not None and mode not in {"link", "password"}:
            raise PublisherError("INVALID_INPUT: Choose link or password mode.")
        secret = (
            (password if password is not None else passwords.generate())
            if mode == "password"
            else password
        )
        encoded = passwords.hash_password(secret) if secret is not None else None
        expires = expiry(expires_at)

        def change(state):
            item = self._find(state, artifact)
            target_mode = mode or item["access_mode"]
            if secret is not None and target_mode != "password":
                raise PublisherError(
                    "INVALID_INPUT: Set mode=password to add a password."
                )
            if mode is not None or secret is not None or rotate_link:
                item["access_version"] += 1
            item["access_mode"] = target_mode
            if encoded:
                item["password_hash"] = encoded
            elif target_mode == "link":
                item["password_hash"] = None
            if expires_at is not None or clear_expiry:
                item["expires_at"] = expires
            if rotate_link:
                item["url_token"] = token()
            self._audit(state, actor, "set_access", item)
            result = self._public(state, item)
            if secret:
                result["password"] = secret
            if item.get("expires_at") and item["expires_at"] < now():
                result.setdefault("warnings", []).append(
                    "This page is already expired."
                )
            return result

        return self.metadata.transaction(change)

    def list_versions(self, artifact):
        state = self.metadata.read()
        item = self._find(state, artifact)
        return sorted(
            [
                {
                    k: v
                    for k, v in version.items()
                    if k not in {"blob_path", "artifact_id"}
                }
                for version in state["versions"].values()
                if version["artifact_id"] == item["id"]
            ],
            key=lambda v: v["version"],
            reverse=True,
        )

    def rollback_artifact(self, actor, artifact, version):
        def change(state):
            item = self._find(state, artifact)
            old = state["versions"].get(f"{item['id']}_{version}")
            if not old:
                raise PublisherError("NOT_FOUND: Version not found. Use list_versions.")
            self._version(
                state,
                item,
                self.blobs.get(old["blob_path"]),
                actor,
                f"rollback to v{version}",
            )
            self._audit(state, actor, "rollback", item, {"from_version": version})
            return self._public(state, item)

        return self.metadata.transaction(change)

    def _status(self, actor, artifact, status):
        def change(state):
            item = self._find(state, artifact)
            item["status"] = status
            item["access_version"] += 1
            self._audit(state, actor, status, item)
            return self._public(state, item)

        return self.metadata.transaction(change)

    def unpublish_artifact(self, actor, artifact):
        return self._status(actor, artifact, "unpublished")

    def restore_artifact(self, actor, artifact):
        return self._sync_analytics_result(self._status(actor, artifact, "live"))

    def delete_artifact_permanently(self, actor, artifact, confirm_title):
        if actor.lower() not in csv(self.settings.admin_emails):
            raise PublisherError(
                f"FORBIDDEN: Only an administrator can permanently delete. Contact {self.settings.admin_contact}."
            )

        def change(state):
            item = self._find(state, artifact)
            if item["status"] != "unpublished" or item["title"] != confirm_title:
                raise PublisherError(
                    "INVALID_INPUT: Unpublish first and confirm the exact title."
                )
            identifier = item["id"]
            self._audit(state, actor, "delete", item)
            del state["artifacts"][identifier]
            state["versions"] = {
                k: v
                for k, v in state["versions"].items()
                if v["artifact_id"] != identifier
            }
            return identifier

        identifier = self.metadata.transaction(change)
        self.blobs.delete_prefix(f"artifacts/{identifier}/")
        return {"id": identifier, "deleted": True}

    def list_domains(self):
        state = self.metadata.read()
        return {
            "domains": [
                {"hostname": name, "is_default": item.get("is_default", False)}
                for name, item in domains.active_domains(state).items()
            ],
            "run_app_fallback": self.settings.viewer_url,
        }

    def set_artifact_domain(self, actor, artifact, domain):
        def change(state):
            domains.require_domain(state, domain)
            item = self._find(state, artifact)
            previous = domains.artifact_url(state, item, self.settings)
            item["domain"] = domain or domains.default_domain(
                state, item["client_slug"]
            )
            self._audit(state, actor, "set_artifact_domain", item)
            return {**self._public(state, item), "previous_url": previous}

        result = self.metadata.transaction(change)
        return self._sync_analytics_result(result)

    def set_client_domain(self, actor, client, domain):
        def change(state):
            domains.require_domain(state, domain)
            item = self._client(state, client)
            item.update(default_domain=domain, updated_by=actor, updated_at=now())
            self._audit(
                state,
                actor,
                "set_client_domain",
                details={"client_slug": item["slug"], "domain": domain},
            )
            return {
                "client": item["slug"],
                "domain": domain,
                "pages_not_moved": sum(
                    p["client_slug"] == item["slug"]
                    for p in state["artifacts"].values()
                ),
            }

        return self.metadata.transaction(change)
