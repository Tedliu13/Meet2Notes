from types import SimpleNamespace

import pytest

from local_meeting_ai.adapters.summary import llama_cpp
from local_meeting_ai.domain.errors import CapabilityUnavailableError


@pytest.fixture
def remote_engine(tmp_path, monkeypatch):
    calls = []
    response = {"choices": [{"message": {"content": "摘要"}, "finish_reason": "stop"}]}

    def complete(**arguments):
        calls.append(arguments)
        return response

    module = SimpleNamespace(
        completion=complete,
        get_supported_openai_params=lambda **kwargs: ["max_completion_tokens"],
    )
    monkeypatch.setattr(llama_cpp, "load_litellm", lambda: module)
    engine = llama_cpp.LlamaCppSummaryEngine(tmp_path)
    yield engine, calls, module
    engine.shutdown()


@pytest.mark.parametrize("model", ["openai/gpt-6-luna", "openai/gpt-6-luna-2026-10-01"])
def test_luna_defaults_to_no_reasoning_and_keeps_output_budget(remote_engine, model):
    engine, calls, _ = remote_engine
    result = engine._litellm_completion(
        [], {"model": model, "api_key": "", "max_output_tokens": 2048},
        maximum_tokens=512,
    )
    assert calls[0]["reasoning_effort"] == "none"
    assert calls[0]["max_completion_tokens"] == 512
    assert "max_tokens" not in calls[0]
    assert "temperature" not in calls[0]
    assert "top_p" not in calls[0]
    assert engine._completion_content(result)[0] == "摘要"


def test_explicit_luna_reasoning_is_preserved(remote_engine):
    engine, calls, _ = remote_engine
    engine._litellm_completion(
        [], {"model": "openai/gpt-6-luna", "api_key": "", "reasoning_effort": "low"},
    )
    assert calls[0]["reasoning_effort"] == "low"


def test_other_models_do_not_receive_luna_reasoning_default(remote_engine):
    engine, calls, module = remote_engine
    module.get_supported_openai_params = lambda **kwargs: ["max_tokens", "temperature", "top_p"]
    engine._litellm_completion([], {"model": "openai/gpt-4.1-mini", "api_key": ""})
    assert "reasoning_effort" not in calls[0]
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["max_tokens"] == 1024


def test_empty_reasoning_only_output_reports_limit_without_replaying(remote_engine, caplog):
    engine, calls, module = remote_engine
    module.completion = lambda **kwargs: calls.append(kwargs) or {
        "choices": [{"finish_reason": "length", "message": {
            "content": None, "reasoning_content": "PRIVATE REASONING",
        }}],
        "usage": {"completion_tokens": 1024,
                  "completion_tokens_details": {"reasoning_tokens": 1024}},
    }
    with caplog.at_level("INFO"):
        result = engine._litellm_completion([], {"model": "openai/gpt-6-luna", "api_key": ""})
        with pytest.raises(CapabilityUnavailableError, match="output token limit") as error:
            engine._completion_content(result)
    assert len(calls) == 1
    assert "reasoning_tokens=1024" in str(error.value)
    assert "finish_reason=length" in caplog.text
    assert "PRIVATE REASONING" not in caplog.text + str(error.value)


def test_streaming_preserves_finish_reason_and_usage(remote_engine):
    engine, calls, module = remote_engine

    def complete(**arguments):
        calls.append(arguments)
        return iter([
            {"choices": [{"delta": {"content": None}, "finish_reason": "length"}]},
            {"choices": [], "usage": {"completion_tokens": 512,
              "completion_tokens_details": {"reasoning_tokens": 512}}},
        ])

    module.completion = complete
    result = engine._litellm_completion(
        [], {"model": "openai/gpt-6-luna", "api_key": ""}, on_token=lambda text: None,
    )
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert result["choices"][0]["finish_reason"] == "length"
    with pytest.raises(CapabilityUnavailableError, match="reasoning_tokens=512"):
        engine._completion_content(result)


@pytest.mark.parametrize("result", [
    {"choices": []},
    {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]},
])
def test_empty_or_whitespace_output_is_not_a_success(result):
    with pytest.raises(CapabilityUnavailableError, match="empty summary"):
        llama_cpp.LlamaCppSummaryEngine._completion_content(result)


def test_refusal_does_not_expose_provider_text(caplog):
    result = {"choices": [{"message": {"content": None, "refusal": "PRIVATE TEXT"}}]}
    with caplog.at_level("INFO"), pytest.raises(
        CapabilityUnavailableError, match="declined",
    ) as error:
        llama_cpp.LlamaCppSummaryEngine._completion_content(result)
    assert "PRIVATE TEXT" not in caplog.text + str(error.value)
