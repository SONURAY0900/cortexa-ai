"""
Numbered PDF questions read from the page image (extraction breaks
formulas such as determinants). Vision and retrieval are mocked.
"""

import os
import tempfile
import unittest
from unittest import mock

import pymupdf

from agents import rag_agent as rag
from rag import question_reader as qr


def two_page_pdf() -> str:
    handle, path = tempfile.mkstemp(suffix=".pdf")
    os.close(handle)
    doc = pymupdf.open()
    for text in ("22. First page", "23. Second page"):
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


class AskedQuestion(unittest.TestCase):

    def test_question_numbers_are_found(self):
        for query, number in [
            ("give mcq answer and from pdf question no 23", 23),
            ("solve Q23", 23), ("Q.7 please", 7), ("answer que no 5", 5),
            ("question number 12", 12), ("Question 28 from the pdf", 28),
        ]:
            with self.subTest(query=query):
                self.assertEqual(qr.asked_question(query), number)

    def test_other_numbers_are_not_questions(self):
        for query in ["what happened in 2023", "summarise page 5", "explain chapter 3", ""]:
            with self.subTest(query=query):
                self.assertIsNone(qr.asked_question(query))

    def test_page_of_question(self):
        chunks = [
            {"page_number": 4, "content": "17. If the given lines ... 123 1 16"},
            {"page_number": 5, "content": "23. If x, y, z are different numbers and 23 23 23 1 10 1"},
        ]
        self.assertEqual(qr.page_of_question(chunks, 23), 5)
        self.assertIsNone(qr.page_of_question(chunks, 2))        # "23." or "123" are not question 2
        self.assertIsNone(qr.page_of_question(chunks, 99))


class ReadQuestion(unittest.TestCase):

    def setUp(self):
        self.pdf = two_page_pdf()
        self.addCleanup(os.remove, self.pdf)

    def test_renders_the_right_page_and_returns_the_text(self):
        seen = {}

        def vision(image_path, prompt, max_tokens):
            with pymupdf.open(image_path) as image:
                seen["size"] = image[0].rect
            seen["prompt"], seen["max_tokens"] = prompt, max_tokens
            return "23. Show that $1 + xyz = 0$"

        with mock.patch.object(qr, "analyze_image", side_effect=vision):
            self.assertEqual(qr.read_question(self.pdf, 2, 23), "23. Show that $1 + xyz = 0$")
        self.assertIn("question 23", seen["prompt"])
        self.assertLessEqual(seen["max_tokens"], 300)

    def test_not_on_page_and_failures_give_none(self):
        with mock.patch.object(qr, "analyze_image", return_value="NOT ON PAGE"):
            self.assertIsNone(qr.read_question(self.pdf, 1, 23))
        with mock.patch.object(qr, "analyze_image", side_effect=RuntimeError("429")):
            self.assertIsNone(qr.read_question(self.pdf, 1, 23))
        self.assertIsNone(qr.read_question(self.pdf + ".missing", 1, 23))


class RetrievalUsesThePageImage(unittest.TestCase):

    RETRIEVED = {
        "context": "[Source 1 | Page 5 | Chunk 3]\n23. If x, y, z ... 23 23 23 1 10 1",
        "context_chunks": [{"page_number": 5, "chunk_index": 3, "content": "23. If x, y, z ... 23 23 23 1 10 1"}],
    }

    def retrieve(self, query, path):
        with mock.patch.object(rag, "retrieve_context", return_value=self.RETRIEVED), \
                mock.patch.object(rag, "read_question", return_value="23. If $\\Delta = 0$, show $1+xyz=0$") as read:
            result = rag.retrieve_pdf_context(query, [{"id": "d", "filename": "Math.pdf", "path": path}])
        return result["context"], read

    def test_printed_question_comes_first(self):
        context, read = self.retrieve("give answer of question no 23", "/uploads/math.pdf")
        read.assert_called_once_with("/uploads/math.pdf", 5, 23)
        self.assertTrue(context.startswith("Question 23 as printed on page 5"))
        self.assertIn("[Source 1 | Page 5", context)                  # extracted text kept too

    def test_no_number_or_no_file_skips_the_extra_read(self):
        for query, path in [("what is a determinant", "/uploads/math.pdf"), ("solve question 23", None)]:
            with self.subTest(query=query, path=path):
                context, read = self.retrieve(query, path)
                read.assert_not_called()
                self.assertTrue(context.startswith("[Source 1"))


if __name__ == "__main__":
    unittest.main()
