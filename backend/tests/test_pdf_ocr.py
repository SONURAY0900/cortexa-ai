"""
PDF extraction, OCR reliability and ingestion outcome tests.

OCR.space is always mocked (no network, no API credits). PDFs are
generated on the fly: text pages carry a text layer, "scanned" pages are
an image only, so embedded-text extraction finds nothing there.

    cd backend
    python -m unittest tests.test_pdf_ocr -v
"""

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from fastapi.testclient import TestClient

import main
from auth.supabase_auth import get_current_user
from rag import pipeline
from rag.ingestion import ocr_processor, pdf_loader
from tests.test_security import make_user

LONG = "The quick brown fox jumps over the lazy dog near the riverbank today."


def make_pdf(pages: list[str]) -> str:
    """
    pages: "text:<content>" (text layer), "scan:<content>" (image only)
    or "blank". Returns a temporary file path.
    """
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=595, height=842)
        kind, _, content = spec.partition(":")
        if kind == "text":
            page.insert_text((72, 100), content, fontsize=12)
        elif kind == "scan":
            src = pymupdf.open()
            p = src.new_page(width=595, height=200)
            p.insert_text((40, 100), content, fontsize=14)
            png = p.get_pixmap(dpi=100).tobytes("png")
            src.close()
            page.insert_image(pymupdf.Rect(40, 40, 555, 240), stream=png)
    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    doc.save(path)
    doc.close()
    return path


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def ok(text):
    return FakeResponse({"IsErroredOnProcessing": False, "OCRExitCode": 1,
                         "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": text}]})


def e502():
    return FakeResponse({"IsErroredOnProcessing": True, "OCRExitCode": 99,
                         "ErrorMessage": ["Error E502: Error during OCR"]})


class OcrServer:
    """
    Fake OCR.space. `plan` maps page number -> list of responses consumed
    in order (the last one repeats). Records (page, engine) per call.
    """

    def __init__(self, plan, default=None):
        self.plan = {k: list(v) for k, v in plan.items()}
        self.default = default
        self.calls = []

    def __call__(self, url, files=None, data=None, timeout=None):
        name = files["file"][0]
        page = int(re.search(r"page_(\d+)", name).group(1)) if "page_" in name else 0
        self.calls.append((page, data["OCREngine"]))
        queue = self.plan.get(page)
        if not queue:
            return self.default() if self.default else ok(f"{LONG} (page {page})")
        response = queue[0] if len(queue) == 1 else queue.pop(0)
        return response() if callable(response) else response


class Base(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {
            "OCR_SPACE_API_KEY": "test-key", "OCR_SPACE_MIN_INTERVAL_S": "0",
            "OCR_MAX_ATTEMPTS": "4", "OCR_SPACE_ENGINE": "2", "OCR_SPACE_FALLBACK_ENGINE": "3",
        })
        env.start()
        self.addCleanup(env.stop)
        self.sleep = mock.patch.object(ocr_processor.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            Path(path).unlink(missing_ok=True)

    def pdf(self, pages):
        path = make_pdf(pages)
        self.paths.append(path)
        return path

    def serve(self, plan=None, default=None):
        server = OcrServer(plan or {}, default)
        mock.patch.object(ocr_processor.requests, "post", side_effect=server).start()
        return server


# ============================================================
# 1–9: extraction and OCR
# ============================================================

class Extraction(Base):

    def test_text_pdf_uses_no_ocr(self):
        server = self.serve()
        pages = pdf_loader.extract_text_from_pdf(self.pdf([f"text:{LONG}", f"text:{LONG} two"]))
        self.assertEqual([p["source"] for p in pages], ["text", "text"])
        self.assertEqual(server.calls, [])

    def test_scanned_pdf_all_pages_ocr_success(self):
        server = self.serve()
        pages = pdf_loader.extract_text_from_pdf(self.pdf(["scan:alpha", "scan:beta", "scan:gamma"]))
        self.assertEqual([p["ocr_status"] for p in pages], ["success"] * 3)
        self.assertEqual([p["page_number"] for p in pages], [1, 2, 3])
        for p in pages:   # text attributed to its own page
            self.assertIn(f"(page {p['page_number']})", p["text"])
        self.assertEqual(sorted(c[0] for c in server.calls), [1, 2, 3])

    def test_mixed_pdf_only_scanned_pages_go_to_ocr(self):
        server = self.serve()
        pages = pdf_loader.extract_text_from_pdf(self.pdf([f"text:{LONG}", "scan:image page", f"text:{LONG} three"]))
        self.assertEqual([p["source"] for p in pages], ["text", "ocr", "text"])
        self.assertEqual([c[0] for c in server.calls], [2])

    def test_e502_then_success_on_retry(self):
        server = self.serve({1: [e502, ok(f"{LONG} recovered")]})
        result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
        self.assertEqual(result.status, "success")
        self.assertIn("recovered", result.text)
        self.assertEqual(result.attempts, 2)
        # Engine error -> the other engine is tried next.
        self.assertEqual([c[1] for c in server.calls], ["2", "3"])
        self.sleep.assert_called_once()

    def test_failure_after_all_retries(self):
        server = self.serve({1: [e502]})
        result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "E502")
        self.assertEqual(result.attempts, 4)
        self.assertEqual(len(server.calls), 4)
        self.assertEqual({c[1] for c in server.calls}, {"2", "3"})      # alternated engines
        delays = [c.args[0] for c in self.sleep.call_args_list]
        self.assertEqual(len(delays), 3)
        self.assertTrue(all(0 < d <= 8 for d in delays))

    def test_permanent_error_is_not_retried_on_same_engine(self):
        bad_key = FakeResponse({"IsErroredOnProcessing": True, "ErrorMessage": ["Invalid API key"]}, status=200)
        server = self.serve({1: [bad_key]})
        result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
        self.assertEqual(result.status, "failed")
        self.assertEqual([c[1] for c in server.calls], ["2", "3"])     # each engine once, no backoff loop
        self.sleep.assert_not_called()

    def test_rate_limit_waits_retry_after(self):
        limited = FakeResponse({}, status=429, headers={"Retry-After": "7"})
        self.serve({1: [limited, ok(LONG)]})
        result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
        self.assertEqual(result.status, "success")
        self.assertGreaterEqual(self.sleep.call_args_list[0].args[0], 7)

    def test_one_page_fails_others_succeed(self):
        self.serve({2: [e502]})
        pages = pdf_loader.extract_text_from_pdf(self.pdf(["scan:a", "scan:b", "scan:c"]))
        self.assertEqual([p["ocr_status"] for p in pages], ["success", "failed", "success"])
        self.assertEqual(pages[1]["text"], "")
        self.assertEqual(pages[1]["ocr_error"], "E502")

    def test_malformed_and_empty_responses(self):
        cases = {
            "not json": FakeResponse(None, text="<html>gateway</html>"),
            "no results": FakeResponse({"IsErroredOnProcessing": False, "OCRExitCode": 1}),
            "results not list": FakeResponse({"IsErroredOnProcessing": False, "ParsedResults": "x"}),
            "page timeout": FakeResponse({"IsErroredOnProcessing": False,
                                          "ParsedResults": [{"FileParseExitCode": -20, "ErrorMessage": "timeout"}]}),
            "http 503": FakeResponse({}, status=503),
        }
        for label, response in cases.items():
            with self.subTest(label):
                self.sleep.reset_mock()
                self.serve({1: [response, ok(f"{LONG} after {label}")]})
                result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
                self.assertEqual(result.status, "success", label)
                self.assertEqual(result.attempts, 2)

    def test_blank_page_is_empty_not_failed(self):
        self.serve({1: [ok("")]})
        pages = pdf_loader.extract_text_from_pdf(self.pdf(["blank"]))
        self.assertEqual(pages[0]["ocr_status"], "empty")

    def test_missing_api_key_fails_without_requests(self):
        server = self.serve()
        with mock.patch.dict(os.environ, {"OCR_SPACE_API_KEY": ""}):
            result = ocr_processor.ocr_pdf_page(self.pdf(["scan:x"]), 1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(server.calls, [])

    def test_progress_reports_ocr_pages(self):
        self.serve()
        events = []
        pdf_loader.extract_text_from_pdf(self.pdf([f"text:{LONG}", "scan:b", "scan:c"]),
                                         progress=lambda stage, d=None: events.append((stage, d)))
        self.assertEqual(events, [("ocr", {"page": 2, "current": 1, "total": 2}),
                                  ("ocr", {"page": 3, "current": 2, "total": 2})])


# ============================================================
# 10–12: ingestion outcomes, statuses, no duplicate chunks
# ============================================================

class Ingestion(Base):

    def setUp(self):
        super().setUp()
        self.created = []
        self.stored = []
        self.deleted = []
        self.completed = []
        mock.patch.object(pipeline, "create_document", side_effect=lambda **kw: self.created.append(kw) or "doc-1").start()
        mock.patch.object(pipeline, "store_chunks", side_effect=lambda document_id, chunks: self.stored.append(chunks)).start()
        mock.patch.object(pipeline, "delete_document", side_effect=lambda document_id: self.deleted.append(document_id)).start()
        mock.patch.object(pipeline, "generate_embeddings", side_effect=lambda texts: [[0.0] * 384 for _ in texts]).start()
        db = mock.patch.object(pipeline, "supabase").start()
        db.table.return_value.update.side_effect = lambda values: self.completed.append(values) or db.table.return_value.update.return_value

    def test_full_success(self):
        self.serve()
        doc_id, summary = pipeline.ingest_pdf_with_summary(self.pdf(["scan:a", f"text:{LONG}"]), "f.pdf")
        self.assertEqual(doc_id, "doc-1")
        self.assertTrue(summary["indexed"])
        self.assertFalse(summary["partial"])
        self.assertEqual(summary["pages_indexed"], [1, 2])
        self.assertEqual(summary["pages_needing_ocr"], 1)
        self.assertEqual(self.completed, [{"processing_status": "COMPLETED"}])
        self.assertEqual(len(self.stored), 1)                       # one write, no duplicates

    def test_partial_success_keeps_good_pages(self):
        self.serve({2: [e502]})
        _, summary = pipeline.ingest_pdf_with_summary(self.pdf(["scan:a", "scan:b", "scan:c"]), "f.pdf")
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["failed_pages"], [2])
        self.assertEqual(summary["pages_indexed"], [1, 3])
        self.assertEqual({c["page_number"] for c in self.stored[0]}, {1, 3})   # nothing claimed for page 2

    def test_citations_carry_the_source_page(self):
        # Mixed PDF with an unreadable middle page: every stored chunk keeps
        # the page it came from, and the LLM context cites that page.
        from rag.retrieval.context_builder import build_context
        self.serve({3: [e502]})
        pipeline.ingest_pdf_with_summary(self.pdf(["scan:a", f"text:{LONG} alpha", "scan:c", "scan:d"]), "f.pdf")
        chunks = self.stored[0]
        self.assertEqual({c["page_number"] for c in chunks}, {1, 2, 4})
        for c in chunks:
            if c["page_number"] != 2:                               # OCR text names its page
                self.assertIn(f"(page {c['page_number']})", c["content"])
        context = build_context([{**c, "document_id": "doc-1"} for c in chunks])
        for c in chunks:
            self.assertIn(f"Page {c['page_number']} | Chunk {c['chunk_index']}]\n{c['content'].strip()}", context)
        self.assertNotIn("Page 3 |", context)

    def test_all_ocr_failed_is_failure(self):
        self.serve({1: [e502], 2: [e502]})
        with self.assertRaises(pipeline.IngestionError) as ctx:
            pipeline.ingest_pdf_with_summary(self.pdf(["scan:a", "scan:b"]), "f.pdf")
        self.assertEqual(ctx.exception.reason, "ocr_failed")
        self.assertEqual(ctx.exception.summary["failed_pages"], [1, 2])
        self.assertEqual(self.created, [])                          # no document row at all
        self.assertEqual(self.completed, [])

    def test_no_extractable_text_is_failure(self):
        self.serve({1: [ok("")]})
        with self.assertRaises(pipeline.IngestionError) as ctx:
            pipeline.ingest_pdf_with_summary(self.pdf(["blank"]), "f.pdf")
        self.assertEqual(ctx.exception.reason, "no_text")

    def test_embedding_failure_rolls_back(self):
        self.serve()
        mock.patch.object(pipeline, "generate_embeddings", side_effect=RuntimeError("model crashed")).start()
        with self.assertRaises(pipeline.IngestionError) as ctx:
            pipeline.ingest_pdf_with_summary(self.pdf([f"text:{LONG}"]), "f.pdf")
        self.assertEqual(ctx.exception.reason, "indexing_failed")
        self.assertEqual(self.deleted, ["doc-1"])
        self.assertEqual(self.completed, [])

    def test_storage_failure_rolls_back(self):
        self.serve()
        mock.patch.object(pipeline, "store_chunks", side_effect=RuntimeError("db down")).start()
        with self.assertRaises(pipeline.IngestionError):
            pipeline.ingest_pdf_with_summary(self.pdf([f"text:{LONG}"]), "f.pdf")
        self.assertEqual(self.deleted, ["doc-1"])

    def test_retrying_ingestion_creates_independent_documents(self):
        """A retried upload is a new document; the failed one left nothing behind."""
        self.serve({1: [e502]})
        path = self.pdf(["scan:a"])
        with self.assertRaises(pipeline.IngestionError):
            pipeline.ingest_pdf_with_summary(path, "f.pdf")
        self.serve()   # provider recovered
        pipeline.ingest_pdf_with_summary(path, "f.pdf")
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.stored), 1)


# ============================================================
# Upload endpoint: statuses and responses
# ============================================================

class UploadEndpoint(Base):

    def setUp(self):
        super().setUp()
        main.app.dependency_overrides[get_current_user] = lambda: make_user()
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.tmp = Path(tempfile.mkdtemp())
        mock.patch.object(main, "save_upload", side_effect=lambda d, c, e: self._save(c)).start()
        mock.patch.object(main, "create_file", return_value={"id": "file-1"}).start()
        self.status = mock.patch.object(main, "set_file_status").start()
        mock.patch.object(main, "log_activity").start()
        mock.patch.object(main, "delete_stored_file").start()

    def _save(self, content):
        path = self.tmp / "upload.pdf"
        path.write_bytes(content)
        return path

    def upload(self, stream=False):
        url = "/upload/pdf" + ("?stream=true" if stream else "")
        return self.client.post(url, files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")})

    def summary(self, **overrides):
        base = {"pages_total": 3, "pages_with_embedded_text": 0, "pages_needing_ocr": 3, "pages_ocr_succeeded": 3,
                "failed_pages": [], "pages_without_text": [], "pages_with_text": [1, 2, 3],
                "pages_indexed": [1, 2, 3], "chunks": 3, "indexed": True, "partial": False}
        return {**base, **overrides}

    def test_success_marks_processed(self):
        summary = self.summary()
        mock.patch.object(main, "ingest_pdf_with_summary", return_value=("doc-1", summary)).start()
        body = self.upload().json()
        self.assertTrue(body["success"])
        self.assertFalse(body["partial"])
        self.status.assert_called_once_with("file-1", "PROCESSED", {"processing": summary})

    def test_partial_is_explicit(self):
        summary = self.summary(failed_pages=[3], pages_indexed=[1, 2], partial=True, pages_ocr_succeeded=2)
        mock.patch.object(main, "ingest_pdf_with_summary", return_value=("doc-1", summary)).start()
        body = self.upload().json()
        self.assertTrue(body["partial"])
        self.assertIn("page 3", body["message"])
        self.assertIn("not searchable", body["message"])

    def test_failure_marks_failed_with_safe_details(self):
        error = pipeline.IngestionError("ocr_failed", "OCR failed: E502 at D:/secret/path",
                                        self.summary(failed_pages=[1, 2, 3], pages_indexed=[], indexed=False))
        mock.patch.object(main, "ingest_pdf_with_summary", side_effect=error).start()
        response = self.upload()
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["reason"], "ocr_failed")
        self.assertTrue(body["retryable"])
        self.assertNotIn("secret", response.text)
        self.assertEqual(self.status.call_args.args[:2], ("file-1", "FAILED"))

    def test_stream_reports_stages_then_result(self):
        def fake_ingest(**kwargs):
            kwargs["progress"]("extracting")
            kwargs["progress"]("ocr", {"page": 1, "current": 1, "total": 1})
            return "doc-1", self.summary()
        mock.patch.object(main, "ingest_pdf_with_summary", side_effect=fake_ingest).start()
        events = [json.loads(line) for line in self.upload(stream=True).text.splitlines() if line.strip()]
        self.assertEqual([e.get("stage") for e in events if e["type"] == "stage"], ["uploaded", "extracting", "ocr"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["summary"]["pages_indexed"], [1, 2, 3])

    def test_removed_while_processing_is_discarded(self):
        mock.patch.object(main, "ingest_pdf_with_summary", return_value=("doc-1", self.summary())).start()
        mock.patch.object(main, "_file_was_deleted", return_value=True).start()
        deleted = mock.patch.object(main, "delete_document").start()
        events = [json.loads(line) for line in self.upload(stream=True).text.splitlines() if line.strip()]
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["reason"], "cancelled")
        deleted.assert_called_once_with("doc-1")
        # The canonical file row stays DELETED (not overwritten with PROCESSED/FAILED).
        self.status.assert_not_called()

    def test_stream_unexpected_error_still_ends(self):
        mock.patch.object(main, "ingest_pdf_with_summary", side_effect=KeyError("boom")).start()
        events = [json.loads(line) for line in self.upload(stream=True).text.splitlines() if line.strip()]
        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("boom", json.dumps(events))


# ============================================================
# Chat retry does not store the question twice
# ============================================================

class ChatRetry(unittest.TestCase):

    def rows(self, rows):
        db = mock.patch.object(main, "supabase").start()
        self.addCleanup(mock.patch.stopall)
        chain = db.table.return_value.select.return_value.eq.return_value.eq.return_value
        chain.order.return_value.limit.return_value.execute.return_value = mock.Mock(data=rows)

    def test_reuses_last_unanswered_question(self):
        self.rows([{"id": "m1", "role": "USER", "content": "What is OCR?"}])
        self.assertEqual(main._unanswered_question_id("c1", make_user(), "What is OCR?"), "m1")

    def test_answered_or_different_question_is_not_reused(self):
        self.rows([{"id": "m2", "role": "ASSISTANT", "content": "answer"}])
        self.assertIsNone(main._unanswered_question_id("c1", make_user(), "What is OCR?"))
        self.rows([{"id": "m3", "role": "USER", "content": "Another question"}])
        self.assertIsNone(main._unanswered_question_id("c1", make_user(), "What is OCR?"))


if __name__ == "__main__":
    unittest.main()
