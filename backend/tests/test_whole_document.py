"""
Whole-document requests ("give all questions"): the full text in page
order with a question checklist, an output budget that fits Groq's
per-request limit, and an answer style that asks for every item.
Supabase and Groq are mocked.
"""

import unittest
from unittest import mock

from agents import rag_agent as rag
from agents.llm_agent import answer_style


def chunk(i, page, text):
    return {"chunk_index": i, "page_number": page, "content": text}


QUESTION_PAPER = [chunk(i, 1 + i // 3, f"{i + 1}. Question number {i + 1} text.") for i in range(12)]


class WholeDocumentRequests(unittest.TestCase):

    def test_detection(self):
        for query in ["give all question from this pdf", "list every MCQ in the paper",
                      "summarise this PDF", "show the complete question paper"]:
            with self.subTest(query=query):
                self.assertTrue(rag.WHOLE_DOCUMENT.search(query))
        for query in ["solve question 23", "what is a determinant", "who wrote this"]:
            with self.subTest(query=query):
                self.assertFalse(rag.WHOLE_DOCUMENT.search(query))

    def test_whole_text_in_order_with_a_checklist(self):
        with mock.patch.object(rag, "document_chunks_in_order", return_value=QUESTION_PAPER):
            context, kept = rag.whole_document_context("d")
        self.assertEqual(len(kept), 12)
        self.assertTrue(context.startswith("[This text contains questions numbered 1 to 12."))
        self.assertLess(context.index("1. Question number 1"), context.index("12. Question number 12"))
        self.assertIn("[Source 1 | Page 1 | Chunk 0]", context)

    def test_long_documents_are_cut_with_a_note(self):
        long_doc = [chunk(i, i + 1, f"Page {i + 1} " + "x" * 1500) for i in range(20)]
        with mock.patch.object(rag, "document_chunks_in_order", return_value=long_doc):
            context, kept = rag.whole_document_context("d")
        self.assertLess(len(kept), 20)
        self.assertLessEqual(sum(len(c["content"]) for c in kept), rag.WHOLE_DOCUMENT_CHARS)
        self.assertIn("the PDF continues to page 20", context)

    def test_retrieval_uses_the_whole_document_instead_of_search(self):
        with mock.patch.object(rag, "document_chunks_in_order", return_value=QUESTION_PAPER), \
                mock.patch.object(rag, "retrieve_context", side_effect=AssertionError("searched")):
            result = rag.retrieve_pdf_context("give all question from this pdf", [{"id": "d", "filename": "Math.pdf"}])
        self.assertEqual(len(result["chunks"]), 12)
        self.assertEqual([s["page_number"] for s in result["sources"]], [1, 2, 3, 4])

    def test_no_checklist_without_numbered_questions(self):
        prose = [chunk(0, 1, "Chapter one is about stacks. Version 2. and 3. are old.")]
        with mock.patch.object(rag, "document_chunks_in_order", return_value=prose):
            context, _ = rag.whole_document_context("d")
        self.assertFalse(context.startswith("[This text contains questions"))


class OutputBudget(unittest.TestCase):

    def test_prompt_plus_output_fits_the_request_limit(self):
        for prompt_chars in (2000, 10652, 16000, 30000):
            with self.subTest(prompt_chars=prompt_chars):
                budget = rag.output_budget("x" * prompt_chars)
                self.assertGreaterEqual(budget, 1024)
                self.assertLessEqual(budget, 4096)
                if prompt_chars <= 16000:
                    self.assertLessEqual(prompt_chars // 3 + budget, 7800)

    def test_pdf_answers_request_the_budget(self):
        llm = mock.Mock()
        llm.stream.return_value = iter([])
        with mock.patch.object(rag, "rag_llm", llm):
            list(rag.stream_pdf_answer("give all questions", "ctx"))
        self.assertEqual(llm.stream.call_args.kwargs["max_tokens"], rag.output_budget(llm.stream.call_args.args[0]))


class CompleteAnswers(unittest.TestCase):

    def test_requests_for_everything_ask_for_every_item(self):
        for query in ["give all question from this pdf", "list every MCQ", "the full list please"]:
            with self.subTest(query=query):
                self.assertIn("include every item", answer_style(query))
        self.assertNotIn("include every item", answer_style("What is the capital of France?"))


if __name__ == "__main__":
    unittest.main()
