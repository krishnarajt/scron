"""
Tests for log broadcaster — pub/sub for real-time log streaming.
"""

import asyncio
import pytest

from app.services import log_broadcaster


@pytest.fixture(autouse=True)
def clean_channels():
    """Ensure channels are clean before/after each test."""
    with log_broadcaster._lock:
        log_broadcaster._channels.clear()
    yield
    with log_broadcaster._lock:
        log_broadcaster._channels.clear()


class TestChannelLifecycle:
    def test_create_channel(self):
        log_broadcaster.create_channel(1, "job-abc")
        channels = log_broadcaster.get_active_channels()
        assert len(channels) == 1
        assert channels[0]["execution_id"] == 1
        assert channels[0]["job_id"] == "job-abc"

    def test_publish_line(self):
        log_broadcaster.create_channel(1, "job-abc")
        log_broadcaster.publish_line(1, "hello")
        log_broadcaster.publish_line(1, "world")
        with log_broadcaster._lock:
            ch = log_broadcaster._channels[1]
            assert len(ch.buffer) == 2
            assert ch.buffer[0] == "hello"

    def test_publish_to_nonexistent_channel(self):
        # Should not raise
        log_broadcaster.publish_line(999, "hello")

    def test_close_channel(self):
        log_broadcaster.create_channel(1, "job-abc")
        log_broadcaster.close_channel(1)
        with log_broadcaster._lock:
            ch = log_broadcaster._channels.get(1)
            assert ch is not None  # Still exists briefly
            assert ch.finished is True

    def test_close_nonexistent_channel(self):
        # Should not raise
        log_broadcaster.close_channel(999)

    def test_buffer_max(self):
        log_broadcaster.create_channel(1, "job-abc")
        for i in range(600):
            log_broadcaster.publish_line(1, f"line {i}")
        with log_broadcaster._lock:
            ch = log_broadcaster._channels[1]
            assert len(ch.buffer) == 500  # capped


class TestGetChannelForJob:
    def test_find_active_channel(self):
        log_broadcaster.create_channel(10, "job-x")
        assert log_broadcaster.get_channel_for_job("job-x") == 10

    def test_finished_channel_not_returned(self):
        log_broadcaster.create_channel(10, "job-x")
        log_broadcaster.close_channel(10)
        assert log_broadcaster.get_channel_for_job("job-x") is None

    def test_nonexistent_job(self):
        assert log_broadcaster.get_channel_for_job("ghost") is None


class TestSubscription:
    @pytest.mark.asyncio
    async def test_subscribe_receives_buffered_lines(self):
        log_broadcaster.create_channel(1, "job-abc")
        log_broadcaster.publish_line(1, "line1")
        log_broadcaster.publish_line(1, "line2")

        queue = await log_broadcaster.subscribe(1)
        assert queue is not None
        # Should have buffered lines
        line = queue.get_nowait()
        assert line == "line1"
        line = queue.get_nowait()
        assert line == "line2"

    @pytest.mark.asyncio
    async def test_subscribe_to_nonexistent(self):
        queue = await log_broadcaster.subscribe(999)
        assert queue is None

    @pytest.mark.asyncio
    async def test_subscribe_to_finished_gets_sentinel(self):
        log_broadcaster.create_channel(1, "job-abc")
        log_broadcaster.publish_line(1, "data")
        log_broadcaster.close_channel(1)

        queue = await log_broadcaster.subscribe(1)
        assert queue is not None
        # Should get buffered data then sentinel
        assert queue.get_nowait() == "data"
        assert queue.get_nowait() is None

    def test_unsubscribe(self):
        log_broadcaster.create_channel(1, "job-abc")
        # Create a mock queue to unsubscribe
        q = asyncio.Queue()
        loop = asyncio.new_event_loop()
        with log_broadcaster._lock:
            log_broadcaster._channels[1].subscribers.append((q, loop))

        log_broadcaster.unsubscribe(1, q)
        with log_broadcaster._lock:
            assert len(log_broadcaster._channels[1].subscribers) == 0
        loop.close()

    def test_unsubscribe_nonexistent(self):
        q = asyncio.Queue()
        # Should not raise
        log_broadcaster.unsubscribe(999, q)


class TestActiveChannels:
    def test_get_active_channels_empty(self):
        assert log_broadcaster.get_active_channels() == []

    def test_get_active_channels_multiple(self):
        log_broadcaster.create_channel(1, "job-a")
        log_broadcaster.create_channel(2, "job-b")
        channels = log_broadcaster.get_active_channels()
        assert len(channels) == 2
        ids = {c["execution_id"] for c in channels}
        assert ids == {1, 2}
