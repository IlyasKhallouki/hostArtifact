from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from publisher.models import PublisherError
from publisher.slugs import token

_hasher = PasswordHasher()


def generate() -> str:
    return "-".join(token(4, "abcdefghjkmnpqrstuvwxyz23456789") for _ in range(3))


def hash_password(value: str) -> str:
    if len(value) < 8 or len(value) > 1024:
        raise PublisherError("INVALID_INPUT: Passwords need 8–1024 characters.")
    return _hasher.hash(value)


def verify(encoded: str, value: str) -> bool:
    if len(value) > 1024:
        return False
    try:
        return _hasher.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False
