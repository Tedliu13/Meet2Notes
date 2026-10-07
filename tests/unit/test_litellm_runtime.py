from __future__ import annotations

import asyncio
import io
import logging
from types import ModuleType

import pytest

from local_meeting_ai.adapters import litellm_runtime
from local_meeting_ai.adapters.embeddings.litellm import LiteLLMEmbeddingProvider
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.domain.errors import CapabilityUnavailableError


class BrokenRedactionFilter(logging.Filter):
    __module__ = "litellm._logging"

    def filter(self, record: logging.LogRecord) -> bool:
        raise KeyError("litellm")


def test_failed_import_preserves_original_error_and_recovers_asyncio_logging(monkeypatch):
    logger = logging.getLogger("asyncio")
    previous_filters = logger.filters[:]
    previous_level = logger.level
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    original = ImportError("missing dependency")
    existing = logging.Filter()
    logger.addFilter(existing)

    def broken_import(name):
        assert name == "litellm"
        logger.addFilter(BrokenRedactionFilter())
        handler.addFilter(BrokenRedactionFilter())
        raise original

    monkeypatch.setattr(litellm_runtime.importlib, "import_module", broken_import)
    try:
        with pytest.raises(CapabilityUnavailableError, match="could not initialize") as caught:
            litellm_runtime.load_litellm()
        assert caught.value.__cause__ is original
        assert existing in logger.filters

        async def report_original_exception():
            try:
                raise RuntimeError("private transcript and api_key=secret")
            except RuntimeError as error:
                asyncio.get_running_loop().default_exception_handler(
                    {"message": "private message", "exception": error}
                )

        asyncio.run(report_original_exception())
        assert "exception type: RuntimeError" in output.getvalue()
        assert "report_original_exception" in output.getvalue()
        assert "secret" not in output.getvalue()
        assert "private" not in output.getvalue()
    finally:
        logger.filters[:] = previous_filters
        logger.setLevel(previous_level)
        logger.removeHandler(handler)
        handler.close()


def test_successful_import_keeps_redaction_filter(monkeypatch):
    logger = logging.getLogger("meet2notes.test.litellm_success")
    module = ModuleType("litellm")
    redaction = BrokenRedactionFilter()

    def successful_import(name):
        assert name == "litellm"
        logger.addFilter(redaction)
        return module

    monkeypatch.setattr(litellm_runtime.importlib, "import_module", successful_import)
    try:
        assert litellm_runtime.load_litellm() is module
        assert logger.filters == [redaction]
    finally:
        logger.removeFilter(redaction)


def test_missing_package_has_actionable_error(monkeypatch):
    def missing_import(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_runtime.importlib, "import_module", missing_import)
    with pytest.raises(CapabilityUnavailableError, match="ModuleNotFoundError") as caught:
        litellm_runtime.load_litellm()
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)


@pytest.mark.parametrize("operation", ["summary", "embedding"])
def test_adapters_propagate_initialization_failure_before_request(monkeypatch, tmp_path, operation):
    def broken_import(name):
        assert name == "litellm"
        raise ImportError("runtime dependency unavailable")

    monkeypatch.setattr(litellm_runtime.importlib, "import_module", broken_import)
    engine = LlamaCppSummaryEngine(tmp_path)
    try:
        with pytest.raises(
            CapabilityUnavailableError, match="LiteLLM could not initialize"
        ) as caught:
            if operation == "summary":
                engine._litellm_completion([], {"model": "openai/test"})
            else:
                LiteLLMEmbeddingProvider()._embed_sync(["test"], {"embedding_model": "openai/test"})
        assert isinstance(caught.value.__cause__, ImportError)
    finally:
        engine.shutdown()
