"""
Tests for the analytics service — all aggregation queries.
"""

from datetime import datetime, timezone, timedelta

from app.db.models import JobExecution
from app.services import analytics_service


class TestOverview:
    def test_empty_overview(self, db_session, test_user):
        result = analytics_service.get_overview(db_session, test_user.id)
        assert result["total_jobs"] == 0
        assert result["total_executions"] == 0
        assert result["success_rate"] == 0.0
        assert result["avg_duration_seconds"] == 0.0

    def test_overview_counts(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        for status, dur in [("success", 1.5), ("success", 2.0), ("failure", 0.5)]:
            db_session.add(
                JobExecution(
                    job_id=test_job.id,
                    started_at=now - timedelta(minutes=10),
                    ended_at=now,
                    duration_seconds=dur,
                    status=status,
                    exit_code=0 if status == "success" else 1,
                )
            )
        db_session.commit()

        r = analytics_service.get_overview(db_session, test_user.id)
        assert r["total_jobs"] == 1
        assert r["active_jobs"] == 1
        assert r["paused_jobs"] == 0
        assert r["total_executions"] == 3
        assert r["success_count"] == 2
        assert r["failure_count"] == 1
        assert r["running_count"] == 0
        assert r["success_rate"] == 66.7

    def test_overview_with_running(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="running",
            )
        )
        db_session.commit()
        r = analytics_service.get_overview(db_session, test_user.id)
        assert r["running_count"] == 1


class TestTimeline:
    def test_fills_missing_days(self, db_session, test_user):
        result = analytics_service.get_execution_timeline(
            db_session, test_user.id, days=7
        )
        assert len(result) == 8  # today + 7 days
        for entry in result:
            assert entry["success"] == 0
            assert entry["failure"] == 0

    def test_timeline_with_data(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="success",
                exit_code=0,
            )
        )
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="failure",
                exit_code=1,
            )
        )
        db_session.commit()

        result = analytics_service.get_execution_timeline(
            db_session, test_user.id, days=1
        )
        today = now.strftime("%Y-%m-%d")
        today_entry = [e for e in result if e["date"] == today]
        assert len(today_entry) == 1
        assert today_entry[0]["success"] == 1
        assert today_entry[0]["failure"] == 1


class TestHeatmap:
    def test_empty_heatmap(self, db_session, test_user):
        result = analytics_service.get_hourly_heatmap(db_session, test_user.id)
        assert result == []

    def test_heatmap_with_data(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="success",
            )
        )
        db_session.commit()
        result = analytics_service.get_hourly_heatmap(db_session, test_user.id)
        assert len(result) >= 1
        assert result[0]["count"] >= 1
        assert 0 <= result[0]["hour"] <= 23
        assert 0 <= result[0]["dow"] <= 6


class TestJobSuccessBreakdown:
    def test_empty_breakdown(self, db_session, test_user):
        result = analytics_service.get_job_success_breakdown(db_session, test_user.id)
        assert result == []

    def test_breakdown_with_data(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="success",
            )
        )
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now,
                status="failure",
            )
        )
        db_session.commit()
        result = analytics_service.get_job_success_breakdown(db_session, test_user.id)
        assert len(result) == 1
        assert result[0]["job_name"] == "Test Job"
        assert result[0]["success"] == 1
        assert result[0]["failure"] == 1
        assert result[0]["total"] == 2


class TestJobStats:
    def test_not_found(self, db_session, test_user):
        assert analytics_service.get_job_stats(db_session, "fake", test_user.id) is None

    def test_stats_with_executions(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now - timedelta(minutes=5),
                ended_at=now,
                duration_seconds=3.0,
                status="success",
                exit_code=0,
            )
        )
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now - timedelta(minutes=2),
                ended_at=now,
                duration_seconds=1.0,
                status="failure",
                exit_code=1,
            )
        )
        db_session.commit()

        r = analytics_service.get_job_stats(db_session, test_job.id, test_user.id)
        assert r["total_executions"] == 2
        assert r["success_count"] == 1
        assert r["failure_count"] == 1
        assert r["success_rate"] == 50.0
        assert r["avg_duration_seconds"] == 2.0
        assert r["max_duration_seconds"] == 3.0
        assert r["min_duration_seconds"] == 1.0
        assert r["last_status"] == "failure"
        assert r["last_execution_at"] is not None

    def test_stats_no_executions(self, db_session, test_user, test_job):
        r = analytics_service.get_job_stats(db_session, test_job.id, test_user.id)
        assert r["total_executions"] == 0
        assert r["success_rate"] == 0.0
        assert r["last_status"] is None


class TestJobDurationTrend:
    def test_empty_trend(self, db_session, test_user, test_job):
        result = analytics_service.get_job_duration_trend(
            db_session, test_job.id, test_user.id
        )
        assert result == []

    def test_trend_oldest_first(self, db_session, test_user, test_job):
        now = datetime.now(timezone.utc)
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now - timedelta(hours=2),
                duration_seconds=5.0,
                status="success",
            )
        )
        db_session.add(
            JobExecution(
                job_id=test_job.id,
                started_at=now - timedelta(hours=1),
                duration_seconds=3.0,
                status="success",
            )
        )
        db_session.commit()

        result = analytics_service.get_job_duration_trend(
            db_session, test_job.id, test_user.id
        )
        assert len(result) == 2
        assert result[0]["duration_seconds"] == 5.0  # oldest first
        assert result[1]["duration_seconds"] == 3.0

    def test_trend_not_found_job(self, db_session, test_user):
        result = analytics_service.get_job_duration_trend(
            db_session, "fake", test_user.id
        )
        assert result == []


class TestJobTimeline:
    def test_empty_timeline(self, db_session, test_user, test_job):
        result = analytics_service.get_job_timeline(
            db_session, test_job.id, test_user.id, days=3
        )
        assert len(result) == 4  # today + 3 days
        for entry in result:
            assert entry["success"] == 0

    def test_timeline_not_found_job(self, db_session, test_user):
        result = analytics_service.get_job_timeline(db_session, "fake", test_user.id)
        assert result == []
