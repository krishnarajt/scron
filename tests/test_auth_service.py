"""
Tests for auth service — password hashing, JWT tokens, user CRUD.
Direct service-layer tests (no HTTP, just DB).
"""

from datetime import timedelta

from app.services.auth_service import (
    get_password_hash,
    verify_password,
    create_access_token,
    verify_access_token,
    create_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
    revoke_all_user_tokens,
    rotate_refresh_token,
    authenticate_user,
    create_user,
    get_user_by_id,
)


class TestPasswordHashing:
    def test_hash_produces_valid_format(self):
        hashed = get_password_hash("mypassword")
        parts = hashed.split("$")
        assert len(parts) == 3
        assert int(parts[0]) == 100000  # iterations
        assert len(parts[1]) == 64  # salt hex

    def test_verify_correct_password(self):
        hashed = get_password_hash("mypassword")
        assert verify_password("mypassword", hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("mypassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_verify_malformed_hash_returns_false(self):
        assert verify_password("pw", "not-a-valid-hash") is False
        assert verify_password("pw", "") is False
        assert verify_password("pw", "a$b") is False

    def test_different_passwords_produce_different_hashes(self):
        h1 = get_password_hash("password1")
        h2 = get_password_hash("password2")
        assert h1 != h2

    def test_same_password_different_salts(self):
        h1 = get_password_hash("same")
        h2 = get_password_hash("same")
        # Same password but different random salts
        assert h1 != h2
        # Both should verify
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


class TestAccessTokens:
    def test_create_and_verify_round_trip(self):
        token = create_access_token(42)
        user_id = verify_access_token(token)
        assert user_id == 42

    def test_expired_token_returns_none(self):
        token = create_access_token(42, expires_delta=timedelta(seconds=-1))
        assert verify_access_token(token) is None

    def test_invalid_token_returns_none(self):
        assert verify_access_token("garbage.jwt.token") is None
        assert verify_access_token("") is None

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token(1)
        t2 = create_access_token(2)
        assert t1 != t2
        assert verify_access_token(t1) == 1
        assert verify_access_token(t2) == 2


class TestRefreshTokens:
    def test_create_and_verify(self, db_session, test_user):
        token = create_refresh_token(db_session, test_user.id)
        user_id = verify_refresh_token(db_session, token)
        assert user_id == test_user.id

    def test_revoke_refresh_token(self, db_session, test_user):
        token = create_refresh_token(db_session, test_user.id)
        assert revoke_refresh_token(db_session, token) is True
        # Now verification should fail
        assert verify_refresh_token(db_session, token) is None

    def test_revoke_all_user_tokens(self, db_session, test_user):
        t1 = create_refresh_token(db_session, test_user.id)
        t2 = create_refresh_token(db_session, test_user.id)
        revoke_all_user_tokens(db_session, test_user.id)
        assert verify_refresh_token(db_session, t1) is None
        assert verify_refresh_token(db_session, t2) is None

    def test_rotate_refresh_token(self, db_session, test_user):
        old_token = create_refresh_token(db_session, test_user.id)
        result = rotate_refresh_token(db_session, old_token)
        assert result is not None
        user_id, new_token = result
        assert user_id == test_user.id
        assert new_token != old_token
        # Old token should be revoked
        assert verify_refresh_token(db_session, old_token) is None
        # New token should work
        assert verify_refresh_token(db_session, new_token) == test_user.id

    def test_rotate_invalid_token_returns_none(self, db_session):
        assert rotate_refresh_token(db_session, "bad-token") is None

    def test_verify_invalid_refresh_returns_none(self, db_session):
        assert verify_refresh_token(db_session, "garbage") is None


class TestUserCRUD:
    def test_create_user(self, db_session):
        user = create_user(db_session, "newuser", "password123")
        assert user.username == "newuser"
        assert user.salt is not None
        assert len(user.salt) == 64

    def test_create_user_with_email(self, db_session):
        user = create_user(db_session, "emailuser", "pw", email="test@test.com")
        assert user.email == "test@test.com"

    def test_authenticate_user_success(self, db_session):
        create_user(db_session, "authuser", "secret")
        user = authenticate_user(db_session, "authuser", "secret")
        assert user is not None
        assert user.username == "authuser"

    def test_authenticate_user_wrong_password(self, db_session):
        create_user(db_session, "authuser", "secret")
        assert authenticate_user(db_session, "authuser", "wrong") is None

    def test_authenticate_nonexistent_user(self, db_session):
        assert authenticate_user(db_session, "ghost", "pw") is None

    def test_get_user_by_id(self, db_session, test_user):
        found = get_user_by_id(db_session, test_user.id)
        assert found is not None
        assert found.id == test_user.id

    def test_get_user_by_id_not_found(self, db_session):
        assert get_user_by_id(db_session, 99999) is None
