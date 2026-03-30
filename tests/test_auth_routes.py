"""
Tests for authentication routes — signup, login, refresh, logout,
rate limiting, token lifecycle.
"""


class TestSignup:
    def test_signup_success(self, client):
        response = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "password123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["message"] == "Account created successfully"

    def test_signup_with_email(self, client):
        response = client.post(
            "/api/auth/signup",
            json={"username": "bob", "password": "pass123", "email": "bob@example.com"},
        )
        assert response.status_code == 200
        assert "accessToken" in response.json()

    def test_signup_duplicate_username_returns_400(self, client):
        client.post("/api/auth/signup", json={"username": "alice", "password": "p1"})
        response = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "p2"}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Username already exists"

    def test_signup_missing_password_returns_422(self, client):
        response = client.post("/api/auth/signup", json={"username": "alice"})
        assert response.status_code == 422

    def test_signup_missing_username_returns_422(self, client):
        response = client.post("/api/auth/signup", json={"password": "pass"})
        assert response.status_code == 422

    def test_signup_empty_body_returns_422(self, client):
        response = client.post("/api/auth/signup", json={})
        assert response.status_code == 422


class TestLogin:
    def test_login_success(self, client):
        client.post("/api/auth/signup", json={"username": "alice", "password": "pw123"})
        response = client.post(
            "/api/auth/login", json={"username": "alice", "password": "pw123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["message"] == "Login successful"

    def test_login_wrong_password_returns_401(self, client):
        client.post("/api/auth/signup", json={"username": "alice", "password": "pw123"})
        response = client.post(
            "/api/auth/login", json={"username": "alice", "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid username or password"

    def test_login_nonexistent_user_returns_401(self, client):
        response = client.post(
            "/api/auth/login", json={"username": "ghost", "password": "pw"}
        )
        assert response.status_code == 401

    def test_login_missing_fields_returns_422(self, client):
        response = client.post("/api/auth/login", json={"username": "alice"})
        assert response.status_code == 422


class TestTokenRefresh:
    def test_refresh_success(self, client):
        signup = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "pw123"}
        )
        refresh_token = signup.json()["refreshToken"]
        response = client.post(
            "/api/auth/refresh", json={"refreshToken": refresh_token}
        )
        assert response.status_code == 200
        data = response.json()
        assert "accessToken" in data
        assert "refreshToken" in data
        # New refresh token should differ (rotation)
        assert data["refreshToken"] != refresh_token

    def test_refresh_invalid_token_returns_401(self, client):
        response = client.post(
            "/api/auth/refresh", json={"refreshToken": "invalid.jwt.token"}
        )
        assert response.status_code == 401

    def test_refresh_old_token_invalid_after_rotation(self, client):
        signup = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "pw123"}
        )
        old_token = signup.json()["refreshToken"]
        # Rotate
        client.post("/api/auth/refresh", json={"refreshToken": old_token})
        # Old token should now be revoked
        response = client.post("/api/auth/refresh", json={"refreshToken": old_token})
        assert response.status_code == 401

    def test_refresh_missing_token_returns_422(self, client):
        response = client.post("/api/auth/refresh", json={})
        assert response.status_code == 422


class TestLogout:
    def test_logout_success(self, client):
        signup = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "pw123"}
        )
        refresh_token = signup.json()["refreshToken"]
        response = client.post("/api/auth/logout", json={"refreshToken": refresh_token})
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_refresh_after_logout_fails(self, client):
        signup = client.post(
            "/api/auth/signup", json={"username": "alice", "password": "pw123"}
        )
        token = signup.json()["refreshToken"]
        client.post("/api/auth/logout", json={"refreshToken": token})
        response = client.post("/api/auth/refresh", json={"refreshToken": token})
        assert response.status_code == 401

    def test_logout_missing_token_returns_422(self, client):
        response = client.post("/api/auth/logout", json={})
        assert response.status_code == 422
