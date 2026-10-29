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
from auth.supabase_auth import get_current_user, get_user_or_guest
from tests.test_security import make_user


def events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def chunks(*texts):
    return iter(SimpleNamespace(content=t) for t in texts)


class StreamedPdfAnswer(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        user = make_user()
        main.app.dependency_overrides[get_current_user] = lambda: user
        main.app.dependency_overrides[get_user_or_guest] = lambda: user
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

    def general_answer(self, retrieval, pdf_reply):
        """Ask a general question; the web search route answers it."""
        llm = mock.Mock()
        llm.stream.return_value = chunks(*pdf_reply)
        tavily = mock.Mock(return_value={"content": "Argentina won in 2022.",
                                         "sources": [{"title": "FIFA", "url": "https://fifa.com"}]})
        general = mock.Mock(return_value=iter(["Argentina ", "won the 2022 World Cup."]))   # yields text
        with mock.patch.object(main, "retrieve_pdf_context", return_value=retrieval), \
                mock.patch.object(rag, "rag_llm", llm), \
                mock.patch.object(main, "route_query", return_value=SimpleNamespace(route="TAVILY", reason="current")), \
                mock.patch.dict(main.SEARCH_AGENTS, {"TAVILY": (tavily, "Searching the web")}), \
                mock.patch.object(main, "stream_response", general):
            evts = events(self.ask("Who won the World Cup?"))
        return evts, llm, general

    def assert_general_answer(self, evts):
        text = "".join(e["content"] for e in evts if e["type"] == "content")
        self.assertTrue(text.startswith("**This isn't covered in your PDF**, so here's an answer from a web search"))
        self.assertTrue(text.endswith("Argentina won the 2022 World Cup."))
        self.assertNotIn("NOT_IN_PDF", text)
        self.assertEqual([e["route"] for e in evts if e["type"] == "route"], ["PDF_RAG", "TAVILY"])
        sources = next(e for e in evts if e["type"] == "sources")["sources"]
        self.assertEqual(sources, [{"title": "FIFA", "url": "https://fifa.com"}])   # not the PDF pages
        self.assertEqual(self.saved.call_args_list[-1].args[3], text)
        self.assertEqual(evts[-1]["type"], "done")

    def test_nothing_found_in_the_pdf_is_answered_from_other_sources(self):
        evts, llm, general = self.general_answer(self.retrieval(found=False), ["unused"])
        llm.stream.assert_not_called()                                # no PDF call without PDF text
        self.assert_general_answer(evts)

    def test_a_general_question_the_pdf_does_not_cover(self):
        evts, llm, general = self.general_answer(self.retrieval(), ["NOT_", "IN_PDF"])
        self.assertEqual(llm.stream.call_count, 1)
        self.assert_general_answer(evts)

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

    def test_request_too_large_has_its_own_message(self):
        class TooLarge(Exception):
            status_code = 413

        llm = mock.Mock()
        llm.stream.side_effect = TooLarge("413 request too large")
        with mock.patch.object(main, "retrieve_pdf_context", return_value=self.retrieval()), \
                mock.patch.object(rag, "rag_llm", llm):
            evts = events(self.ask())
        error = next(e for e in evts if e["type"] == "error")
        self.assertIn("too long", error["content"])
        self.assertNotIn("413", error["content"])


class PromptBudget(unittest.TestCase):
    """Prompts must stay well under Groq's 8,000-token per-request limit."""

    def test_general_prompt_is_capped(self):
        from agents import llm_agent
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 5000} for i in range(20)]
        prompt = llm_agent.build_prompt("latest AI news?", "y" * 50000, history)
        self.assertLess(len(prompt), 12000)                          # about 3,000 tokens
        self.assertEqual(prompt.count("USER:") + prompt.count("ASSISTANT:"), llm_agent.HISTORY_MESSAGES)
        self.assertIn(" …", prompt)                                  # long parts are visibly cut

    def test_short_inputs_are_unchanged(self):
        from agents import llm_agent
        prompt = llm_agent.build_prompt("q", "short result", [{"role": "user", "content": "hello"}])
        self.assertIn("short result", prompt)
        self.assertIn("USER: hello\n", prompt)
        self.assertNotIn(" …", prompt)

    def test_pdf_prompt_history_and_image_text_are_capped(self):
        history = [{"role": "assistant", "content": "z" * 5000}] * 10
        prompt = rag.build_pdf_prompt("q", "ctx", history, image_context="i" * 20000)
        self.assertLess(len(prompt), 12000)

    def test_search_results_are_trimmed(self):
        from tools import tavily_tool
        fake = mock.Mock()
        fake.search.return_value = {"results": [{"title": f"t{i}", "content": "c" * 10000, "url": f"https://e.com/{i}"}
                                                for i in range(5)]}
        with mock.patch.object(tavily_tool, "tavily_client", fake), mock.patch.object(tavily_tool, "TAVILY_AVAILABLE", True):
            result = tavily_tool.search_tavily("news")
        self.assertLess(len(result["content"]), 5 * (tavily_tool.RESULT_CHARS + 200))
        self.assertEqual(len(result["sources"]), 5)


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
        found = {"context": "[Source 1 | Page 2 | Chunk 0]\ntext", "context_chunks": [{"page_number": 2, "content": "text"}]}
        with mock.patch.object(rag, "retrieve_context", return_value=found), \
                mock.patch.object(rag, "rag_llm", mock.Mock(invoke=mock.Mock(return_value=SimpleNamespace(content="answer")))):
            result = rag.rag_agent("q", document_id="d")
        self.assertEqual(result["answer"], "answer")
        self.assertEqual(result["sources"][0]["page_number"], 2)

    def test_rag_agent_answers_general_questions_the_pdf_does_not_cover(self):
        found = {"context": "[Source 1 | Page 2 | Chunk 0]\ntext", "context_chunks": [{"page_number": 2, "content": "text"}]}
        for retrieval, reply in (({"context": "", "context_chunks": []}, "unused"), (found, "NOT_IN_PDF")):
            with self.subTest(reply=reply), \
                    mock.patch.object(rag, "retrieve_context", return_value=retrieval), \
                    mock.patch.object(rag, "rag_llm", mock.Mock(invoke=mock.Mock(return_value=SimpleNamespace(content=reply)))), \
                    mock.patch.object(rag, "generate_response", return_value="Paris."):
                result = rag.rag_agent("Capital of France?", document_id="d")
            self.assertTrue(result["answer"].startswith("**This isn't covered in your PDF**"))
            self.assertTrue(result["answer"].endswith("Paris."))
            self.assertEqual(result["sources"], [])


if __name__ == "__main__":
    unittest.main()
