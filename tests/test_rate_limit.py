"""
Tests for the rate limiter — sliding window, integration with auth endpoints.
"""

from app.api.rate_limit import _check_rate_limit, _requests, _lock


class TestRateLimiterCore:
    def setup_method(self):
        with _lock:
            _requests.clear()

    def test_allows_requests_within_limit(self):
        for _ in range(5):
            allowed, _ = _check_rate_limit("test:1", max_requests=5, window_seconds=60)
            assert allowed is True

    def test_blocks_over_limit(self):
        for _ in range(5):
            _check_rate_limit("test:2", max_requests=5, window_seconds=60)
        allowed, remaining = _check_rate_limit(
            "test:2", max_requests=5, window_seconds=60
        )
        assert allowed is False
        assert remaining == 0

    def test_different_keys_independent(self):
        for _ in range(5):
            _check_rate_limit("test:3", max_requests=5, window_seconds=60)
        allowed, _ = _check_rate_limit("test:4", max_requests=5, window_seconds=60)
        assert allowed is True

    def test_remaining_count(self):
        _, rem = _check_rate_limit("test:5", max_requests=3, window_seconds=60)
        assert rem == 2
        _, rem = _check_rate_limit("test:5", max_requests=3, window_seconds=60)
        assert rem == 1
        _, rem = _check_rate_limit("test:5", max_requests=3, window_seconds=60)
        assert rem == 0

    def test_single_request_limit(self):
        allowed, _ = _check_rate_limit("test:6", max_requests=1, window_seconds=60)
        assert allowed is True
        allowed, _ = _check_rate_limit("test:6", max_requests=1, window_seconds=60)
        assert allowed is False


class TestRateLimitIntegration:
    """Test rate limiting on actual auth endpoints."""

    def setup_method(self):
        with _lock:
            _requests.clear()

    def test_login_rate_limit(self, client):
        """Login endpoint should return 429 after too many attempts."""
        for _ in range(5):
            client.post(
                "/api/auth/login",
                json={"username": "x", "password": "y"},
            )
        response = client.post(
            "/api/auth/login",
            json={"username": "x", "password": "y"},
        )
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_signup_rate_limit(self, client):
        """Signup endpoint should return 429 after too many attempts."""
        for i in range(10):
            client.post(
                "/api/auth/signup",
                json={"username": f"user{i}", "password": "pw"},
            )
        response = client.post(
            "/api/auth/signup",
            json={"username": "overflow", "password": "pw"},
        )
        assert response.status_code == 429
