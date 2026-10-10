"""
Welcome email: who gets one, duplicate protection and provider failures.

The database (public.welcome_emails) and every email provider are mocked;
no email is sent and nothing is written to Supabase.
"""

import io
import smtplib
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import httpx
from fastapi.testclient import TestClient

import main
from auth.supabase_auth import get_current_user
from services import welcome_email as we


def iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) + delta).isoformat()


def new_user(provider="email", email="new.user@example.com", confirmed=True, age=timedelta(minutes=2), name="Ada"):
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        email=email,
        created_at=iso(-age),
        email_confirmed_at=iso() if confirmed else None,
        app_metadata={"provider": provider, "providers": [provider]},
        user_metadata={"full_name": name} if name else {},
    )


class FakeStore:
    """In-memory public.welcome_emails with the same claim semantics."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def get(self, user_id):
        row = self.rows.get(user_id)
        return dict(row) if row else None

    def insert(self, user_id, status, reason=None):
        with self.lock:
            if user_id in self.rows:
                return False
            self.rows[user_id] = {"status": status, "attempts": 1 if status == "SENDING" else 0,
                                  "reason": reason, "updated_at": iso()}
            return True

    def claim_retry(self, user_id, attempts):
        with self.lock:
            row = self.rows.get(user_id)
            if not row or row["status"] != "FAILED" or row["attempts"] != attempts:
                return False
            row.update(status="SENDING", attempts=attempts + 1, updated_at=iso())
            return True

    def claim_skipped(self, user_id):
        with self.lock:
            row = self.rows.get(user_id)
            if not row or row["status"] != "SKIPPED" or row["reason"] != "not_configured":
                return False
            row.update(status="SENDING", attempts=1, reason=None, updated_at=iso())
            return True

    def finish(self, user_id, status, reason=None):
        self.rows[user_id].update(status=status, reason=reason, updated_at=iso())

    def age(self, user_id, delta):
        self.rows[user_id]["updated_at"] = iso(-delta)


class Base(unittest.TestCase):

    def setUp(self):
        we._settled.clear()
        self.store = FakeStore()
        self.sent = []
        self.patch(we, "store", self.store)
        self.patch(we, "table_available", lambda *a, **k: True)
        self.patch(we, "deliver", self.fake_deliver)
        self.patch(we, "provider_configured", lambda: True)
        self.fail_with = None

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_deliver(self, email):
        if self.fail_with:
            raise self.fail_with
        self.sent.append(email)


class WhoGetsOne(Base):

    def test_new_password_account(self):
        user = new_user(provider="email")
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].to, user.email)
        self.assertEqual(self.sent[0].subject, "Welcome to Cortexa AI")
        self.assertEqual(self.store.rows[user.id]["status"], "SENT")

    def test_new_email_code_account(self):
        # Email-code accounts have provider "email" and no password; the
        # trigger does not depend on how the user signed in.
        user = new_user(provider="email", name=None)
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertIn("Hi there,", self.sent[0].text)

    def test_new_google_account(self):
        user = new_user(provider="google")
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertEqual(len(self.sent), 1)

    def test_existing_user_logging_in_again_gets_nothing(self):
        user = new_user()
        self.store.rows[user.id] = {"status": "SKIPPED", "attempts": 0, "reason": "existing_account", "updated_at": iso()}
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(self.sent, [])

    def test_old_account_without_a_row_is_skipped(self):
        user = new_user(age=timedelta(days=30))
        self.assertEqual(we.send_welcome_if_new(user), "existing_account")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.rows[user.id]["status"], "SKIPPED")

    def test_unconfirmed_email_waits(self):
        user = new_user(confirmed=False)
        self.assertEqual(we.send_welcome_if_new(user), "unconfirmed")
        self.assertEqual(self.store.rows, {})
        user.email_confirmed_at = iso()
        self.assertEqual(we.send_welcome_if_new(user), "sent")

    def test_no_provider_configured_marks_skipped(self):
        self.patch(we, "provider_configured", lambda: False)
        user = new_user()
        self.assertEqual(we.send_welcome_if_new(user), "not_configured")
        self.assertEqual(self.store.rows[user.id]["status"], "SKIPPED")
        self.assertEqual(self.sent, [])

    def test_sent_once_provider_is_configured_later(self):
        configured = {"on": False}
        self.patch(we, "provider_configured", lambda: configured["on"])
        user = new_user(provider="google")
        self.assertEqual(we.send_welcome_if_new(user), "not_configured")
        self.assertEqual(self.sent, [])
        configured["on"] = True
        we._settled.clear()                       # provider settings need a restart
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertEqual(we.send_welcome_if_new(user), "settled")
        we._settled.clear()
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(len(self.sent), 1)

    def test_skipped_without_provider_expires_after_max_age(self):
        user = new_user(age=timedelta(days=8))
        self.store.rows[user.id] = {"status": "SKIPPED", "attempts": 0, "reason": "not_configured", "updated_at": iso()}
        self.assertEqual(we.send_welcome_if_new(user), "existing_account")
        self.assertEqual(self.sent, [])

    def test_existing_accounts_stay_skipped_when_a_provider_is_added(self):
        user = new_user(age=timedelta(minutes=1))
        self.store.rows[user.id] = {"status": "SKIPPED", "attempts": 0, "reason": "existing_account", "updated_at": iso()}
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(self.sent, [])

    def test_missing_table_sends_nothing(self):
        self.patch(we, "table_available", lambda *a, **k: False)
        self.assertEqual(we.send_welcome_if_new(new_user()), "no_table")
        self.assertEqual(self.sent, [])


class Duplicates(Base):

    def test_every_later_login_sends_nothing(self):
        user = new_user()
        we.send_welcome_if_new(user)
        for _ in range(3):
            self.assertEqual(we.send_welcome_if_new(user), "settled")
        we._settled.clear()                       # e.g. another server process
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(len(self.sent), 1)

    def test_concurrent_requests_send_once(self):
        user = new_user()
        gate = threading.Barrier(8)
        original_get = self.store.get

        def get_after_everyone_arrives(user_id):
            row = original_get(user_id)
            gate.wait(timeout=5)                  # all see "no row" first
            return row

        self.store.get = get_after_everyone_arrives
        results = []
        threads = [threading.Thread(target=lambda: results.append(we.send_welcome_if_new(user))) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(results.count("sent"), 1)
        self.assertEqual(results.count("claimed_elsewhere"), 7)

    def test_claim_in_progress_is_never_resent(self):
        user = new_user()
        self.store.rows[user.id] = {"status": "SENDING", "attempts": 1, "reason": None, "updated_at": iso(-timedelta(days=1))}
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(self.sent, [])

    def test_unknown_outcome_is_not_retried(self):
        user = new_user()
        self.fail_with = RuntimeError("connection dropped after DATA")
        self.assertEqual(we.send_welcome_if_new(user), "unknown")
        self.assertEqual(self.store.rows[user.id]["status"], "SENDING")
        self.fail_with = None
        we._settled.clear()
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(self.sent, [])

    def test_failed_record_update_does_not_cause_a_resend(self):
        user = new_user()
        self.store.finish = mock.Mock(side_effect=httpx.ConnectError("db down"))
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertEqual(self.store.rows[user.id]["status"], "SENDING")
        we._settled.clear()
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(len(self.sent), 1)


class ProviderFailures(Base):

    def test_failure_is_recorded_and_never_raises(self):
        user = new_user()
        self.fail_with = we.EmailError("smtp_unavailable")
        self.assertEqual(we.send_welcome_if_new(user), "failed")
        row = self.store.rows[user.id]
        self.assertEqual((row["status"], row["reason"], row["attempts"]), ("FAILED", "smtp_unavailable", 1))

    def test_retry_waits_then_sends_once(self):
        user = new_user()
        self.fail_with = we.EmailError("provider_unavailable")
        we.send_welcome_if_new(user)
        self.fail_with = None
        self.assertEqual(we.send_welcome_if_new(user), "retry_later")
        self.store.age(user.id, timedelta(minutes=11))
        self.assertEqual(we.send_welcome_if_new(user), "sent")
        self.assertEqual(we.send_welcome_if_new(user), "settled")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.store.rows[user.id]["attempts"], 2)

    def test_gives_up_after_max_attempts(self):
        user = new_user()
        self.fail_with = we.EmailError("provider_auth")
        for _ in range(we.MAX_ATTEMPTS):
            we.send_welcome_if_new(user)
            self.store.age(user.id, timedelta(hours=1))
        self.assertEqual(self.store.rows[user.id]["attempts"], we.MAX_ATTEMPTS)
        self.fail_with = None
        self.assertEqual(we.send_welcome_if_new(user), "already_handled")
        self.assertEqual(self.sent, [])

    def test_database_outage_sends_nothing(self):
        user = new_user()
        self.store.get = mock.Mock(side_effect=httpx.ConnectError("db down"))
        self.assertEqual(we.send_welcome_if_new(user), "store_error")
        self.assertEqual(self.sent, [])

    def test_logs_contain_no_address_or_content(self):
        user = new_user(email="private.person@example.com")
        self.fail_with = we.EmailError("smtp_auth")
        out = io.StringIO()
        with redirect_stdout(out):
            we.send_welcome_if_new(user)
            self.store.age(user.id, timedelta(hours=1))
            self.fail_with = None
            we.send_welcome_if_new(user)
        logged = out.getvalue()
        self.assertIn("WELCOME", logged)
        self.assertNotIn("private.person", logged)
        self.assertNotIn("Welcome to Cortexa AI", logged)


class Content(unittest.TestCase):

    def test_text_and_html_with_app_link(self):
        with mock.patch.multiple(we.settings, APP_PUBLIC_URL="https://chat.example.com", SUPPORT_EMAIL="", SUPPORT_URL=""):
            email = we.build_email(new_user(name="Ada"))
        self.assertIn("Hi Ada,", email.text)
        self.assertIn("https://chat.example.com", email.text)
        self.assertIn('href="https://chat.example.com"', email.html)
        self.assertIn("PDF", email.text)
        self.assertNotIn("Need help", email.text)
        self.assertNotIn("Need help", email.html)

    def test_support_link_only_when_configured(self):
        with mock.patch.multiple(we.settings, APP_PUBLIC_URL="", SUPPORT_EMAIL="help@example.com", SUPPORT_URL=""):
            email = we.build_email(new_user())
        self.assertIn("help@example.com", email.text)
        self.assertIn("mailto:help@example.com", email.html)
        self.assertNotIn("Start chatting", email.html)

    def test_user_name_is_escaped(self):
        email = we.build_email(new_user(name='<img src=x onerror="alert(1)">'))
        self.assertNotIn("<img src=x", email.html)
        self.assertIn("&lt;img", email.html)


class Providers(unittest.TestCase):

    def email(self):
        return we.Email(to="a@example.com", subject="S", text="T", html="<p>H</p>", idempotency_key="welcome-u1")

    def test_provider_configured_needs_provider_sender_and_credentials(self):
        cases = [
            (dict(WELCOME_EMAIL_PROVIDER="", EMAIL_FROM="x@example.com"), False),
            (dict(WELCOME_EMAIL_PROVIDER="smtp", EMAIL_FROM="", SMTP_HOST="smtp.example.com"), False),
            (dict(WELCOME_EMAIL_PROVIDER="smtp", EMAIL_FROM="x@example.com", SMTP_HOST=""), False),
            (dict(WELCOME_EMAIL_PROVIDER="smtp", EMAIL_FROM="x@example.com", SMTP_HOST="smtp.example.com"), True),
            (dict(WELCOME_EMAIL_PROVIDER="resend", EMAIL_FROM="x@example.com", RESEND_API_KEY=""), False),
            (dict(WELCOME_EMAIL_PROVIDER="resend", EMAIL_FROM="x@example.com", RESEND_API_KEY="k"), True),
            (dict(WELCOME_EMAIL_PROVIDER="brevo", EMAIL_FROM="x@example.com", BREVO_API_KEY="k"), True),
            (dict(WELCOME_EMAIL_PROVIDER="mailgun", EMAIL_FROM="x@example.com"), False),
        ]
        for values, expected in cases:
            with self.subTest(values=values), mock.patch.multiple(we.settings, **values):
                self.assertEqual(we.provider_configured(), expected)

    def test_smtp_uses_starttls_and_login(self):
        server = mock.MagicMock()
        server.__enter__.return_value = server
        with mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="smtp", SMTP_HOST="smtp.example.com", SMTP_PORT=587,
                                 SMTP_USERNAME="user", SMTP_PASSWORD="pw", EMAIL_FROM="App <x@example.com>"), \
                mock.patch.object(we.smtplib, "SMTP", return_value=server) as smtp:
            we.deliver(self.email())
        smtp.assert_called_once_with("smtp.example.com", 587, timeout=we.SEND_TIMEOUT_S)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user", "pw")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "a@example.com")
        self.assertTrue(message.is_multipart())

    def test_smtp_errors_become_codes(self):
        cases = [
            (smtplib.SMTPAuthenticationError(535, b"bad"), "smtp_auth"),
            (smtplib.SMTPRecipientsRefused({}), "recipient_refused"),
            (OSError("unreachable"), "smtp_unavailable"),
        ]
        for error, code in cases:
            with self.subTest(code=code), \
                    mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="smtp", SMTP_HOST="h", SMTP_PORT=587, SMTP_USERNAME=""), \
                    mock.patch.object(we.smtplib, "SMTP", side_effect=error):
                with self.assertRaises(we.EmailError) as ctx:
                    we.deliver(self.email())
                self.assertEqual(ctx.exception.code, code)

    def test_resend_sends_idempotency_key(self):
        response = httpx.Response(200, json={"id": "e1"})
        with mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="resend", RESEND_API_KEY="re_test", EMAIL_FROM="x@example.com"), \
                mock.patch.object(we.httpx, "post", return_value=response) as post:
            we.deliver(self.email())
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Idempotency-Key"], "welcome-u1")
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["a@example.com"])

    def test_brevo_payload(self):
        response = httpx.Response(201, json={"messageId": "m"})
        with mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="brevo", BREVO_API_KEY="k", EMAIL_FROM="App <x@example.com>"), \
                mock.patch.object(we.httpx, "post", return_value=response) as post:
            we.deliver(self.email())
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["sender"], {"email": "x@example.com", "name": "App"})
        self.assertEqual(payload["to"], [{"email": "a@example.com"}])

    def test_http_provider_errors_become_codes(self):
        cases = [(401, "provider_auth"), (429, "provider_rate_limited"), (503, "provider_unavailable"), (422, "provider_rejected_422")]
        for status, code in cases:
            with self.subTest(status=status), \
                    mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="resend", RESEND_API_KEY="k", EMAIL_FROM="x@example.com"), \
                    mock.patch.object(we.httpx, "post", return_value=httpx.Response(status, json={})):
                with self.assertRaises(we.EmailError) as ctx:
                    we.deliver(self.email())
                self.assertEqual(ctx.exception.code, code)
        with mock.patch.multiple(we.settings, WELCOME_EMAIL_PROVIDER="brevo", BREVO_API_KEY="k", EMAIL_FROM="x@example.com"), \
                mock.patch.object(we.httpx, "post", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(we.EmailError) as ctx:
                we.deliver(self.email())
            self.assertEqual(ctx.exception.code, "provider_unavailable")


class Trigger(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def test_auth_me_queues_the_welcome_email_for_the_verified_user(self):
        user = new_user()
        main.app.dependency_overrides[get_current_user] = lambda: user
        with mock.patch.object(main, "send_welcome_if_new") as send, \
                mock.patch.object(main, "is_admin", return_value=False):
            response = self.client.get("/auth/me")
        self.assertEqual(response.status_code, 200)
        send.assert_called_once_with(user)

    def test_unauthenticated_request_queues_nothing(self):
        with mock.patch.object(main, "send_welcome_if_new") as send:
            self.assertEqual(self.client.get("/auth/me").status_code, 401)
        send.assert_not_called()

    def test_config_shows_code_timing_but_no_email_secrets(self):
        with mock.patch.multiple(main, EMAIL_OTP_EXPIRY_S=600, EMAIL_OTP_RESEND_S=60):
            body = self.client.get("/config").json()
        self.assertEqual(body["email_otp"], {"expiry_seconds": 600, "resend_seconds": 60})
        text = str(body)
        for name in ("SMTP_PASSWORD", "RESEND_API_KEY", "BREVO_API_KEY"):
            value = getattr(we.settings, name)
            if value:
                self.assertNotIn(value, text)
        self.assertNotIn("smtp", text.lower())


if __name__ == "__main__":
    unittest.main()
