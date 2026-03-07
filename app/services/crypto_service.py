"""
Encryption / decryption service for job environment variables.

Uses Fernet symmetric encryption with a key derived from:
    PBKDF2(SECRET_KEY, user_salt, iterations)

Each user gets a unique derived key because their salt is unique.
If the master SECRET_KEY or the user's salt changes, existing encrypted
values become unrecoverable — by design.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.common import constants
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Cache derived Fernet instances per user_salt to avoid re-deriving on every call.
# In a single-user app this cache will have exactly one entry.
_fernet_cache: dict[str, Fernet] = {}


def _derive_fernet_key(user_salt: str) -> bytes:
    """
    Derive a 32-byte key from SECRET_KEY + user_salt using PBKDF2-SHA256,
    then base64url-encode it to produce a valid Fernet key (44 bytes).
    """
    raw_key = hashlib.pbkdf2_hmac(
        "sha256",
        constants.SECRET_KEY.encode("utf-8"),
        user_salt.encode("utf-8"),
        constants.ENCRYPTION_KEY_ITERATIONS,
    )
    # Fernet requires a url-safe base64-encoded 32-byte key
    return base64.urlsafe_b64encode(raw_key)


def _get_fernet(user_salt: str) -> Fernet:
    """Return a cached Fernet instance for the given user salt."""
    if user_salt not in _fernet_cache:
        key = _derive_fernet_key(user_salt)
        _fernet_cache[user_salt] = Fernet(key)
    return _fernet_cache[user_salt]


def encrypt_value(plaintext: str, user_salt: str) -> str:
    """
    Encrypt a plaintext string and return a base64-encoded ciphertext string.
    Safe to store directly in a TEXT column.
    """
    f = _get_fernet(user_salt)
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")  # Fernet tokens are already url-safe base64


def decrypt_value(ciphertext: str, user_salt: str) -> str:
    """
    Decrypt a ciphertext string that was produced by encrypt_value().
    Raises cryptography.fernet.InvalidToken on tampered or wrong-key data.
    """
    f = _get_fernet(user_salt)
    plaintext_bytes = f.decrypt(ciphertext.encode("utf-8"))
    return plaintext_bytes.decode("utf-8")


def clear_cache() -> None:
    """Clear the Fernet key cache (useful for testing or key rotation)."""
    _fernet_cache.clear()
