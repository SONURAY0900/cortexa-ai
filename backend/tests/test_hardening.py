"""
Request-size limits, security headers and the worker pool size.
No network.
"""

import unittest
from unittest import mock

import anyio
from fastapi.testclient import TestClient

import main
from config import settings


class RequestSize(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def test_declared_oversized_body_is_refused_before_auth_or_reading(self):
        response = self.client.post("/upload/pdf", content=b"x",
                                    headers={"Content-Length": str(main.MAX_REQUEST_BYTES + 1)})
        self.assertEqual(response.status_code, 413)

    def test_body_without_a_declared_size_is_refused(self):
        def chunks():
            yield b"x" * 1024

        response = self.client.post("/upload/pdf", content=chunks())   # sent chunked
        self.assertEqual(response.status_code, 411)

    def test_normal_requests_pass(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        # Reaches the endpoint (and its login check) instead of the size guard.
        self.assertEqual(self.client.post("/upload/pdf", files={"file": ("a.pdf", b"%PDF-1.4")}).status_code, 401)


class Headers(unittest.TestCase):

    def test_security_headers_on_pages_and_api(self):
        client = TestClient(main.app)
        for path in ("/", "/health"):
            with self.subTest(path=path):
                headers = client.get(path).headers
                csp = headers["Content-Security-Policy"]
                self.assertIn("default-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("object-src 'none'", csp)
                script_src = next(part for part in csp.split("; ") if part.startswith("script-src"))
                self.assertNotIn("unsafe", script_src)                        # no inline scripts or eval
                self.assertIn("microphone=(self)", headers["Permissions-Policy"])
                self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_hsts_only_in_production(self):
        client = TestClient(main.app)
        with mock.patch.object(main, "IS_PRODUCTION", False):
            self.assertNotIn("Strict-Transport-Security", client.get("/health").headers)
        with mock.patch.object(main, "IS_PRODUCTION", True):
            self.assertIn("max-age=", client.get("/health").headers["Strict-Transport-Security"])

    def test_no_inline_scripts_in_pages(self):
        # The CSP has no 'unsafe-inline' for scripts, so an inline <script>
        # would silently stop working.
        for name in ("index.html", "about.html", "admin.html", "profile.html", "settings.html"):
            with self.subTest(page=name):
                html = (main.FRONTEND_DIR / name).read_text(encoding="utf-8")
                self.assertNotIn("<script>", html)


class WorkerPool(unittest.TestCase):

    def test_startup_raises_the_thread_pool(self):
        with TestClient(main.app) as client:
            tokens = client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().total_tokens)
        self.assertEqual(tokens, settings.WORKER_THREADS)


if __name__ == "__main__":
    unittest.main()
