"""
PDF questions in the streaming chat: one grounded LLM call.

Retrieval, Groq and Supabase are mocked; no network.
"""

import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import main
from agents import rag_agent as rag
from auth.supabase_auth import get_current_user
from tests.test_security import make_user


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def chunks(*texts):
    return iter(SimpleNamespace(content=t) for t in texts)


class StreamedPdfAnswer(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        main.app.dependency_overrides[get_current_user] = lambda: make_user()
        self.addCleanup(main.app.dependency_overrides.clear)

        plan = main.AttachmentPlan(documents=[{"id": "doc-1", "filename": "notes.pdf"}])
        self.history = [{"role": "user", "content": "What is a stack?"},
                        {"role": "assistant", "content": "A LIFO structure."}]
        patches = {
            "_plan_attachments": mock.Mock(return_value=plan),
            "_get_or_create_conversation": mock.Mock(return_value={"id": str(uuid.uuid4())}),
            "_get_conversation_history": mock.Mock(return_value=self.history),
            "_save_message": mock.Mock(return_value=str(uuid.uuid4())),
            "_set_message_route": mock.Mock(),
            "_update_conversation_title_if_needed": mock.Mock(),
            "log_activity": mock.Mock(),
            # The general-purpose rewrite must not run for PDF questions.
            "stream_response": mock.Mock(side_effect=AssertionError("second LLM call")),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.saved = main._save_message

    def retrieval(self, found=True):
        return {
            "context": "[Source 1 | Page 45 | Chunk 3]\nA stack follows LIFO." if found else "",
            "sources": [{"type": "pdf", "title": "Page 45", "page_number": 45}] if found else [],
            "chunks": [{"page_number": 45, "content": "A stack follows LIFO."}] if found else [],
        }

    def ask(self, query="And what is pop?"):
        return self.client.post("/chat/stream", json={"query": query})

    def test_single_grounded_call_streams_answer_and_sources(self):
        llm = mock.Mock()
        llm.stream.return_value = chunks("Pop removes ", "the top element.")
        llm.invoke.side_effect = AssertionError("non-streaming call")
        with mock.patch.object(main, "retrieve_pdf_context", return_value=self.retrieval()) as retrieve, \
                mock.patch.object(rag, "rag_llm", llm):
            response = self.ask()

        self.assertEqual(response.status_code, 200)
        evts = events(response)
        text = "".join(e["content"] for e in evts if e["type"] == "content")
        self.assertEqual(text, "Pop removes the top element.")
        self.assertEqual(llm.stream.call_count, 1)
        retrieve.assert_called_once_with("And what is pop?", [{"id": "doc-1", "filename": "notes.pdf"}],
                                         chat_history=self.history)

        prompt = llm.stream.call_args.args[0]
        self.assertIn("using ONLY", prompt)                          # grounded PDF prompt
        self.assertIn("A stack follows LIFO.", prompt)                # retrieved context
        self.assertIn("not a source of facts", prompt)                # history only for follow-ups
        self.assertIn("ASSISTANT: A LIFO structure.", prompt)

        sources = next(e for e in evts if e["type"] == "sources")["sources"]
        self.assertEqual(sources[0]["page_number"], 45)
        saved_answer = self.saved.call_args_list[-1]
        self.assertEqual(saved_answer.args[2:4], ("ASSISTANT", "Pop removes the top element."))

    def test_nothing_found_answers_without_any_llm_call(self):
        llm = mock.Mock()
        llm.stream.side_effect = AssertionError("LLM called")
        with mock.patch.object(main, "retrieve_pdf_context", return_value=self.retrieval(found=False)), \
                mock.patch.object(rag, "rag_llm", llm):
            evts = events(self.ask("Who won the World Cup?"))
        text = "".join(e["content"] for e in evts if e["type"] == "content")
        self.assertEqual(text, rag.NOT_FOUND)
        self.assertEqual(evts[-1]["type"], "done")

    def test_rate_limit_shows_busy_message(self):
        class RateLimited(Exception):
            status_code = 429

        llm = mock.Mock()
        llm.stream.side_effect = RateLimited("429 tokens per minute")
        with mock.patch.object(main, "retrieve_pdf_context", return_value=self.retrieval()), \
                mock.patch.object(rag, "rag_llm", llm):
            evts = events(self.ask())
        error = next(e for e in evts if e["type"] == "error")
        self.assertIn("busy", error["content"])
        self.assertNotIn("429", error["content"])


class PdfPrompt(unittest.TestCase):

    def test_history_is_limited_and_optional(self):
        history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
        prompt = rag.build_pdf_prompt("q", "ctx", history)
        self.assertNotIn("USER: q3\n", prompt)
        self.assertIn("USER: q4\n", prompt)                          # last 6 messages
        self.assertNotIn("Previous conversation", rag.build_pdf_prompt("q", "ctx"))

    def test_follow_up_questions_are_searched_with_their_topic(self):
        history = [{"role": "user", "content": "Explain binary search"},
                   {"role": "assistant", "content": "It halves a sorted array."}]
        for follow_up in ["and what is its time complexity?", "Why is that faster?", "What about the worst case?"]:
            with self.subTest(follow_up=follow_up):
                self.assertEqual(rag.retrieval_query(follow_up, history), f"Explain binary search {follow_up}")

    def test_standalone_questions_are_searched_unchanged(self):
        history = [{"role": "user", "content": "Explain binary search"}]
        for question in ["What is a leaf node?", "Types of linked lists",
                         "Give a detailed explanation of the difference between that kind of tree and a heap in this document"]:
            with self.subTest(question=question):
                self.assertEqual(rag.retrieval_query(question, history), question)
        self.assertEqual(rag.retrieval_query("what is its use?", None), "what is its use?")
        self.assertEqual(rag.retrieval_query("what is its use?", [{"role": "assistant", "content": "x"}]),
                         "what is its use?")

    def test_retrieval_uses_the_follow_up_search_text(self):
        history = [{"role": "user", "content": "Explain binary search"}]
        with mock.patch.object(rag, "retrieve_context", return_value={"context": "", "context_chunks": []}) as rc:
            rag.retrieve_pdf_context("and its complexity?", [{"id": "d", "filename": None}], chat_history=history)
        self.assertEqual(rc.call_args.kwargs["query"], "Explain binary search and its complexity?")

    def test_image_analysis_is_added_to_the_context(self):
        prompt = rag.build_pdf_prompt("q", "pdf text", image_context="OCR: hello")
        self.assertIn("pdf text\n\nAttached image analysis:\nOCR: hello", prompt)

    def test_rag_agent_keeps_its_contract(self):
        with mock.patch.object(rag, "retrieve_context", return_value={"context": "", "context_chunks": []}):
            self.assertEqual(rag.rag_agent("q", document_id="d")["answer"], rag.NOT_FOUND)
        with mock.patch.object(rag, "retrieve_context", return_value={
                "context": "[Source 1 | Page 2 | Chunk 0]\ntext", "context_chunks": [{"page_number": 2, "content": "text"}]}), \
                mock.patch.object(rag, "rag_llm", mock.Mock(invoke=mock.Mock(return_value=SimpleNamespace(content="answer")))):
            result = rag.rag_agent("q", document_id="d")
        self.assertEqual(result["answer"], "answer")
        self.assertEqual(result["sources"][0]["page_number"], 2)


if __name__ == "__main__":
    unittest.main()
