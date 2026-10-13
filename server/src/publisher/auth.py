from cryptography.fernet import Fernet
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware

from publisher.config import csv


def identity(settings, claims=None):
    if settings.auth_mode == "dev":
        claims = {"email": "dev@local", "email_verified": True}
    elif claims is None:
        token = get_access_token()
        claims = token.claims if token else {}
    email = str(claims.get("email") or "").lower()
    verified = (
        claims.get("email_verified") is True or claims.get("email_verified") == "true"
    )
    reason = None
    if not email or "@" not in email:
        reason = "Google did not provide an email address"
    elif not verified:
        reason = "Google hasn't verified this email address"
    elif (
        settings.auth_mode != "dev"
        and email not in csv(settings.allowed_emails)
        and email.rsplit("@", 1)[1] not in csv(settings.allowed_email_domains)
    ):
        reason = f"The account {email} isn't in the publisher allowlist"
    return {
        "email": email,
        "allowed": reason is None,
        "reason": reason,
        "fix": None
        if reason is None
        else f"Reconnect with an allowed, verified Google account, or ask {settings.admin_contact} to add this email to ALLOWED_EMAILS and redeploy.",
        "is_admin": email in csv(settings.admin_emails),
        "server_version": settings.git_sha,
    }


class AccessPolicy(Middleware):
    def __init__(self, settings):
        self.settings = settings

    async def on_list_tools(self, context, call_next):
        result = await call_next(context)
        if not identity(self.settings)["allowed"]:
            return [tool for tool in result if tool.name == "whoami"]
        return result

    async def on_call_tool(self, context, call_next):
        if context.message.name != "whoami":
            user = identity(self.settings)
            if not user["allowed"]:
                raise ToolError(f"FORBIDDEN: {user['reason']}. {user['fix']}")
        return await call_next(context)


def google_provider(settings):
    from fastmcp.server.auth.providers.google import GoogleProvider
    from key_value.aio.stores.firestore import (
        FirestoreStore,
        FirestoreV1KeySanitizationStrategy,
    )
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper

    for field in (
        "google_client_id",
        "google_client_secret",
        "jwt_signing_key",
        "storage_encryption_key",
    ):
        if not getattr(settings, field):
            raise ValueError(f"Required secret is missing: {field.upper()}")
    store = FirestoreStore(
        project=settings.gcp_project_id,
        database=settings.firestore_database,
        # CIMD clients identify themselves with HTTPS URLs. Encode invalid
        # document IDs while preserving existing DCR/session keys unchanged.
        key_sanitization_strategy=FirestoreV1KeySanitizationStrategy(),
    )
    store = PrefixCollectionsWrapper(store, prefix="oauth_")
    encrypted = FernetEncryptionWrapper(
        store, fernet=Fernet(settings.storage_encryption_key.encode())
    )
    params = {"access_type": "offline", "prompt": "consent"}
    if len(csv(settings.allowed_email_domains)) == 1:
        params["hd"] = next(iter(csv(settings.allowed_email_domains)))
    return GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.api_url,
        resource_base_url=settings.api_url,
        required_scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
        jwt_signing_key=settings.jwt_signing_key,
        client_storage=encrypted,
        allowed_client_redirect_uris=[
            "https://claude.ai/api/mcp/auth_callback",
            "https://claude.com/api/mcp/auth_callback",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
        require_authorization_consent=True,
        fastmcp_access_token_expiry_seconds=28800,
        extra_authorize_params=params,
    )
