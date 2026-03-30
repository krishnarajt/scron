"""
Tests for job routes — CRUD, env vars, script versions, trigger, duplicate, next-runs, stream-status.
"""

import pytest
from unittest.mock import patch


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


class TestJobCRUD:
    def test_create_job_minimal(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Simple",
                "script_content": "print('hi')",
                "cron_expression": "*/5 * * * *",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        d = response.json()
        assert d["name"] == "Simple"
        assert d["script_type"] == "python"
        assert d["is_active"] is True
        assert d["timeout_seconds"] == 0
        assert d["depends_on"] == []
        assert d["tags"] == []

    def test_create_job_all_fields(self, client, auth_headers, test_tag):
        response = client.post(
            "/api/jobs",
            json={
                "name": "Full",
                "description": "A full job",
                "script_content": "echo hi",
                "script_type": "bash",
                "cron_expression": "0 9 * * 1-5",
                "is_active": False,
                "timeout_seconds": 300,
                "depends_on": [],
                "tag_ids": [test_tag.id],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        d = response.json()
        assert d["script_type"] == "bash"
        assert d["is_active"] is False
        assert d["timeout_seconds"] == 300
        assert len(d["tags"]) == 1

    def test_create_job_invalid_cron(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={"name": "Bad", "script_content": "x", "cron_expression": "nope"},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_create_job_missing_name(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={"script_content": "x", "cron_expression": "* * * * *"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_job_empty_script(self, client, auth_headers):
        response = client.post(
            "/api/jobs",
            json={"name": "x", "script_content": "", "cron_expression": "* * * * *"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_list_jobs_empty(self, client, auth_headers):
        response = client.get("/api/jobs", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_list_jobs_with_data(self, client, auth_headers, test_job):
        response = client.get("/api/jobs", headers=auth_headers)
        assert response.json()["total"] >= 1

    def test_list_jobs_filter_by_tag(
        self, client, auth_headers, test_job, test_tag, db_session
    ):
        from app.db.models import JobTag

        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        db_session.commit()
        response = client.get(f"/api/jobs?tag_id={test_tag.id}", headers=auth_headers)
        assert response.json()["total"] >= 1

    def test_get_job(self, client, auth_headers, test_job):
        response = client.get(f"/api/jobs/{test_job.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["name"] == "Test Job"
        assert "tags" in response.json()
        assert "dependency_names" in response.json()

    def test_get_job_not_found(self, client, auth_headers):
        response = client.get("/api/jobs/nonexistent", headers=auth_headers)
        assert response.status_code == 404

    def test_update_job_name(self, client, auth_headers, test_job):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"

    def test_update_job_invalid_cron(self, client, auth_headers, test_job):
        response = client.patch(
            f"/api/jobs/{test_job.id}",
            json={"cron_expression": "invalid"},
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_update_job_not_found(self, client, auth_headers):
        response = client.patch(
            "/api/jobs/fake", json={"name": "x"}, headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_job(self, client, auth_headers, test_job):
        response = client.delete(f"/api/jobs/{test_job.id}", headers=auth_headers)
        assert response.status_code == 204
        assert (
            client.get(f"/api/jobs/{test_job.id}", headers=auth_headers).status_code
            == 404
        )

    def test_delete_job_not_found(self, client, auth_headers):
        response = client.delete("/api/jobs/fake", headers=auth_headers)
        assert response.status_code == 404

    def test_unauthenticated_rejected(self, client):
        assert client.get("/api/jobs").status_code in (401, 403)
        assert client.post("/api/jobs", json={}).status_code in (401, 403)


class TestEnvVars:
    def test_create_env_var(self, client, auth_headers, test_job):
        response = client.post(
            f"/api/jobs/{test_job.id}/env",
            json={"var_key": "API_KEY", "var_value": "secret"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["var_key"] == "API_KEY"
        assert response.json()["var_value"] == "secret"

    def test_upsert_returns_200(self, client, auth_headers, test_job):
        client.post(
            f"/api/jobs/{test_job.id}/env",
            json={"var_key": "K", "var_value": "v1"},
            headers=auth_headers,
        )
        response = client.post(
            f"/api/jobs/{test_job.id}/env",
            json={"var_key": "K", "var_value": "v2"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["var_value"] == "v2"

    def test_list_env_vars(self, client, auth_headers, test_job):
        client.post(
            f"/api/jobs/{test_job.id}/env",
            json={"var_key": "A", "var_value": "1"},
            headers=auth_headers,
        )
        response = client.get(f"/api/jobs/{test_job.id}/env", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_bulk_set(self, client, auth_headers, test_job):
        response = client.put(
            f"/api/jobs/{test_job.id}/env",
            json={
                "env_vars": [
                    {"var_key": "X", "var_value": "1"},
                    {"var_key": "Y", "var_value": "2"},
                ]
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["total"] == 2

    def test_delete_env_var(self, client, auth_headers, test_job):
        client.post(
            f"/api/jobs/{test_job.id}/env",
            json={"var_key": "DEL", "var_value": "x"},
            headers=auth_headers,
        )
        response = client.delete(
            f"/api/jobs/{test_job.id}/env/DEL", headers=auth_headers
        )
        assert response.status_code == 204

    def test_delete_nonexistent_env_var(self, client, auth_headers, test_job):
        response = client.delete(
            f"/api/jobs/{test_job.id}/env/GHOST", headers=auth_headers
        )
        assert response.status_code == 404

    def test_env_vars_on_nonexistent_job(self, client, auth_headers):
        response = client.get("/api/jobs/fake/env", headers=auth_headers)
        assert response.status_code == 404


class TestTrigger:
    def test_trigger_job(self, client, auth_headers, test_job):
        response = client.post(f"/api/jobs/{test_job.id}/trigger", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["execution_id"] == 1

    def test_trigger_nonexistent_job(self, client, auth_headers):
        response = client.post("/api/jobs/fake/trigger", headers=auth_headers)
        assert response.status_code == 404


class TestCancelAndReplay:
    def test_cancel_execution(self, client, auth_headers, test_job):
        response = client.post(
            f"/api/jobs/{test_job.id}/executions/1/cancel", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["cancelled"] is True

    def test_cancel_nonexistent_job(self, client, auth_headers):
        response = client.post(
            "/api/jobs/fake/executions/1/cancel", headers=auth_headers
        )
        assert response.status_code == 404

    def test_replay_execution(self, client, auth_headers, test_job):
        response = client.post(
            f"/api/jobs/{test_job.id}/replay",
            json={"execution_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["execution_id"] == 99

    def test_replay_nonexistent_job(self, client, auth_headers):
        response = client.post(
            "/api/jobs/fake/replay", json={"execution_id": 1}, headers=auth_headers
        )
        assert response.status_code == 404


class TestScriptVersions:
    def test_list_versions(self, client, auth_headers, test_job):
        response = client.get(f"/api/jobs/{test_job.id}/versions", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["versions"][0]["version"] == 1

    def test_get_specific_version(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/jobs/{test_job.id}/versions/1", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["script_content"] == "print('hello')"

    def test_get_nonexistent_version(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/jobs/{test_job.id}/versions/99", headers=auth_headers
        )
        assert response.status_code == 404

    def test_update_creates_version(self, client, auth_headers, test_job):
        client.patch(
            f"/api/jobs/{test_job.id}",
            json={"script_content": "print('v2')"},
            headers=auth_headers,
        )
        response = client.get(f"/api/jobs/{test_job.id}/versions", headers=auth_headers)
        assert response.json()["total"] == 2

    def test_restore_version(self, client, auth_headers, test_job):
        client.patch(
            f"/api/jobs/{test_job.id}",
            json={"script_content": "print('v2')"},
            headers=auth_headers,
        )
        response = client.post(
            f"/api/jobs/{test_job.id}/versions/1/restore", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["script_content"] == "print('hello')"

    def test_restore_nonexistent_version(self, client, auth_headers, test_job):
        response = client.post(
            f"/api/jobs/{test_job.id}/versions/99/restore", headers=auth_headers
        )
        assert response.status_code == 404

    def test_versions_on_nonexistent_job(self, client, auth_headers):
        assert (
            client.get("/api/jobs/fake/versions", headers=auth_headers).status_code
            == 404
        )


class TestDuplicate:
    def test_duplicate_job(self, client, auth_headers, test_job):
        response = client.post(
            f"/api/jobs/{test_job.id}/duplicate", headers=auth_headers
        )
        assert response.status_code == 201
        d = response.json()
        assert d["name"] == "Test Job (copy)"
        assert d["is_active"] is False
        assert d["id"] != test_job.id

    def test_duplicate_nonexistent(self, client, auth_headers):
        response = client.post("/api/jobs/fake/duplicate", headers=auth_headers)
        assert response.status_code == 404


class TestNextRuns:
    def test_get_next_runs(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/jobs/{test_job.id}/next-runs?count=3", headers=auth_headers
        )
        assert response.status_code == 200
        assert len(response.json()["next_runs"]) == 3

    def test_next_runs_nonexistent(self, client, auth_headers):
        response = client.get("/api/jobs/fake/next-runs", headers=auth_headers)
        assert response.status_code == 404


class TestStreamStatus:
    def test_stream_status_no_stream(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/jobs/{test_job.id}/stream-status", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["is_streaming"] is False

    def test_stream_status_nonexistent(self, client, auth_headers):
        response = client.get("/api/jobs/fake/stream-status", headers=auth_headers)
        assert response.status_code == 404


class TestExecutionHistory:
    def test_list_executions_empty(self, client, auth_headers, test_job):
        response = client.get(
            f"/api/jobs/{test_job.id}/executions", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_list_executions_with_data(
        self, client, auth_headers, test_job, db_session
    ):
        from datetime import datetime, timezone
        from app.db.models import JobExecution

        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=datetime.now(timezone.utc),
                status="success",
                exit_code=0,
            )
        )
        db_session.commit()
        response = client.get(
            f"/api/jobs/{test_job.id}/executions", headers=auth_headers
        )
        assert response.json()["total"] == 1

    def test_list_executions_pagination(
        self, client, auth_headers, test_job, db_session
    ):
        from datetime import datetime, timezone
        from app.db.models import JobExecution

        for _ in range(5):
            db_session.add(
                JobExecution(
                    job_id=test_job.id,
                    started_at=datetime.now(timezone.utc),
                    status="success",
                )
            )
        db_session.commit()
        response = client.get(
            f"/api/jobs/{test_job.id}/executions?limit=2&offset=0", headers=auth_headers
        )
        assert response.json()["total"] == 5
        assert len(response.json()["executions"]) == 2

    def test_executions_nonexistent_job(self, client, auth_headers):
        response = client.get("/api/jobs/fake/executions", headers=auth_headers)
        assert response.status_code == 404
