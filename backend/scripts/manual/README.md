# Manual smoke scripts

Developer scripts that call OCR / vision / the LangGraph image route against a
local image. They are not part of the automated test suite (`backend/tests/`)
and need real API keys. Run from `backend/`, e.g.:

    python -m scripts.manual.test_ocr_agent

Edit the image path at the top of each script first.
