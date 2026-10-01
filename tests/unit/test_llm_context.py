from types import SimpleNamespace

import pytest

from local_meeting_ai.adapters.prompt_cache import cache_arguments
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.infrastructure.llm_context import discover_context, size_context


def test_remote_budget_grows_past_old_ceiling_but_manual_stays_fixed():
    config = {"provider": "litellm", "model": "openai/gpt-6-luna", "context_length": 8192}
    assert size_context(config, 600000) > 600000
    assert size_context({**config, "auto_context": False}, 600000) == 8192


@pytest.mark.parametrize("profile", ["lfm2.5-1.2b-q4", "qwen3-0.6b"])
def test_small_local_models_grow_only_to_trained_window(profile):
    config = {"provider": "local", "profile_id": profile, "context_length": 8192}
    assert size_context(config, 18000) == 32768
    assert size_context(config, 100000) == 32768


def test_ollama_uses_discovered_limit_and_does_not_assume_cloud_memory():
    config = {"provider": "litellm", "model": "ollama_chat/qwen3:8b",
              "context_length": 8192, "model_context_limit": 32768}
    assert size_context(config, 100000) == 32768


def test_ollama_metadata_discovery_does_not_send_transcript(monkeypatch):
    import httpx

    def post(url, *, json, timeout):
        assert url == "http://127.0.0.1:11434/api/show"
        assert json == {"model": "qwen3:8b"}
        return httpx.Response(200, json={"model_info": {"qwen3.context_length": 32768}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr("local_meeting_ai.infrastructure.llm_context.httpx.post", post)
    config = {"provider": "litellm", "model": "ollama_chat/qwen3:8b"}
    assert discover_context(config)["model_context_limit"] == 32768
    assert "model_context_limit" not in config


def test_provider_cache_markers_do_not_mutate_messages_or_apply_to_custom_gateways():
    sdk = SimpleNamespace(get_model_info=lambda **kw: {"supports_prompt_cache_breakpoint": True})
    messages = [{"role": "system", "content": "Fixed transcript"},
                {"role": "user", "content": "Question"}]
    base = {"messages": messages}
    claude = cache_arguments(sdk, {**base, "model": "anthropic/claude-sonnet-5-5"})
    assert claude["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    openai = cache_arguments(sdk, {**base, "model": "openai/gpt-6-luna"})
    assert openai["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert openai["prompt_cache_key"] == cache_arguments(sdk, {
        **base, "model": "openai/gpt-6-luna",
        "messages": [messages[0], {"role": "user", "content": "Second question"}],
    })["prompt_cache_key"]
    assert messages[0]["content"] == "Fixed transcript"
    for model, endpoint in [("gemini/gemini-3.8-flash", None),
                            ("openai/custom", "http://localhost:8000/v1")]:
        args = {**base, "model": model, "api_base": endpoint}
        assert cache_arguments(sdk, args) == args


def test_remote_notes_send_whole_text_once_and_share_prefix_with_followup(tmp_path, monkeypatch):
    engine = LlamaCppSummaryEngine(tmp_path)
    requests = []

    def complete(messages, config, **kwargs):
        requests.append(messages)
        return {"choices": [{"message": {"content": "Answer"}}]}

    monkeypatch.setattr(engine, "_litellm_completion", complete)
    text = "Long evidence. " * 70000 + "FINAL_DECISION"
    config = {"provider": "litellm", "model": "openai/gpt-6-luna", "context_length": 8192,
              "bonsai_document_prefix": text, "max_output_tokens": 20000}
    try:
        engine._summarize_sync(text, config, lambda *args: None, lambda: False)
        engine._summarize_sync(text, {**config, "prompt_mode": True, "prompt_turns": [],
                                     "prompt_question": "What was decided?"},
                               lambda *args: None, lambda: False)
        assert len(requests) == 2
        assert requests[0][0] == requests[1][0]
        assert text in requests[0][0]["content"]
        assert config["context_length"] == 8192
    finally:
        engine.shutdown()
