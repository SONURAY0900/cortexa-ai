"""
Images: OCR with engine fallback, a caution for scripts the readers
misread, and answering from whichever reader worked. No network.
"""

import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

from graph import nodes
from rag.ingestion.ocr_processor import OcrResult
from services.image_analysis import combine
from tools import ocr_tool


def image_file() -> str:
    handle, path = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    Image.new("RGB", (40, 20), "white").save(path)
    return path


class ImageOcr(unittest.TestCase):

    def setUp(self):
        self.path = image_file()
        self.addCleanup(os.remove, self.path)

    def test_uses_the_shared_ocr_with_engine_fallback(self):
        result = OcrResult(text=" ପ୍ରଶ୍ନ ", status="success", engine="3", attempts=2)
        with mock.patch.object(ocr_tool, "ocr_upload", return_value=result) as upload:
            self.assertEqual(ocr_tool.extract_text_from_image(self.path), "ପ୍ରଶ୍ନ")
        filename, data, mime = upload.call_args.args[0]
        self.assertEqual(mime, "image/jpeg")
        self.assertIsNone(upload.call_args.kwargs["language"])          # auto, not English only

    def test_no_text_and_failure(self):
        with mock.patch.object(ocr_tool, "ocr_upload", return_value=OcrResult(status="empty")):
            self.assertEqual(ocr_tool.extract_text_from_image(self.path), "")
        with mock.patch.object(ocr_tool, "ocr_upload", return_value=OcrResult(status="failed", error="E500")):
            with self.assertRaises(RuntimeError):
                ocr_tool.extract_text_from_image(self.path)


class ScriptCaution(unittest.TestCase):

    def test_scripts_the_readers_misread_get_a_caution(self):
        odia = combine({"ocr_text": "୧. ପୃଥିବୀର ଉପଗ୍ରହ କିଏ? (କ) ସୂର୍ଯ୍ୟ (ଖ) ଚନ୍ଦ୍ର"}, {"answer": "..."})
        self.assertTrue(odia["unreliable_script"])
        self.assertIn("check the question and options against the image", odia["tool_result"])

    def test_latin_text_numbers_and_no_text_get_none(self):
        for text in ["def average(marks): return total / len(marks)", "6 ÷ 2(1+2) =", ""]:
            with self.subTest(text=text):
                result = combine({"ocr_text": text}, {"answer": "..."})
                self.assertFalse(result["unreliable_script"])
                self.assertNotIn("misread", result["tool_result"])

    def test_ocr_wording_is_preferred_over_vision(self):
        self.assertIn("trust OCR TEXT", combine({"ocr_text": "x"}, {"answer": "y"})["tool_result"])


class ImageNode(unittest.TestCase):

    def analyze(self, ocr_error="", vision_error=""):
        result = combine({"ocr_text": "" if ocr_error else "text", "error": ocr_error},
                         {"answer": "" if vision_error else "a picture", "error": vision_error})
        with mock.patch.object(nodes, "analyze_image", return_value=result):
            return nodes.image_node({"query": "q", "image_path": "x.png"})

    def test_one_working_reader_is_enough(self):
        for kwargs in ({"vision_error": "429 limit"}, {"ocr_error": "timeout"}):
            with self.subTest(**kwargs):
                state = self.analyze(**kwargs)
                self.assertEqual(state["error"], "")
                self.assertIn("IMAGE ANALYSIS RESULT", state["tool_result"])

    def test_both_failing_is_an_error(self):
        self.assertIn("OCR error", self.analyze(ocr_error="timeout", vision_error="429")["error"])


if __name__ == "__main__":
    unittest.main()
