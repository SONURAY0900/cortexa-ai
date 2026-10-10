"""
Token verification and admin authorization.

Supabase is mocked: these tests check how the API classifies each kind of
failure, not Supabase itself.
"""

import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError, AuthRetryableError

import main
from auth import supabase_auth
from config import settings
from tests.test_security import make_user


def user_response(user):
    return SimpleNamespace(user=user)


class TokenVerification(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.headers = {"Authorization": "Bearer token-under-test"}
        self.admin_email = (settings.ADMIN_EMAILS or ["admin@example.com"])[0]
        sleep = mock.patch.object(supabase_auth.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def patch_get_user(self, **kwargs):
        patcher = mock.patch.object(supabase_auth.supabase.auth, "get_user", **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def test_valid_token(self):
        self.patch_get_user(return_value=user_response(make_user()))
        self.assertEqual(self.client.get("/auth/me", headers=self.headers).status_code, 200)

    def test_rejected_token_is_401(self):
        self.patch_get_user(side_effect=AuthApiError("invalid JWT", 403, "bad_jwt"))
        response = self.client.get("/auth/me", headers=self.headers)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], supabase_auth.SESSION_EXPIRED)

    def test_transient_error_is_retried_then_succeeds(self):
        mocked = self.patch_get_user(side_effect=[
            httpx.ReadError("[WinError 10035] A non-blocking socket operation could not be completed immediately"),
            user_response(make_user()),
        ])
        self.assertEqual(self.client.get("/auth/me", headers=self.headers).status_code, 200)
        self.assertEqual(mocked.call_count, 2)

    def test_persistent_transport_error_is_503_not_401(self):
        mocked = self.patch_get_user(side_effect=httpx.ConnectError("unreachable"))
        response = self.client.get("/auth/me", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers.get("retry-after"), "2")
        self.assertEqual(mocked.call_count, supabase_auth.VERIFY_ATTEMPTS)

    def test_supabase_5xx_is_503(self):
        self.patch_get_user(side_effect=AuthRetryableError("bad gateway", 502))
        self.assertEqual(self.client.get("/auth/me", headers=self.headers).status_code, 503)

    def test_supabase_500_or_rate_limit_is_503_not_401(self):
        for status in (500, 429):
            with self.subTest(status=status):
                mocked = self.patch_get_user(side_effect=AuthApiError("server trouble", status, None))
                response = self.client.get("/auth/me", headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(mocked.call_count, supabase_auth.VERIFY_ATTEMPTS)

    def test_auth_me_reports_unknown_role_as_null(self):
        self.patch_get_user(return_value=user_response(make_user(email=self.admin_email if settings.ADMIN_EMAILS else "a@example.com")))
        with mock.patch.object(supabase_auth, "_profile_role", side_effect=supabase_auth.RoleUnavailable()):
            response = self.client.get("/auth/me", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        if settings.ADMIN_EMAILS:
            self.assertIsNone(response.json()["user"]["is_admin"])

    def test_errors_never_leak_details(self):
        self.patch_get_user(side_effect=httpx.ReadError("secret socket detail"))
        self.assertNotIn("secret socket detail", self.client.get("/auth/me", headers=self.headers).text)

    def test_missing_or_malformed_header(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": "Bearer "}).status_code, 401)


class AdminRole(unittest.TestCase):

    def setUp(self):
        self.admin_email = (settings.ADMIN_EMAILS or ["admin@example.com"])[0]

    def test_app_metadata_role_alone_is_not_admin(self):
        if not settings.ADMIN_EMAILS:
            self.skipTest("ADMIN_EMAIL not configured")
        with mock.patch.object(supabase_auth, "_profile_role", return_value="USER"):
            self.assertFalse(supabase_auth.is_admin(make_user(email=self.admin_email, role="admin")))

    def test_normal_user_never_reads_profiles(self):
        with mock.patch.object(supabase_auth, "_profile_role", side_effect=AssertionError("not needed")):
            self.assertFalse(supabase_auth.is_admin(make_user()))

    def test_profile_read_failure_raises_unavailable(self):
        with mock.patch.object(supabase_auth.supabase, "table", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(supabase_auth.RoleUnavailable):
                supabase_auth._profile_role(str(uuid.uuid4()))

    def test_profiles_role(self):
        if not settings.ADMIN_EMAILS:
            self.skipTest("ADMIN_EMAIL not configured")
        with mock.patch.object(supabase_auth, "_profile_role", return_value="ADMIN"):
            self.assertTrue(supabase_auth.is_admin(make_user(email=self.admin_email)))
        with mock.patch.object(supabase_auth, "_profile_role", return_value="USER"):
            self.assertFalse(supabase_auth.is_admin(make_user(email=self.admin_email)))

    def test_role_without_allowlisted_email(self):
        with mock.patch.object(supabase_auth, "_profile_role", return_value="ADMIN"):
            self.assertFalse(supabase_auth.is_admin(make_user(email=f"{uuid.uuid4().hex}@example.com", role="admin")))

    def test_unconfirmed_admin_email(self):
        with mock.patch.object(supabase_auth, "_profile_role", return_value="ADMIN"):
            self.assertFalse(supabase_auth.is_admin(make_user(email=self.admin_email, role="admin", confirmed=False)))

    def test_admin_email_is_a_real_account(self):
        """ADMIN_EMAIL must belong to an existing, confirmed Supabase user (read-only check)."""
        if not settings.ADMIN_EMAILS:
            self.skipTest("ADMIN_EMAIL not configured")
        emails = {
            (u.email or "").lower(): u
            for u in supabase_auth.supabase.auth.admin.list_users(page=1, per_page=200)
        }
        for email in settings.ADMIN_EMAILS:
            with self.subTest(email="(configured admin)"):
                self.assertIn(email, emails)
                self.assertTrue(emails[email].email_confirmed_at)


if __name__ == "__main__":
    unittest.main()
