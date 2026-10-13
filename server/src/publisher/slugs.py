import re
import secrets
import string
import unicodedata

from publisher.models import PublisherError

RESERVED = {
    "healthz",
    "robots-txt",
    "favicon-ico",
    "static",
    "api",
    "mcp",
    "auth",
    "well-known",
}


def slugify(value: str) -> str:
    value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    )
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:60].rstrip("-")
    if len(value) < 3:
        value = (value + "-page").strip("-")
    if value in RESERVED:
        value += "-page"
    return value


def validate_slug(value: str) -> str:
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,58})[a-z0-9]", value)
        or value in RESERVED
    ):
        raise PublisherError(
            "INVALID_INPUT: Slugs need 3–60 lowercase letters, digits or hyphens; choose a non-reserved name."
        )
    return value


def token(
    length: int = 10, alphabet: str = string.ascii_lowercase + string.digits
) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))
