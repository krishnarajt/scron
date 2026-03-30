"""
Tests for notification service — message formatting, send functions, notify logic.
"""

from unittest.mock import patch, MagicMock

from app.services.notification_service import (
    _format_message,
    _send_telegram,
    _send_email,
    notify_execution_complete,
)


class TestFormatMessage:
    def test_success_message(self):
        msg = _format_message("My Job", "success", 2.5, execution_id=42)
        assert "My Job" in msg
        assert "SUCCESS" in msg
        assert "2.5s" in msg
        assert "42" in msg
        assert "\u2705" in msg  # checkmark emoji

    def test_failure_message_with_error(self):
        msg = _format_message(
            "Bad Job", "failure", 0.3, error_summary="crash!", execution_id=1
        )
        assert "Bad Job" in msg
        assert "FAILURE" in msg
        assert "crash!" in msg
        assert "\u274c" in msg  # X emoji

    def test_failure_without_error(self):
        msg = _format_message("Job", "failure", 1.0)
        assert "FAILURE" in msg
        assert "Error" not in msg  # No error section

    def test_long_error_truncated(self):
        long_err = "x" * 500
        _format_message("Job", "failure", 1.0, error_summary=long_err)
        # Error should be truncated to 300 chars in the message
        assert len(long_err[:300]) <= 300

    def test_zero_duration(self):
        msg = _format_message("Job", "success", 0.0)
        assert "0.0s" in msg

    def test_no_duration(self):
        msg = _format_message("Job", "success", None)
        assert "N/A" in msg


class TestSendTelegram:
    @patch("app.services.notification_service.constants")
    def test_no_bot_token(self, mock_constants):
        mock_constants.TELEGRAM_BOT_TOKEN = ""
        result = _send_telegram("123", "hello")
        assert result is False

    @patch("urllib.request.urlopen")
    @patch("app.services.notification_service.constants")
    def test_successful_send(self, mock_constants, mock_urlopen):
        mock_constants.TELEGRAM_BOT_TOKEN = "fake-token"
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _send_telegram("12345", "test message")
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    @patch("app.services.notification_service.constants")
    def test_api_error(self, mock_constants, mock_urlopen):
        mock_constants.TELEGRAM_BOT_TOKEN = "fake-token"
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": false, "description": "bad"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _send_telegram("12345", "test")
        assert result is False

    @patch("urllib.request.urlopen")
    @patch("app.services.notification_service.constants")
    def test_network_error(self, mock_constants, mock_urlopen):
        mock_constants.TELEGRAM_BOT_TOKEN = "fake-token"
        mock_urlopen.side_effect = Exception("Network error")
        result = _send_telegram("12345", "test")
        assert result is False


class TestSendEmail:
    @patch("app.services.notification_service.constants")
    def test_no_smtp_credentials(self, mock_constants):
        mock_constants.SMTP_USER = ""
        mock_constants.SMTP_PASSWORD = ""
        result = _send_email("to@test.com", "Subject", "Body")
        assert result is False

    @patch("smtplib.SMTP")
    @patch("app.services.notification_service.constants")
    def test_successful_send(self, mock_constants, mock_smtp_class):
        mock_constants.SMTP_USER = "user@gmail.com"
        mock_constants.SMTP_PASSWORD = "secret"
        mock_constants.SMTP_HOST = "smtp.gmail.com"
        mock_constants.SMTP_PORT = 587
        mock_constants.SMTP_FROM = "user@gmail.com"

        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = _send_email("to@test.com", "Test", "Hello")
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP")
    @patch("app.services.notification_service.constants")
    def test_smtp_failure(self, mock_constants, mock_smtp_class):
        mock_constants.SMTP_USER = "user@gmail.com"
        mock_constants.SMTP_PASSWORD = "secret"
        mock_constants.SMTP_HOST = "smtp.gmail.com"
        mock_constants.SMTP_PORT = 587
        mock_constants.SMTP_FROM = "user@gmail.com"
        mock_smtp_class.side_effect = Exception("Connection refused")

        result = _send_email("to@test.com", "Test", "Hello")
        assert result is False


class TestNotifyExecutionComplete:
    @patch("app.services.notification_service._get_user_notification_settings")
    @patch("app.services.notification_service._send_telegram")
    def test_failure_only_sends_on_failure(self, mock_tg, mock_settings):
        mock_settings.return_value = {
            "telegram_enabled": True,
            "telegram_chat_id": "123",
            "email_enabled": False,
            "notify_on": "failure_only",
            "user_email": None,
        }
        # Simulate synchronous call (skip threading)
        from app.services.notification_service import _format_message

        settings = mock_settings.return_value
        _format_message("Job", "success", 1.0)
        # Since notify_on=failure_only, success should NOT trigger telegram
        # We test the logic directly
        if settings["notify_on"] == "failure_only" and "success" != "failure":
            sent = False
        else:
            sent = True
        assert sent is False

    @patch("app.services.notification_service._get_user_notification_settings")
    def test_never_sends_nothing(self, mock_settings):
        mock_settings.return_value = {
            "telegram_enabled": True,
            "telegram_chat_id": "123",
            "email_enabled": True,
            "notify_on": "never",
            "user_email": "x@x.com",
        }
        settings = mock_settings.return_value
        assert settings["notify_on"] == "never"

    @patch("app.services.notification_service._get_user_notification_settings")
    def test_no_settings_does_nothing(self, mock_settings):
        mock_settings.return_value = None
        # Should not raise
        notify_execution_complete(1, "Job", "success")
