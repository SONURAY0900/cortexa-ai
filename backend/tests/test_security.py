"""
Security / authorization tests.

    cd backend
    python -m unittest discover -s tests -v

Authenticated users are simulated with FastAPI dependency overrides,
so no real credentials are needed. Every request below is rejected
before any database write; the few database calls made are reads.
"""

import io
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import main
from auth import supabase_auth
from auth.supabase_auth import get_current_user, is_admin
from config import settings
from services.storage import UPLOAD_ROOT, resolve_inside_uploads


ADMIN_EMAIL = (settings.ADMIN_EMAILS or ["admin@example.com"])[0]


def make_user(email="user@example.com", role=None, confirmed=True):
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        email=email,
        created_at="2026-01-01T00:00:00Z",
        email_confirmed_at="2026-01-01T00:00:00Z" if confirmed else None,
        app_metadata={"role": role} if role else {},
    )


ADMIN_ENDPOINTS = [
    ("GET", "/admin/me"),
    ("GET", "/admin/dashboard"),
    ("GET", "/admin/users"),
    ("GET", f"/admin/users/{uuid.uuid4()}"),
    ("DELETE", f"/admin/users/{uuid.uuid4()}"),
    ("GET", "/admin/activity"),
    ("GET", "/admin/searches"),
    ("GET", "/admin/chat-activity"),
    ("DELETE", f"/admin/activity/{uuid.uuid4()}"),
    ("GET", "/admin/audit-log"),
    ("GET", "/admin/documents"),
    ("DELETE", f"/admin/documents/{uuid.uuid4()}"),
    ("GET", "/admin/images"),
    ("DELETE", f"/admin/images/{uuid.uuid4()}"),
    ("GET", "/admin/system-health"),
    ("GET", "/admin/settings"),
]


class Base(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def login_as(self, user):
        main.app.dependency_overrides[get_current_user] = lambda: user

    def call(self, method, path, **kwargs):
        if method == "DELETE" and path.startswith("/admin/users/"):
            kwargs.setdefault("json", {"confirm_email": "x@example.com"})
        return self.client.request(method, path, **kwargs)


class PublicEndpoints(Base):

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})

    def test_config_exposes_no_secret(self):
        body = self.client.get("/config").text
        self.assertNotIn(settings.SUPABASE_KEY, body)
        self.assertNotIn("sb_secret_", body)
        self.assertNotIn(settings.GROQ_API_KEY, body)

    def test_security_headers(self):
        headers = self.client.get("/health").headers
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")


class Unauthenticated(Base):

    def test_protected_endpoints_require_login(self):
        protected = [
            ("POST", "/upload/image"),
            ("POST", "/upload/pdf"),
            ("POST", "/voice/transcribe"),
            ("POST", "/chat/stream"),
            ("POST", "/chat"),
            ("GET", "/conversations"),
            ("GET", "/auth/me"),
            *ADMIN_ENDPOINTS,
        ]
        for method, path in protected:
            with self.subTest(path=path, method=method):
                response = self.call(method, path)
                self.assertIn(response.status_code, (401, 422), response.text)
                if response.status_code == 422:
                    # body validation ran first; still must not succeed
                    self.assertNotIn("success", response.json())

    def test_invalid_token_message_is_generic(self):
        response = self.client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Your session has expired. Please log in again.")


class AdminAuthorization(Base):

    def test_normal_user_gets_403_on_every_admin_endpoint(self):
        self.login_as(make_user())
        for method, path in ADMIN_ENDPOINTS:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.call(method, path).status_code, 403)

    def test_admin_email_without_role_is_not_admin(self):
        self.profile_role("USER")
        self.login_as(make_user(email=ADMIN_EMAIL))
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def profile_role(self, role):
        patcher = mock.patch.object(supabase_auth, "_profile_role", return_value=role)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_role_without_allowlisted_email_is_not_admin(self):
        self.profile_role("ADMIN")
        self.login_as(make_user(email="attacker@example.com"))
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def test_unconfirmed_admin_is_not_admin(self):
        self.profile_role("ADMIN")
        self.login_as(make_user(email=ADMIN_EMAIL, confirmed=False))
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def test_legacy_app_metadata_role_is_ignored(self):
        self.profile_role("USER")
        self.login_as(make_user(email=ADMIN_EMAIL, role="admin"))
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def test_missing_profile_is_not_admin(self):
        self.profile_role(None)
        self.login_as(make_user(email=ADMIN_EMAIL))
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def test_role_lookup_outage_is_503_not_403(self):
        if not settings.ADMIN_EMAILS:
            self.skipTest("ADMIN_EMAIL not configured")
        patcher = mock.patch.object(supabase_auth, "_profile_role", side_effect=supabase_auth.RoleUnavailable())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.login_as(make_user(email=ADMIN_EMAIL))
        self.assertEqual(self.client.get("/admin/me").status_code, 503)
        # A normal user is refused without ever needing the role lookup.
        self.login_as(make_user())
        self.assertEqual(self.client.get("/admin/me").status_code, 403)

    def test_real_admin_is_accepted(self):
        if not settings.ADMIN_EMAILS:
            self.skipTest("ADMIN_EMAIL not configured")
        self.profile_role("ADMIN")
        self.login_as(make_user(email=ADMIN_EMAIL.upper()))
        self.assertEqual(self.client.get("/admin/me").status_code, 200)

    def test_is_admin_rules(self):
        self.profile_role(None)                                     # no profile row
        self.assertFalse(is_admin(make_user()))
        self.assertFalse(is_admin(make_user(email=ADMIN_EMAIL)))
        if settings.ADMIN_EMAILS:
            self.assertTrue(is_admin(make_user(email=ADMIN_EMAIL), role="ADMIN"))
            self.assertFalse(is_admin(make_user(email=ADMIN_EMAIL), role="USER"))


class UploadValidation(Base):

    def setUp(self):
        super().setUp()
        self.login_as(make_user())

    def upload(self, path, name, content):
        return self.client.post(path, files={"file": (name, io.BytesIO(content))})

    def test_pdf_wrong_extension(self):
        self.assertEqual(self.upload("/upload/pdf", "notes.txt", b"%PDF-1.4").status_code, 400)

    def test_pdf_without_pdf_signature(self):
        self.assertEqual(self.upload("/upload/pdf", "fake.pdf", b"MZ\x90\x00 not a pdf").status_code, 400)

    def test_pdf_too_large(self):
        original = main.MAX_PDF_MB
        main.MAX_PDF_MB = 1
        try:
            response = self.upload("/upload/pdf", "big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 10))
        finally:
            main.MAX_PDF_MB = original
        self.assertEqual(response.status_code, 413)

    def test_image_with_fake_bytes(self):
        self.assertEqual(self.upload("/upload/image", "photo.png", b"<?php echo 1; ?>").status_code, 400)

    def test_image_wrong_extension(self):
        self.assertEqual(self.upload("/upload/image", "shell.svg", b"<svg/>").status_code, 400)

    def test_audio_wrong_extension(self):
        self.assertEqual(self.upload("/voice/transcribe", "a.exe", b"MZ").status_code, 400)


class ImageAccess(Base):

    def test_client_paths_are_never_resolved(self):
        for path in ["../.env", str(UPLOAD_ROOT.parent / ".env"), "C:/Windows/win.ini", "/etc/passwd", ""]:
            with self.subTest(path=path):
                self.assertIsNone(resolve_inside_uploads(path))

    def test_image_path_field_is_ignored_and_foreign_image_rejected(self):
        self.login_as(make_user())
        response = self.client.post("/chat/stream", json={
            "query": "read this",
            "image_id": str(uuid.uuid4()),          # not owned by this user
            "image_path": "../.env",                # legacy field: ignored
        })
        self.assertEqual(response.status_code, 404)

    def test_foreign_document_rejected(self):
        self.login_as(make_user())
        response = self.client.post("/chat/stream", json={"query": "hi", "document_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)


class ErrorLeakage(Base):

    def test_validation_errors_are_generic(self):
        self.login_as(make_user())
        response = self.client.post("/chat/stream", json={"query": ""})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"detail": "The request is invalid. Please check the input and try again."})

    def test_unhandled_errors_are_generic(self):
        @main.app.get("/__test_boom__")
        def boom():
            raise RuntimeError("secret internal detail")

        response = self.client.get("/__test_boom__")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret internal detail", response.text)


if __name__ == "__main__":
    unittest.main()
