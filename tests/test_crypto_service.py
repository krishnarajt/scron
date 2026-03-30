"""
Tests for the crypto service — encrypt/decrypt round-trip verification,
edge cases, cache behavior.
"""

import pytest
from app.services.crypto_service import (
    encrypt_value,
    decrypt_value,
    clear_cache,
    _get_fernet,
)


class TestCryptoRoundTrip:
    def setup_method(self):
        clear_cache()

    def test_basic_round_trip(self):
        salt = "a" * 64
        original = "my-secret-database-url"
        encrypted = encrypt_value(original, salt)
        decrypted = decrypt_value(encrypted, salt)
        assert decrypted == original

    def test_empty_string_round_trip(self):
        salt = "b" * 64
        encrypted = encrypt_value("", salt)
        assert decrypt_value(encrypted, salt) == ""

    def test_unicode_round_trip(self):
        salt = "c" * 64
        original = "пароль-密码-パスワード-🔑"
        assert decrypt_value(encrypt_value(original, salt), salt) == original

    def test_special_chars_round_trip(self):
        salt = "d" * 64
        original = 'p@$$w0rd!#%^&*()_+-={}[]|\\:";<>?,./~`'
        assert decrypt_value(encrypt_value(original, salt), salt) == original

    def test_multiline_round_trip(self):
        salt = "e" * 64
        original = "line1\nline2\nline3\ttab"
        assert decrypt_value(encrypt_value(original, salt), salt) == original

    def test_long_value_round_trip(self):
        salt = "f" * 64
        original = "x" * 10000
        assert decrypt_value(encrypt_value(original, salt), salt) == original

    def test_different_salts_different_ciphertext(self):
        enc1 = encrypt_value("same", "salt1" + "a" * 59)
        enc2 = encrypt_value("same", "salt2" + "b" * 59)
        assert enc1 != enc2

    def test_same_plaintext_different_ciphertext(self):
        """Fernet uses a timestamp/IV, so encrypting the same value twice should differ."""
        salt = "g" * 64
        e1 = encrypt_value("same", salt)
        e2 = encrypt_value("same", salt)
        assert e1 != e2  # Different due to random IV
        # Both should decrypt to the same value
        assert decrypt_value(e1, salt) == "same"
        assert decrypt_value(e2, salt) == "same"

    def test_wrong_salt_fails(self):
        encrypted = encrypt_value("secret", "a" * 64)
        with pytest.raises(Exception):
            decrypt_value(encrypted, "b" * 64)

    def test_tampered_ciphertext_fails(self):
        salt = "c" * 64
        encrypted = encrypt_value("secret", salt)
        tampered = encrypted[:-4] + "XXXX"
        with pytest.raises(Exception):
            decrypt_value(tampered, salt)

    def test_completely_invalid_ciphertext(self):
        with pytest.raises(Exception):
            decrypt_value("not-valid-fernet-token", "a" * 64)


class TestFernetCache:
    def setup_method(self):
        clear_cache()

    def test_cache_returns_same_instance(self):
        salt = "z" * 64
        f1 = _get_fernet(salt)
        f2 = _get_fernet(salt)
        assert f1 is f2

    def test_different_salts_different_instances(self):
        f1 = _get_fernet("a" * 64)
        f2 = _get_fernet("b" * 64)
        assert f1 is not f2

    def test_clear_cache_resets(self):
        salt = "y" * 64
        f1 = _get_fernet(salt)
        clear_cache()
        f2 = _get_fernet(salt)
        # After clearing, a new instance should be created
        # (they'll be functionally equivalent but different objects)
        assert f1 is not f2

    def test_cache_after_clear_still_works(self):
        salt = "x" * 64
        original = "test-value"
        encrypted = encrypt_value(original, salt)
        clear_cache()
        # Should still decrypt correctly with a fresh Fernet instance
        assert decrypt_value(encrypted, salt) == original
