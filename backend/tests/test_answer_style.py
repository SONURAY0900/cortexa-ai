"""
Answer length: short by default (MCQs, quick questions), a full
step-by-step explanation only when the user asks for one. No network.
"""

import unittest

from agents import llm_agent
from agents import rag_agent as rag
from agents.llm_agent import answer_style

FULL = "explain fully, step by step"
BRIEF = "Answer briefly"


class AnswerStyle(unittest.TestCase):

    def test_explain_requests_get_a_full_explanation(self):
        for query in [
            "Q19 ... (A) 1/2 (B) 1/4 (C) 1/6 (D) 1/36. Explain step by step.",
            "explain it",
            "Can you give a detailed answer?",
            "Why is the sky blue?",
            "Derive the quadratic formula",
            "show the steps",
        ]:
            with self.subTest(query=query):
                self.assertIn(FULL, answer_style(query))

    def test_mcqs_and_short_questions_stay_brief(self):
        for query in [
            "Which data structure uses LIFO order? (a) Queue (b) Stack (c) Tree (d) Graph",
            "What is the capital of Australia?",
            "List some unexplained phenomena",          # "explain" inside another word
            "",
        ]:
            with self.subTest(query=query):
                self.assertIn(BRIEF, answer_style(query))

    def test_both_answer_prompts_carry_the_style_line(self):
        self.assertIn(FULL, llm_agent.build_prompt("explain stacks", "data"))
        self.assertIn(BRIEF, llm_agent.build_prompt("Capital of France?", "data"))
        self.assertIn(FULL, rag.build_pdf_prompt("explain page 2", "ctx"))
        self.assertIn(BRIEF, rag.build_pdf_prompt("Which option is correct? (a) x (b) y", "ctx"))


if __name__ == "__main__":
    unittest.main()
