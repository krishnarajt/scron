"""
Tests for job_service — direct service-layer tests for all CRUD,
env vars, executions, script versions, tags, notifications, templates.
"""

import pytest
from datetime import datetime, timezone

from app.services import job_service
from app.db.models import (
    Job,
    JobEnvVar,
    JobExecution,
    JobTag,
    JobTemplate,
)


class TestJobCRUDService:
    def test_create_job_returns_enriched(self, db_session, test_user):
        result = job_service.create_job(
            db_session,
            test_user.id,
            "My Job",
            "print('hi')",
            "*/5 * * * *",
        )
        assert result["name"] == "My Job"
        assert result["id"] is not None
        assert result["depends_on"] == []
        assert result["tags"] == []

    def test_create_job_with_all_fields(self, db_session, test_user, test_tag):
        result = job_service.create_job(
            db_session,
            test_user.id,
            "Full Job",
            "print(1)",
            "0 * * * *",
            description="desc",
            script_type="bash",
            is_active=False,
            timeout_seconds=120,
            depends_on=[],
            tag_ids=[test_tag.id],
        )
        assert result["timeout_seconds"] == 120
        assert result["is_active"] is False
        assert len(result["tags"]) == 1
        assert result["tags"][0]["name"] == "production"

    def test_get_job(self, db_session, test_user, test_job):
        job = job_service.get_job(db_session, test_job.id, test_user.id)
        assert job is not None
        assert job.name == "Test Job"

    def test_get_job_wrong_user(self, db_session, test_job):
        assert job_service.get_job(db_session, test_job.id, 99999) is None

    def test_get_job_nonexistent(self, db_session, test_user):
        assert job_service.get_job(db_session, "fake-uuid", test_user.id) is None

    def test_get_job_response(self, db_session, test_user, test_job):
        resp = job_service.get_job_response(db_session, test_job.id, test_user.id)
        assert resp is not None
        assert resp["name"] == "Test Job"
        assert "tags" in resp
        assert "dependency_names" in resp

    def test_list_jobs_empty(self, db_session, test_user):
        jobs, total = job_service.list_jobs(db_session, test_user.id)
        assert total == 0

    def test_list_jobs_with_data(self, db_session, test_user, test_job):
        jobs, total = job_service.list_jobs(db_session, test_user.id)
        assert total == 1

    def test_list_jobs_filter_by_tag(self, db_session, test_user, test_job, test_tag):
        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        db_session.commit()
        jobs, total = job_service.list_jobs(
            db_session, test_user.id, tag_id=test_tag.id
        )
        assert total == 1
        jobs_no_tag, _ = job_service.list_jobs(db_session, test_user.id, tag_id=99999)
        assert len(jobs_no_tag) == 0

    def test_update_job(self, db_session, test_user, test_job):
        result = job_service.update_job(
            db_session, test_job.id, test_user.id, name="Updated"
        )
        assert result["name"] == "Updated"

    def test_update_job_script_creates_version(self, db_session, test_user, test_job):
        job_service.update_job(
            db_session, test_job.id, test_user.id, script_content="print('v2')"
        )
        versions, total = job_service.get_script_versions(
            db_session, test_job.id, test_user.id
        )
        assert total == 2

    def test_update_job_tags(self, db_session, test_user, test_job, test_tag):
        result = job_service.update_job(
            db_session, test_job.id, test_user.id, tag_ids=[test_tag.id]
        )
        assert len(result["tags"]) == 1
        # Clear tags
        result = job_service.update_job(
            db_session, test_job.id, test_user.id, tag_ids=[]
        )
        assert len(result["tags"]) == 0

    def test_update_nonexistent_returns_none(self, db_session, test_user):
        assert (
            job_service.update_job(db_session, "fake", test_user.id, name="x") is None
        )

    def test_delete_job(self, db_session, test_user, test_job):
        assert job_service.delete_job(db_session, test_job.id, test_user.id) is True
        assert job_service.get_job(db_session, test_job.id, test_user.id) is None

    def test_delete_nonexistent(self, db_session, test_user):
        assert job_service.delete_job(db_session, "fake", test_user.id) is False

    def test_get_all_active_jobs(self, db_session, test_user, test_job):
        active = job_service.get_all_active_jobs(db_session)
        assert len(active) >= 1

    def test_duplicate_job(self, db_session, test_user, test_job, test_tag):
        # Add tag and env var to original
        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        from app.services.crypto_service import encrypt_value

        db_session.add(
            JobEnvVar(
                job_id=test_job.id,
                var_key="KEY",
                encrypted_value=encrypt_value("val", test_user.salt),
            )
        )
        db_session.commit()

        dupe = job_service.duplicate_job(db_session, test_job.id, test_user.id)
        assert dupe is not None
        assert dupe["name"] == "Test Job (copy)"
        assert dupe["is_active"] is False
        assert len(dupe["tags"]) == 1  # Tags copied

        # Env vars copied
        env = job_service.get_env_vars(db_session, dupe["id"], test_user.id)
        assert len(env) == 1
        assert env[0]["var_key"] == "KEY"


class TestEnvVarsService:
    def test_set_and_get_env_var(self, db_session, test_user, test_job):
        job_service.set_env_var(
            db_session, test_job.id, test_user.id, "API_KEY", "secret123"
        )
        env_vars = job_service.get_env_vars(db_session, test_job.id, test_user.id)
        assert len(env_vars) == 1
        assert env_vars[0]["var_key"] == "API_KEY"
        assert env_vars[0]["var_value"] == "secret123"

    def test_upsert_env_var(self, db_session, test_user, test_job):
        job_service.set_env_var(db_session, test_job.id, test_user.id, "K", "v1")
        job_service.set_env_var(db_session, test_job.id, test_user.id, "K", "v2")
        env = job_service.get_env_vars(db_session, test_job.id, test_user.id)
        assert len(env) == 1
        assert env[0]["var_value"] == "v2"

    def test_bulk_set_replaces_all(self, db_session, test_user, test_job):
        job_service.set_env_var(db_session, test_job.id, test_user.id, "OLD", "x")
        job_service.set_env_vars_bulk(
            db_session,
            test_job.id,
            test_user.id,
            [
                {"var_key": "NEW1", "var_value": "a"},
                {"var_key": "NEW2", "var_value": "b"},
            ],
        )
        env = job_service.get_env_vars(db_session, test_job.id, test_user.id)
        keys = {e["var_key"] for e in env}
        assert keys == {"NEW1", "NEW2"}  # OLD is gone

    def test_get_decrypted_dict(self, db_session, test_user, test_job):
        job_service.set_env_var(db_session, test_job.id, test_user.id, "A", "1")
        job_service.set_env_var(db_session, test_job.id, test_user.id, "B", "2")
        d = job_service.get_env_vars_decrypted_dict(
            db_session, test_job.id, test_user.id
        )
        assert d == {"A": "1", "B": "2"}

    def test_delete_env_var(self, db_session, test_user, test_job):
        job_service.set_env_var(db_session, test_job.id, test_user.id, "DEL", "x")
        assert job_service.delete_env_var(db_session, test_job.id, "DEL") is True
        assert job_service.delete_env_var(db_session, test_job.id, "DEL") is False

    def test_get_user_salt_missing_user(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            job_service._get_user_salt(db_session, 99999)


class TestExecutionsService:
    def test_create_execution(self, db_session, test_job):
        ex = job_service.create_execution(db_session, test_job.id)
        assert ex.status == "running"
        assert ex.script_version_id is not None  # Auto-detected

    def test_complete_execution_success(self, db_session, test_job):
        ex = job_service.create_execution(db_session, test_job.id)
        completed = job_service.complete_execution(
            db_session, ex.id, status="success", exit_code=0, log_output="done"
        )
        assert completed.status == "success"
        assert completed.duration_seconds > 0
        assert completed.ended_at is not None
        assert completed.pid is None  # Cleared on completion

    def test_complete_execution_failure(self, db_session, test_job):
        ex = job_service.create_execution(db_session, test_job.id)
        completed = job_service.complete_execution(
            db_session,
            ex.id,
            status="failure",
            exit_code=1,
            error_summary="crash",
            log_output="error log",
        )
        assert completed.status == "failure"
        assert completed.error_summary == "crash"

    def test_complete_nonexistent_raises(self, db_session):
        with pytest.raises(ValueError):
            job_service.complete_execution(db_session, 99999, "success")

    def test_set_execution_pid(self, db_session, test_job):
        ex = job_service.create_execution(db_session, test_job.id)
        job_service.set_execution_pid(db_session, ex.id, 12345)
        db_session.refresh(ex)
        assert ex.pid == 12345

    def test_get_executions_pagination(self, db_session, test_job):
        for _ in range(5):
            job_service.create_execution(db_session, test_job.id)
        execs, total = job_service.get_executions(
            db_session, test_job.id, limit=3, offset=0
        )
        assert total == 5
        assert len(execs) == 3

    def test_error_summary_truncated(self, db_session, test_job):
        ex = job_service.create_execution(db_session, test_job.id)
        long_error = "x" * 1000
        completed = job_service.complete_execution(
            db_session,
            ex.id,
            status="failure",
            exit_code=1,
            error_summary=long_error,
        )
        assert len(completed.error_summary) == 500


class TestScriptVersionsService:
    def test_initial_version_created(self, db_session, test_user, test_job):
        versions, total = job_service.get_script_versions(
            db_session, test_job.id, test_user.id
        )
        assert total == 1
        assert versions[0].version == 1

    def test_get_specific_version(self, db_session, test_user, test_job):
        v = job_service.get_script_version(db_session, test_job.id, test_user.id, 1)
        assert v is not None
        assert v.script_content == "print('hello')"

    def test_get_nonexistent_version(self, db_session, test_user, test_job):
        assert (
            job_service.get_script_version(db_session, test_job.id, test_user.id, 99)
            is None
        )

    def test_restore_version(self, db_session, test_user, test_job):
        job_service.update_job(
            db_session, test_job.id, test_user.id, script_content="v2"
        )
        result = job_service.restore_script_version(
            db_session, test_job.id, test_user.id, 1
        )
        assert result is not None
        assert result["script_content"] == "print('hello')"
        versions, total = job_service.get_script_versions(
            db_session, test_job.id, test_user.id
        )
        assert total == 3  # v1, v2, restored

    def test_restore_nonexistent_version(self, db_session, test_user, test_job):
        assert (
            job_service.restore_script_version(
                db_session, test_job.id, test_user.id, 999
            )
            is None
        )

    def test_restore_nonexistent_job(self, db_session, test_user):
        assert (
            job_service.restore_script_version(db_session, "fake", test_user.id, 1)
            is None
        )


class TestDAGService:
    def test_no_dependencies_always_met(self, db_session, test_job):
        assert job_service.check_dependencies_met(db_session, test_job) is True

    def test_dependency_never_run(self, db_session, test_user, test_job):
        dep = Job(
            user_id=test_user.id,
            name="Down",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
            depends_on=[test_job.id],
        )
        db_session.add(dep)
        db_session.commit()
        assert job_service.check_dependencies_met(db_session, dep) is False

    def test_dependency_succeeded(self, db_session, test_user, test_job):
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=datetime.now(timezone.utc),
                status="success",
                exit_code=0,
            )
        )
        db_session.commit()
        dep = Job(
            user_id=test_user.id,
            name="Down",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
            depends_on=[test_job.id],
        )
        db_session.add(dep)
        db_session.commit()
        assert job_service.check_dependencies_met(db_session, dep) is True

    def test_dependency_failed(self, db_session, test_user, test_job):
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=datetime.now(timezone.utc),
                status="failure",
                exit_code=1,
            )
        )
        db_session.commit()
        dep = Job(
            user_id=test_user.id,
            name="Down",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
            depends_on=[test_job.id],
        )
        db_session.add(dep)
        db_session.commit()
        assert job_service.check_dependencies_met(db_session, dep) is False

    def test_multi_dependency_all_must_succeed(self, db_session, test_user):
        j1 = Job(
            user_id=test_user.id,
            name="A",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
        )
        j2 = Job(
            user_id=test_user.id,
            name="B",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
        )
        db_session.add_all([j1, j2])
        db_session.flush()
        downstream = Job(
            user_id=test_user.id,
            name="C",
            script_content="x",
            script_type="python",
            cron_expression="0 * * * *",
            depends_on=[j1.id, j2.id],
        )
        db_session.add(downstream)
        # Only j1 succeeded
        db_session.add(
            JobExecution(
                job_id=j1.id,
                started_at=datetime.now(timezone.utc),
                status="success",
            )
        )
        db_session.commit()
        assert job_service.check_dependencies_met(db_session, downstream) is False

        # Now j2 also succeeds
        db_session.add(
            JobExecution(
                job_id=j2.id,
                started_at=datetime.now(timezone.utc),
                status="success",
            )
        )
        db_session.commit()
        assert job_service.check_dependencies_met(db_session, downstream) is True


class TestTagsService:
    def test_create_tag(self, db_session, test_user):
        tag = job_service.create_tag(db_session, test_user.id, "staging", "#f59e0b")
        assert tag.name == "staging"
        assert tag.color == "#f59e0b"

    def test_list_tags_with_job_counts(self, db_session, test_user, test_tag, test_job):
        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        db_session.commit()
        tags = job_service.list_tags(db_session, test_user.id)
        assert len(tags) >= 1
        matching = [t for t in tags if t["id"] == test_tag.id]
        assert matching[0]["job_count"] == 1

    def test_update_tag(self, db_session, test_user, test_tag):
        updated = job_service.update_tag(
            db_session, test_tag.id, test_user.id, name="prod"
        )
        assert updated.name == "prod"

    def test_update_nonexistent_tag(self, db_session, test_user):
        assert job_service.update_tag(db_session, 99999, test_user.id, name="x") is None

    def test_delete_tag(self, db_session, test_user, test_tag):
        assert job_service.delete_tag(db_session, test_tag.id, test_user.id) is True
        assert job_service.delete_tag(db_session, test_tag.id, test_user.id) is False

    def test_delete_tag_removes_associations(
        self, db_session, test_user, test_tag, test_job
    ):
        db_session.add(JobTag(job_id=test_job.id, tag_id=test_tag.id))
        db_session.commit()
        job_service.delete_tag(db_session, test_tag.id, test_user.id)
        assocs = db_session.query(JobTag).filter(JobTag.tag_id == test_tag.id).all()
        assert len(assocs) == 0


class TestNotificationSettingsService:
    def test_get_default_none(self, db_session, test_user):
        assert job_service.get_notification_settings(db_session, test_user.id) is None

    def test_upsert_creates(self, db_session, test_user):
        s = job_service.upsert_notification_settings(
            db_session, test_user.id, telegram_enabled=True, telegram_chat_id="123"
        )
        assert s.telegram_enabled is True
        assert s.telegram_chat_id == "123"
        assert s.notify_on == "failure_only"  # default

    def test_upsert_updates(self, db_session, test_user):
        job_service.upsert_notification_settings(
            db_session, test_user.id, notify_on="always"
        )
        s = job_service.upsert_notification_settings(
            db_session, test_user.id, notify_on="never"
        )
        assert s.notify_on == "never"


class TestTemplatesService:
    def test_list_templates_empty(self, db_session, test_user):
        templates = job_service.list_templates(db_session, test_user.id)
        assert len(templates) == 0

    def test_list_includes_system_and_user(self, db_session, test_user):
        db_session.add(
            JobTemplate(
                name="System",
                description="",
                category="general",
                script_content="x",
                script_type="python",
                default_cron="0 * * * *",
                user_id=None,
            )
        )
        db_session.add(
            JobTemplate(
                name="Mine",
                description="",
                category="general",
                script_content="y",
                script_type="python",
                default_cron="0 * * * *",
                user_id=test_user.id,
            )
        )
        db_session.commit()
        templates = job_service.list_templates(db_session, test_user.id)
        names = {t.name for t in templates}
        assert "System" in names
        assert "Mine" in names


class TestNextRuns:
    def test_valid_cron(self):
        runs = job_service.get_next_runs("*/5 * * * *", count=3)
        assert len(runs) == 3

    def test_invalid_cron(self):
        runs = job_service.get_next_runs("not valid", count=3)
        assert runs == []

    def test_count_respected(self):
        runs = job_service.get_next_runs("0 * * * *", count=10)
        assert len(runs) == 10
