"""
Tests for new feature routes: tags, notifications, templates,
DAG dependencies via routes, timeout, user profile.
"""

import pytest
from unittest.mock import patch

from app.db.models import JobTag, JobTemplate


@pytest.fixture(autouse=True)
def mock_scheduler():
    with (
        patch("app.api.job_routes.register_job"),
        patch("app.api.job_routes.unregister_job"),
        patch("app.api.job_routes.trigger_job_now", return_value=1),
        patch("app.api.job_routes.cancel_execution", return_value=True),
        patch("app.api.job_routes.replay_execution", return_value=99),
    ):
        yield


# ---------------------------------------------------------------------------
# Tags (route-level)
# ---------------------------------------------------------------------------


class TestTagRoutes:
    def test_create_tag(self, client, auth_headers):
        response = client.post(
            "/api/tags",
            json={"name": "staging", "color": "#f59e0b"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["name"] == "staging"
        assert response.json()["color"] == "#f59e0b"
        assert response.json()["job_count"] == 0

    def test_create_tag_default_color(self, client, auth_headers):
        response = client.post(
            "/api/tags",
            json={"name": "default"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["color"] == "#6366f1"

    def test_create_tag_invalid_color(self, client, auth_headers):
        response = client.post(
            "/api/tags",
            json={"name": "bad", "color": "red"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_tag_empty_name(self, client, auth_headers):
        response = client.post(
            "/api/tags",
            json={"name": ""},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_list_tags(self, client, auth_headers, test_tag):
        response = client.get("/api/tags", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] >= 1

    def test_list_tags_empty(self, client, auth_headers):
        response = client.get("/api/tags", headers=auth_headers)
        assert response.json()["total"] == 0

    def test_update_tag_name(self, client, auth_headers, test_tag):
        response = client.patch(
            f"/api/tags/{test_tag.id}",
            json={"name": "prod"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "prod"

    def test_update_tag_color(self, client, auth_headers, test_tag):
        response = client.patch(
            f"/api/tags/{test_tag.id}",
            json={"color": "#10b981"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["color"] == "#10b981"

    def test_update_nonexistent_tag(self, client, auth_headers):
        response = client.patch(
            "/api/tags/99999",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_delete_tag(self, client, auth_headers, test_tag):
        assert (
            client.delete(f"/api/tags/{test_tag.id}", headers=auth_headers).status_code
            == 204
        )

    def test_delete_nonexistent_tag(self, client, auth_headers):
        assert client.delete("/api/tags/99999", headers=auth_headers).status_code == 404

    def test_create_job_with_tags(self, client, auth_headers, test_tag):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Tagged",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "tag_ids": [test_tag.id],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert len(response.json()["tags"]) == 1

    def test_update_job_tags(self, client, auth_headers, test_job, test_tag):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"tag_ids": [test_tag.id]},
            headers=auth_headers,
        )
        assert len(response.json()["tags"]) == 1
        # Clear
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"tag_ids": []},
            headers=auth_headers,
        )
        assert len(response.json()["tags"]) == 0

    def test_tag_job_count(self, client, auth_headers, test_tag, test_job, db_session):
        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        db_session.commit()
        response = client.get("/api/tags", headers=auth_headers)
        tag_data = [t for t in response.json()["tags"] if t["id"] == test_tag.id]
        assert tag_data[0]["job_count"] == 1


# ---------------------------------------------------------------------------
# DAG Dependencies (route-level)
# ---------------------------------------------------------------------------


class TestDAGRoutes:
    def test_create_with_dependency(self, client, auth_headers, test_job):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Downstream",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "depends_on": [test_job.id],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert test_job.id in response.json()["depends_on"]
        assert len(response.json()["dependency_names"]) == 1
        assert response.json()["dependency_names"][0]["name"] == "Test Job"

    def test_create_with_multiple_deps(self, client, auth_headers, test_job):
        # Create a second job
        j2 = client.post(
            "/api/jobs",
            json={"name": "J2", "script_content": "x", "cron_expression": "0 * * * *"},
            headers=auth_headers,
        ).json()
        response = client.post(
            "/api/jobs",
            json={
                "name": "Both",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "depends_on": [test_job.id, j2["id"]],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert len(response.json()["depends_on"]) == 2

    def test_reject_self_dependency(self, client, auth_headers, test_job):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"depends_on": [test_job.id]},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "itself" in response.json()["detail"].lower()

    def test_reject_nonexistent_dependency(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Bad",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "depends_on": ["nonexistent-uuid"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_update_dependencies(self, client, auth_headers, test_job):
        j2 = client.post(
            "/api/jobs",
            json={"name": "J2", "script_content": "x", "cron_expression": "0 * * * *"},
            headers=auth_headers,
        ).json()
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"depends_on": [j2["id"]]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert j2["id"] in response.json()["depends_on"]

    def test_clear_dependencies(self, client, auth_headers, test_job):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"depends_on": []},
            headers=auth_headers,
        )
        assert response.json()["depends_on"] == []


# ---------------------------------------------------------------------------
# Timeout (route-level)
# ---------------------------------------------------------------------------


class TestTimeoutRoutes:
    def test_create_with_timeout(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Quick",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "timeout_seconds": 60,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["timeout_seconds"] == 60

    def test_default_timeout_is_zero(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Default",
                "script_content": "x",
                "cron_expression": "0 * * * *",
            },
            headers=auth_headers,
        )
        assert response.json()["timeout_seconds"] == 0

    def test_update_timeout(self, client, auth_headers, test_job):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"timeout_seconds": 180},
            headers=auth_headers,
        )
        assert response.json()["timeout_seconds"] == 180

    def test_negative_timeout_rejected(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Neg",
                "script_content": "x",
                "cron_expression": "0 * * * *",
                "timeout_seconds": -1,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Notifications (route-level)
# ---------------------------------------------------------------------------


class TestNotificationRoutes:
    def test_get_defaults(self, client, auth_headers):
        response = client.get("/api/notifications", headers=auth_headers)
        assert response.status_code == 200
        d = response.json()
        assert d["telegram_enabled"] is False
        assert d["email_enabled"] is False
        assert d["notify_on"] == "failure_only"

    def test_enable_telegram(self, client, auth_headers):
        response = client.put(
            "/api/notifications",
            json={"telegram_enabled": True, "telegram_chat_id": "12345"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["telegram_enabled"] is True
        assert response.json()["telegram_chat_id"] == "12345"

    def test_change_notify_on(self, client, auth_headers):
        response = client.put(
            "/api/notifications",
            json={"notify_on": "always"},
            headers=auth_headers,
        )
        assert response.json()["notify_on"] == "always"

    def test_set_to_never(self, client, auth_headers):
        response = client.put(
            "/api/notifications",
            json={"notify_on": "never"},
            headers=auth_headers,
        )
        assert response.json()["notify_on"] == "never"

    def test_invalid_notify_on(self, client, auth_headers):
        response = client.put(
            "/api/notifications",
            json={"notify_on": "invalid"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_email_requires_email_set(self, client, auth_headers):
        response = client.put(
            "/api/notifications",
            json={"email_enabled": True},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()

    def test_email_after_setting_email(self, client, auth_headers):
        # Set email first
        client.patch(
            "/api/profile", json={"email": "me@test.com"}, headers=auth_headers
        )
        response = client.put(
            "/api/notifications",
            json={"email_enabled": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["email_enabled"] is True

    def test_partial_update_preserves_fields(self, client, auth_headers):
        client.put(
            "/api/notifications",
            json={"telegram_enabled": True, "telegram_chat_id": "111"},
            headers=auth_headers,
        )
        # Update only notify_on
        response = client.put(
            "/api/notifications",
            json={"notify_on": "always"},
            headers=auth_headers,
        )
        assert response.json()["telegram_enabled"] is True  # preserved


# ---------------------------------------------------------------------------
# Templates (route-level)
# ---------------------------------------------------------------------------


class TestTemplateRoutes:
    def test_list_templates_empty(self, client, auth_headers):
        response = client.get("/api/templates", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_list_templates_with_data(self, client, auth_headers, db_session):
        db_session.add(
            JobTemplate(
                name="Health Check",
                description="Ping a URL",
                category="monitoring",
                script_content="import urllib",
                script_type="python",
                default_cron="*/5 * * * *",
                user_id=None,
            )
        )
        db_session.add(
            JobTemplate(
                name="Backup",
                description="Backup DB",
                category="backup",
                script_content="pg_dump",
                script_type="bash",
                default_cron="0 2 * * *",
                user_id=None,
            )
        )
        db_session.commit()
        response = client.get("/api/templates", headers=auth_headers)
        assert response.json()["total"] == 2
        names = {t["name"] for t in response.json()["templates"]}
        assert "Health Check" in names
        assert "Backup" in names

    def test_template_response_fields(self, client, auth_headers, db_session):
        db_session.add(
            JobTemplate(
                name="Test",
                description="Desc",
                category="general",
                script_content="echo hi",
                script_type="bash",
                default_cron="0 * * * *",
                user_id=None,
            )
        )
        db_session.commit()
        response = client.get("/api/templates", headers=auth_headers)
        t = response.json()["templates"][0]
        assert "id" in t
        assert t["name"] == "Test"
        assert t["category"] == "general"
        assert t["script_type"] == "bash"
        assert t["default_cron"] == "0 * * * *"


# ---------------------------------------------------------------------------
# User Profile (route-level)
# ---------------------------------------------------------------------------


class TestProfileRoutes:
    def test_get_profile(self, client, auth_headers):
        response = client.get("/api/profile", headers=auth_headers)
        assert response.status_code == 200
        d = response.json()
        assert d["username"] == "testuser"
        assert "email" in d
        assert "created_at" in d

    def test_update_email(self, client, auth_headers):
        response = client.patch(
            "/api/profile",
            json={"email": "new@test.com"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["email"] == "new@test.com"

    def test_update_display_name(self, client, auth_headers):
        response = client.patch(
            "/api/profile",
            json={"display_name": "Krishna"},
            headers=auth_headers,
        )
        assert response.json()["display_name"] == "Krishna"

    def test_update_both_fields(self, client, auth_headers):
        response = client.patch(
            "/api/profile",
            json={"display_name": "Bob", "email": "bob@x.com"},
            headers=auth_headers,
        )
        assert response.json()["display_name"] == "Bob"
        assert response.json()["email"] == "bob@x.com"

    def test_update_preserves_unchanged(self, client, auth_headers):
        client.patch(
            "/api/profile", json={"email": "keep@test.com"}, headers=auth_headers
        )
        response = client.patch(
            "/api/profile",
            json={"display_name": "New"},
            headers=auth_headers,
        )
        assert response.json()["email"] == "keep@test.com"
        assert response.json()["display_name"] == "New"

    def test_profile_unauthenticated(self, client):
        assert client.get("/api/profile").status_code in (401, 403)


# ---------------------------------------------------------------------------
# Analytics routes (route-level)
# ---------------------------------------------------------------------------


class TestAnalyticsRoutes:
    def test_overview(self, client, auth_headers):
        response = client.get("/api/analytics/overview", headers=auth_headers)
        assert response.status_code == 200
        assert "total_jobs" in response.json()

    def test_timeline(self, client, auth_headers):
        response = client.get("/api/analytics/timeline?days=3", headers=auth_headers)
        assert response.status_code == 200

    def test_heatmap(self, client, auth_headers):
        response = client.get("/api/analytics/heatmap?days=3", headers=auth_headers)
        assert response.status_code == 200

    def test_job_breakdown(self, client, auth_headers):
        response = client.get("/api/analytics/jobs/breakdown", headers=auth_headers)
        assert response.status_code == 200

    def test_job_stats(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/analytics/jobs/{test_job.id}/stats",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["job_name"] == "Test Job"

    def test_job_stats_not_found(self, client, auth_headers):
        response = client.get("/api/analytics/jobs/fake/stats", headers=auth_headers)
        assert response.status_code == 404

    def test_job_duration(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/analytics/jobs/{test_job.id}/duration",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_job_timeline(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/analytics/jobs/{test_job.id}/timeline?days=3",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_analytics_unauthenticated(self, client):
        assert client.get("/api/analytics/overview").status_code in (401, 403)
