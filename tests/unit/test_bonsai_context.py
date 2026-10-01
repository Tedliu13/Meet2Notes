from __future__ import annotations

import pytest

from local_meeting_ai.adapters.summary import llama_cpp as adapter
from local_meeting_ai.domain.errors import CapabilityUnavailableError


def test_8b_runtime_clamps_oversized_context_and_uses_dense_cache_budget(tmp_path, monkeypatch):
    attempts = []
    budgets = []

    class Model:
        def __init__(self, directory, path, config):
            attempts.append(dict(config))
            if config.get("offload_kqv", True):
                raise adapter.BonsaiMemoryError("GPU allocation failed")

        def close(self):
            pass

    def fits(context, *, bytes_per_token):
        budgets.append((context, bytes_per_token))
        return True

    monkeypatch.setattr(adapter, "BonsaiModel", Model)
    monkeypatch.setattr(adapter, "host_cache_fits", fits)
    engine = adapter.LlamaCppSummaryEngine(tmp_path)
    try:
        engine._get_model(tmp_path / "model.gguf", {
            "profile_id": "bonsai-8b-1bit", "context_length": 262144,
        })
        assert [item["context_length"] for item in attempts] == [65536, 65536]
        assert budgets == [(65536, 49152)]
        assert attempts[-1]["offload_kqv"] is False
    finally:
        engine.shutdown()


def test_auto_context_reuses_larger_runtime_and_reloads_for_growth_or_settings(
    tmp_path, monkeypatch,
):
    launches = []

    class Model:
        def __init__(self, directory, path, config):
            launches.append(dict(config))
            self.closed = False

        def is_alive(self):
            return not self.closed

        def close(self):
            self.closed = True

    monkeypatch.setattr(adapter, "BonsaiModel", Model)
    engine = adapter.LlamaCppSummaryEngine(tmp_path)
    config = {"profile_id": "bonsai-27b-1bit", "context_length": 32768}
    try:
        first = engine._get_model(tmp_path / "model.gguf", config)
        smaller = engine._get_model(tmp_path / "model.gguf", {**config, "context_length": 8192})
        assert smaller is first
        larger = engine._get_model(tmp_path / "model.gguf", {**config, "context_length": 65536})
        assert larger is not first and first.closed
        manual = engine._get_model(tmp_path / "model.gguf", {
            **config, "context_length": 8192, "bonsai_auto_context": False,
        })
        assert manual is not larger and larger.closed
        assert [item["context_length"] for item in launches] == [32768, 65536, 8192]
    finally:
        engine.shutdown()


@pytest.mark.parametrize("enough_ram", [True, False])
def test_gpu_memory_failure_retries_host_cache_only_with_headroom(
    tmp_path, monkeypatch, enough_ram,
):
    attempts = []

    class Model:
        def __init__(self, directory, path, config):
            attempts.append(dict(config))
            if config.get("offload_kqv", True):
                raise adapter.BonsaiMemoryError("GPU allocation failed")

        def close(self):
            pass

    monkeypatch.setattr(adapter, "BonsaiModel", Model)
    monkeypatch.setattr(adapter, "host_cache_fits", lambda context: enough_ram)
    engine = adapter.LlamaCppSummaryEngine(tmp_path)
    config = {"profile_id": "bonsai-27b-1bit", "context_length": 131072}
    try:
        if enough_ram:
            engine._get_model(tmp_path / "model.gguf", config)
            assert len(attempts) == 2
            assert attempts[-1]["offload_kqv"] is False
            assert attempts[-1]["context_length"] == 131072
        else:
            with pytest.raises(CapabilityUnavailableError, match="No transcript text"):
                engine._get_model(tmp_path / "model.gguf", config)
            assert len(attempts) == 1
    finally:
        engine.shutdown()


@pytest.mark.parametrize("scope", [None, "speaker"])
def test_ai_notes_auto_size_before_loading_and_preserve_full_transcript(
    tmp_path, monkeypatch, scope,
):
    engine = adapter.LlamaCppSummaryEngine(tmp_path)
    loads = []
    prompts = []

    class Model:
        def create_chat_completion(self, **kwargs):
            prompts.append(kwargs["messages"])
            assert engine._fits_context(kwargs["messages"], kwargs["max_tokens"], loads[-1])
            return iter([{"choices": [{"delta": {"content": "# Complete AI notes"}}]}])

    def load(path, config):
        loads.append(config["context_length"])
        return Model()

    monkeypatch.setattr(engine, "_resolve_model_path", lambda *args: tmp_path)
    monkeypatch.setattr(engine, "_get_model", load)
    transcript = "[00:01] Alex: Important evidence.\n" * 2200 + "FINAL_DECISION"
    config = {
        "provider": "local", "profile_id": "bonsai-27b-1bit", "context_length": 8192,
        "max_output_tokens": 1024, "summary_scope": scope, "speaker_name": "Alex",
    }
    try:
        result = engine._summarize_sync(transcript, config, lambda *args: None, lambda: False)
        assert result.content_markdown == "# Complete AI notes"
        assert len(loads) == 1 and loads[0] > 8192
        assert len(prompts) == 1 and transcript in prompts[0][-1]["content"]
        assert config["context_length"] == 8192  # Per-request growth is not a saved preference.
    finally:
        engine.shutdown()


def test_hierarchical_boundary_accounts_for_the_actual_block_heading():
    engine = adapter.LlamaCppSummaryEngine
    block = "x" * 4900
    plain = engine._summary_messages("s", "t", block)
    partial = engine._summary_messages("s", "t", block, label="PARTIAL TRANSCRIPT")
    assert engine._fits_context(plain, 256, 2048)
    assert not engine._fits_context(partial, 256, 2048)
    blocks = engine._fit_transcript_blocks(
        [block], "s", "t", 256, 2048, None, label="PARTIAL TRANSCRIPT",
    )
    assert "".join(blocks) == block
    assert all(engine._fits_context(
        engine._summary_messages("s", "t", piece, label="PARTIAL TRANSCRIPT"), 256, 2048,
    ) for piece in blocks)


def test_summary_and_assistant_share_document_prefix(tmp_path, monkeypatch):
    from local_meeting_ai.domain.meeting_text import transcript_prefix

    engine = adapter.LlamaCppSummaryEngine(tmp_path)
    requests = []

    class Model:
        def create_chat_completion(self, **kwargs):
            requests.append(kwargs["messages"])
            return iter([{"choices": [{"delta": {"content": "Answer"}}]}])

    monkeypatch.setattr(engine, "_resolve_model_path", lambda *args: tmp_path)
    monkeypatch.setattr(engine, "_get_model", lambda *args: Model())
    text = "[0.0s] Alex: The PDF export needs fixing."
    config = {
        "provider": "local", "profile_id": "bonsai-27b-1bit", "context_length": 8192,
        "bonsai_document_prefix": transcript_prefix(47, text),
    }
    try:
        engine._summarize_sync(text, config, lambda *args: None, lambda: False)
        chat_config = {**config, "prompt_mode": True, "prompt_turns": [],
                       "prompt_question": "What about the PDF?"}
        engine._summarize_sync(text, chat_config, lambda *args: None, lambda: False)
        assert requests[0][0] == requests[1][0]
        assert text in requests[0][0]["content"]
        assert sum(message["content"].count(text) for message in requests[0]) == 1
        assert requests[0][-1] != requests[1][-1]
        chat_config["bonsai_document_prefix"] = transcript_prefix(47, text + " Edited.")
        engine._summarize_sync(text, chat_config, lambda *args: None, lambda: False)
        assert requests[2][0] != requests[1][0]
    finally:
        engine.shutdown()
