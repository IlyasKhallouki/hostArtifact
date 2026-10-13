from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class PublisherError(ValueError):
    pass


class Edit(BaseModel):
    find: str = Field(min_length=1)
    replace: str
    expected_count: int = Field(default=1, ge=1)


def now() -> str:
    return datetime.now(UTC).isoformat()


def expiry(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()
    except ValueError as exc:
        raise PublisherError(
            "INVALID_INPUT: Use an ISO date or timestamp for expiry."
        ) from exc


def empty_state() -> dict[str, dict[str, Any]]:
    return {
        name: {} for name in ("clients", "artifacts", "versions", "domains", "audit")
    }
