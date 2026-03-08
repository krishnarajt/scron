"""
Tests for scheduler service — cron parsing, log output building,
script materialisation.
"""

import os
import pytest
from unittest.mock import patch

from app.services.scheduler_service import (
    _parse_cron,
    _build_log_output,
    _materialise_script,
)


class TestParseCron:
    def test_valid_5_field(self):
        trigger = _parse_cron("*/5 * * * *")
        assert trigger is not None

    def test_valid_specific(self):
        trigger = _parse_cron("0 9 * * 1-5")
        assert trigger is not None

    def test_too_few_fields(self):
        with pytest.raises(ValueError, match="5 fields"):
            _parse_cron("* * *")

    def test_too_many_fields(self):
        with pytest.raises(ValueError, match="5 fields"):
            _parse_cron("* * * * * *")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            _parse_cron("")

    def test_whitespace_stripped(self):
        trigger = _parse_cron("  */5 * * * *  ")
        assert trigger is not None


class TestBuildLogOutput:
    def test_empty_input(self):
        assert _build_log_output("") == ""
        assert _build_log_output("   ") == ""

    def test_short_output_kept_intact(self):
        lines = "\n".join(f"line {i}" for i in range(10))
        result = _build_log_output(lines)
        assert result == lines

    def test_long_output_truncated(self):
        lines = "\n".join(f"line {i}" for i in range(200))
        result = _build_log_output(lines)
        assert "lines omitted" in result
        assert "line 0" in result  # head preserved
        assert "line 199" in result  # tail preserved

    def test_single_line(self):
        assert _build_log_output("hello") == "hello"

    def test_exactly_at_threshold(self):
        # 50 head + 50 tail = 100 lines should NOT truncate
        lines = "\n".join(f"line {i}" for i in range(100))
        result = _build_log_output(lines)
        assert "omitted" not in result

    def test_just_over_threshold(self):
        lines = "\n".join(f"line {i}" for i in range(101))
        result = _build_log_output(lines)
        assert "1 lines omitted" in result


class TestMaterialiseScript:
    def test_python_script(self, tmp_path):
        with patch("app.services.scheduler_service._scripts_dir", str(tmp_path)):
            path = _materialise_script("job-123", "print('hi')", "python")
            assert path.endswith(".py")
            assert os.path.exists(path)
            with open(path) as f:
                assert f.read() == "print('hi')"

    def test_bash_script_executable(self, tmp_path):
        with patch("app.services.scheduler_service._scripts_dir", str(tmp_path)):
            path = _materialise_script("job-456", "#!/bin/bash\necho hi", "bash")
            assert path.endswith(".sh")
            assert os.path.exists(path)
            # Check executable bit
            mode = os.stat(path).st_mode
            assert mode & 0o100  # owner execute

    def test_overwrites_existing(self, tmp_path):
        with patch("app.services.scheduler_service._scripts_dir", str(tmp_path)):
            _materialise_script("job-789", "v1", "python")
            _materialise_script("job-789", "v2", "python")
            path = os.path.join(str(tmp_path), "job-789.py")
            with open(path) as f:
                assert f.read() == "v2"

    def test_unicode_content(self, tmp_path):
        with patch("app.services.scheduler_service._scripts_dir", str(tmp_path)):
            path = _materialise_script("job-uni", "print('héllo 日本語')", "python")
            with open(path, encoding="utf-8") as f:
                assert "héllo 日本語" in f.read()
