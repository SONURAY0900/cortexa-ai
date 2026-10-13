"""
Attachments, file access, status vocabularies and profile tests.

    cd backend
    python -m unittest discover -s tests -v

Users are simulated with dependency overrides. Requests below are
rejected before any database write; database calls made are reads.
"""

import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

import main
from auth.supabase_auth import get_current_user, get_user_or_guest
from services.files import FileStatus, ImageStepStatus, image_statuses
from tests.test_security import make_user

# CHECK constraints of the current schema.
IMAGES_STEP_ALLOWED = {"PENDING", "SUCCESS", "FAILED"}
FILES_STATUS_ALLOWED = {"UPLOADED", "PROCESSING", "PROCESSED", "FAILED", "DELETED"}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def login(self, user=None):
        user = user or make_user()
        main.app.dependency_overrides[get_current_user] = lambda: user
        main.app.dependency_overrides[get_user_or_guest] = lambda: user
        return user


class StatusVocabularies(unittest.TestCase):

    def test_image_statuses_match_constraints(self):
        for ocr_ok in (True, False):
            for vision_ok in (True, False):
                s = image_statuses(ocr_ok, vision_ok)
                self.assertIn(s["ocr_status"], IMAGES_STEP_ALLOWED)
                self.assertIn(s["vision_status"], IMAGES_STEP_ALLOWED)
                self.assertIn(s["file_status"], FILES_STATUS_ALLOWED)
        self.assertEqual(image_statuses(True, True)["ocr_status"], "SUCCESS")
        self.assertEqual(image_statuses(False, True)["file_status"], "PROCESSED")
        self.assertEqual(image_statuses(False, False)["file_status"], "FAILED")

    def test_constants_never_use_audio_or_document_values(self):
        self.assertTrue({v for k, v in vars(ImageStepStatus).items() if k.isupper()} <= IMAGES_STEP_ALLOWED)
        self.assertTrue({v for k, v in vars(FileStatus).items() if k.isupper()} <= FILES_STATUS_ALLOWED)

    def test_record_image_analysis_writes_success_not_completed(self):
        writes = []

        class Query:
            def update(self, values):
                writes.append(values)
                return self

            def eq(self, *args):
                return self

            def execute(self):
                return mock.Mock(data=[])

        with mock.patch.object(main, "supabase") as db, mock.patch.object(main, "set_file_status") as file_status:
            db.table.return_value = Query()
            main._record_image_analysis("file-1", {"ocr_ok": True, "vision_ok": True, "ocr_text": "x", "vision_answer": "y"})

        self.assertEqual(writes[0]["ocr_status"], "SUCCESS")
        self.assertEqual(writes[0]["vision_status"], "SUCCESS")
        file_status.assert_called_once_with("file-1", "PROCESSED")


class FileAccess(Base):

    def test_file_content_requires_login(self):
        self.assertEqual(self.client.get(f"/files/{uuid.uuid4()}/content").status_code, 401)

    def test_foreign_or_unknown_file_is_404(self):
        self.login()
        self.assertEqual(self.client.get(f"/files/{uuid.uuid4()}/content").status_code, 404)
        self.assertEqual(self.client.delete(f"/files/{uuid.uuid4()}").status_code, 404)

    def test_file_id_must_be_uuid(self):
        self.login()
        for bad in ("..%2F..%2F.env", "not-a-uuid", "1"):
            with self.subTest(bad=bad):
                response = self.client.get(f"/files/{bad}/content")
                # 422 (not a UUID) or 404 (no route matches): never content.
                self.assertIn(response.status_code, (404, 422))
                self.assertNotIn("SUPABASE", response.text)


class ChatAttachments(Base):

    def test_unknown_attachment_is_rejected(self):
        self.login()
        response = self.client.post("/chat/stream", json={"query": "hi", "attachments": [str(uuid.uuid4())]})
        self.assertEqual(response.status_code, 404)

    def test_attachment_ids_must_be_uuids(self):
        self.login()
        response = self.client.post("/chat/stream", json={"query": "hi", "attachments": ["../../backend/.env"]})
        self.assertEqual(response.status_code, 422)

    def test_too_many_attachments(self):
        self.login()
        ids = [str(uuid.uuid4()) for _ in range(main.MAX_ATTACHMENTS + 1)]
        self.assertEqual(self.client.post("/chat/stream", json={"query": "hi", "attachments": ids}).status_code, 422)

    def test_unknown_context_file_is_rejected(self):
        self.login()
        response = self.client.post("/chat/stream", json={"query": "hi", "context_file_ids": [str(uuid.uuid4())]})
        self.assertEqual(response.status_code, 404)

    def test_context_pdf_is_not_shown_as_attachment(self):
        # A follow-up that only used a PDF as context must not render a card.
        messages = [
            {"role": "USER", "metadata": {"attachments": [], "context_file_ids": ["x"], "document_id": "d"}},
            {"role": "USER", "metadata": {"context_file_ids": ["x"], "document_id": "d"}},
        ]
        with mock.patch.object(main, "owned_files", side_effect=AssertionError("no lookup expected")):
            main._client_message_metadata(messages, make_user())
        self.assertEqual(messages[0]["metadata"]["attachments"], [])
        self.assertNotIn("attachments", messages[1]["metadata"])

    def test_message_metadata_always_has_attachments_key(self):
        plan = main.AttachmentPlan(documents=[{"id": "doc-1", "filename": "a.pdf"}])
        request = main.ChatRequest(query="q", context_file_ids=[uuid.uuid4()])
        metadata = main._message_metadata(plan, request)
        self.assertEqual(metadata["attachments"], [])
        self.assertEqual(metadata["document_id"], "doc-1")

    def test_metadata_never_returns_server_paths(self):
        messages = [{"role": "USER", "metadata": {"image_path": "D:/secret/backend/uploads/images/x.png"}}]
        main._client_message_metadata(messages, make_user())
        self.assertNotIn("image_path", messages[0]["metadata"])


class Profile(Base):

    def test_profile_requires_login(self):
        self.assertEqual(self.client.get("/profile/me").status_code, 401)
        self.assertEqual(self.client.patch("/profile/me", json={"full_name": "A"}).status_code, 401)

    def test_role_cannot_be_submitted(self):
        self.login()
        response = self.client.patch("/profile/me", json={"full_name": "A", "role": "ADMIN"})
        self.assertEqual(response.status_code, 422)

    def test_name_validation(self):
        self.login()
        for name in ("", "   ", "x" * 81):
            with self.subTest(name=name):
                self.assertEqual(self.client.patch("/profile/me", json={"full_name": name}).status_code, 422)

    def test_archive_all_requires_login(self):
        self.assertEqual(self.client.post("/conversations/archive-all").status_code, 401)


class Pages(Base):

    def test_static_pages_served(self):
        for path in ("/", "/profile", "/settings", "/admin"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
