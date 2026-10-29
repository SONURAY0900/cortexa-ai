"""
Large PDFs (batched indexing, page and OCR caps, one ingestion at a time)
and per-user hourly rate limits. Supabase, embeddings and OCR are mocked.
"""

import os
import tempfile
import threading
import unittest
from unittest import mock

import pymupdf
from fastapi.testclient import TestClient

import main
from auth.supabase_auth import get_current_user, get_user_or_guest
from config import settings
from rag import pipeline
from rag.ingestion import pdf_loader
from rag.ingestion.ocr_processor import OcrResult
from services import guest_limits
from tests.test_security import make_user


def temp_file(suffix=".pdf") -> str:
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    return path


class BatchedIndexing(unittest.TestCase):

    def setUp(self):
        self.path = temp_file()
        self.addCleanup(os.remove, self.path)
        self.addCleanup(mock.patch.stopall)
        self.stored, self.embedded, self.deleted, self.stages = [], [], [], []
        mock.patch.object(pipeline, "_page_count", return_value=200).start()
        mock.patch.object(pipeline, "process_document", return_value=[
            {"page_number": n, "text": f"page {n}", "source": "text"} for n in range(1, 201)
        ]).start()
        # 200 pages x 2 chunks = 400 chunks
        mock.patch.object(pipeline, "split_text", side_effect=lambda text: [f"{text} a", f"{text} b"]).start()
        mock.patch.object(pipeline, "create_document", return_value="doc-1").start()
        mock.patch.object(pipeline, "delete_document", side_effect=self.deleted.append).start()
        mock.patch.object(pipeline, "generate_embeddings",
                          side_effect=lambda texts: self.embedded.append(len(texts)) or [[0.0] * 384 for _ in texts]).start()
        self.store = mock.patch.object(pipeline, "store_chunks",
                                       side_effect=lambda document_id, chunks: self.stored.append(chunks)).start()
        mock.patch.object(pipeline, "supabase").start()

    def ingest(self):
        return pipeline.ingest_pdf_with_summary(self.path, "big.pdf", progress=lambda s, d=None: self.stages.append(s))

    def test_every_page_is_indexed_in_small_batches(self):
        document_id, summary = self.ingest()

        self.assertEqual(document_id, "doc-1")
        self.assertEqual(summary["chunks"], 400)
        self.assertEqual(summary["pages_indexed"], list(range(1, 201)))
        self.assertEqual(len(self.stored), 7)                                # ceil(400 / 64)
        self.assertTrue(all(len(batch) <= pipeline.BATCH_CHUNKS for batch in self.stored))
        self.assertEqual(max(self.embedded), pipeline.BATCH_CHUNKS)          # never all 400 at once
        self.assertEqual([c["chunk_index"] for batch in self.stored for c in batch], list(range(400)))
        self.assertTrue(all("embedding" in c for batch in self.stored for c in batch))

    def test_a_failed_batch_removes_the_whole_document(self):
        calls = []

        def store(document_id, chunks):
            calls.append(chunks)
            if len(calls) == 3:
                raise RuntimeError("db down")

        self.store.side_effect = store
        with self.assertRaises(pipeline.IngestionError) as caught:
            self.ingest()
        self.assertEqual(caught.exception.reason, "indexing_failed")
        self.assertEqual(self.deleted, ["doc-1"])

    def test_too_many_pages_is_rejected_before_any_work(self):
        with mock.patch.object(pipeline, "_page_count", return_value=settings.MAX_PDF_PAGES + 1):
            with self.assertRaises(pipeline.IngestionError) as caught:
                self.ingest()
        self.assertEqual(caught.exception.reason, "too_many_pages")
        self.assertEqual(caught.exception.summary["max_pages"], settings.MAX_PDF_PAGES)
        pipeline.process_document.assert_not_called()

    def test_a_second_pdf_waits_for_the_first(self):
        self.assertTrue(pipeline._ingest_slots.acquire(blocking=False))      # another PDF is running
        worker = threading.Thread(target=self.ingest)
        worker.start()
        worker.join(timeout=0.5)
        self.assertTrue(worker.is_alive())                                   # waiting, not failing
        self.assertEqual(self.stages, ["queued"])
        pipeline._ingest_slots.release()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertIn("indexing", self.stages)

    def test_the_slot_is_released_after_a_failure(self):
        with mock.patch.object(pipeline, "process_document", side_effect=ValueError("broken")):
            with self.assertRaises(pipeline.IngestionError):
                self.ingest()
        self.assertTrue(pipeline._ingest_slots.acquire(blocking=False))
        pipeline._ingest_slots.release()


class OcrLimit(unittest.TestCase):

    def test_scanned_pages_over_the_limit_are_not_sent(self):
        path = temp_file()
        self.addCleanup(os.remove, path)
        doc = pymupdf.open()
        for _ in range(4):
            doc.new_page()                     # no text layer: every page needs OCR
        doc.save(path)
        doc.close()

        ocr = mock.Mock(return_value=OcrResult(text="Recognised words on the page.", status="success", attempts=1))
        with mock.patch.object(settings, "MAX_OCR_PAGES", 2), mock.patch.object(pdf_loader, "ocr_pdf_page", ocr):
            pages = pdf_loader.extract_text_from_pdf(path)

        self.assertEqual(ocr.call_count, 2)
        self.assertEqual([p["ocr_status"] for p in pages], ["success", "success", "failed", "failed"])
        self.assertEqual([p["ocr_error"] for p in pages[2:]], ["OCR_LIMIT", "OCR_LIMIT"])
        summary = pipeline.build_summary(pages)
        self.assertEqual(summary["pages_over_ocr_limit"], [3, 4])
        self.assertEqual(summary["failed_pages"], [3, 4])

    def test_result_message_is_short_and_explains_the_ocr_limit(self):
        message = main._pdf_result_message({"partial": True, "failed_pages": list(range(51, 81)),
                                            "pages_over_ocr_limit": list(range(51, 81))})
        self.assertIn("51, 52, 53, 54, 55, 56, 57, 58, 59, 60 and 20 more", message)
        self.assertIn(f"first {settings.MAX_OCR_PAGES} scanned pages", message)


class UserRateLimits(unittest.TestCase):

    def setUp(self):
        guest_limits.reset()
        self.addCleanup(guest_limits.reset)

    def test_limit_window_and_retry_after(self):
        with mock.patch.object(settings, "PDF_UPLOADS_PER_HOUR", 2):
            self.assertEqual(guest_limits.allow_user("pdf", "u1", now=0), 0)
            self.assertEqual(guest_limits.allow_user("pdf", "u1", now=100), 0)
            self.assertEqual(guest_limits.allow_user("pdf", "u1", now=200), 3400)   # oldest leaves at 3600
            self.assertEqual(guest_limits.allow_user("pdf", "u2", now=200), 0)      # per account
            self.assertEqual(guest_limits.allow_user("image", "u1", now=200), 0)    # per action
            self.assertEqual(guest_limits.allow_user("pdf", "u1", now=3600), 0)     # window moved on

    def test_zero_means_no_limit(self):
        with mock.patch.object(settings, "CHAT_MESSAGES_PER_HOUR", 0):
            self.assertTrue(all(guest_limits.allow_user("chat", "u1", now=i) == 0 for i in range(500)))


class RateLimitedEndpoints(unittest.TestCase):

    def setUp(self):
        guest_limits.reset()
        self.addCleanup(guest_limits.reset)
        self.user = make_user()
        main.app.dependency_overrides[get_current_user] = lambda: self.user
        main.app.dependency_overrides[get_user_or_guest] = lambda: self.user
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def exhaust(self, action, setting):
        patcher = mock.patch.object(settings, setting, 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(guest_limits.allow_user(action, str(self.user.id)), 0)

    def assert_limited(self, response, word):
        self.assertEqual(response.status_code, 429)
        self.assertIn(word, response.json()["detail"])
        self.assertGreater(int(response.headers["Retry-After"]), 0)

    def test_signed_in_chat_is_limited(self):
        self.exhaust("chat", "CHAT_MESSAGES_PER_HOUR")
        self.assert_limited(self.client.post("/chat/stream", json={"query": "hi"}), "messages")
        self.assert_limited(self.client.post("/chat", json={"query": "hi"}), "messages")

    def test_uploads_are_limited_before_the_file_is_read(self):
        cases = [
            ("pdf", "PDF_UPLOADS_PER_HOUR", "/upload/pdf", "a.pdf", "PDFs"),
            ("image", "IMAGE_UPLOADS_PER_HOUR", "/upload/image", "a.png", "images"),
            ("voice", "VOICE_REQUESTS_PER_HOUR", "/voice/transcribe", "a.webm", "voice"),
        ]
        for action, setting, url, name, word in cases:
            with self.subTest(action=action):
                self.exhaust(action, setting)
                with mock.patch.object(main, "save_upload") as save:
                    response = self.client.post(url, files={"file": (name, b"data")})
                self.assert_limited(response, word)
                save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
