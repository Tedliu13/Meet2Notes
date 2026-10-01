from __future__ import annotations

import pytest

from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.application.rag import PromptService


@pytest.mark.parametrize("native_bonsai", [False, True])
def test_followup_reaches_local_model_as_latest_user_turn(tmp_path, monkeypatch, native_bonsai):
    engine = LlamaCppSummaryEngine(tmp_path)
    requests = []
    class Model:
        def create_chat_completion(self, **kwargs):
            assert kwargs["repeat_penalty"] == (1.0 if native_bonsai else 1.1)
            requests.append(kwargs["messages"])
            return iter([
                {"choices": [{"delta": {"content": "PDF answer [A1]"}}]},
                {"choices": [], "usage": {"prompt_tokens": 100}},
            ])
    monkeypatch.setattr(engine, "_resolve_model_path", lambda *_args: tmp_path)
    monkeypatch.setattr(engine, "_get_model", lambda *_args: Model())
    if native_bonsai:
        monkeypatch.setattr("local_meeting_ai.adapters.summary.llama_cpp.BonsaiModel", Model)
    config = {
        "prompt_mode": True, "prompt_question": "What about the PDF?",
        "prompt_history": "USER: Who spoke?\nASSISTANT: Alex and Morgan",
        "prompt_turns": [
            {"role": "user", "content": "Who spoke?"},
            {"role": "assistant", "content": "Alex and Morgan"},
        ],
        "context_length": 4096, "system_prompt": "Meeting analyst",
    }
    try:
        result = engine._summarize_sync(
            "[A1] Transcript\nAlex: PDF export loses speaker names.", config,
            lambda progress, message: None, lambda: False,
        )
        assert result.content_markdown == "PDF answer [A1]"
        messages = requests[0]
        assert [item["role"] for item in messages] == ["system", "user", "assistant", "user"]
        assert messages[-1]["content"].endswith("CURRENT QUESTION:\nWhat about the PDF?")
        assert messages[1:3] == config["prompt_turns"]
        assert "RECENT CONVERSATION" not in messages[0]["content"]
        assert "PDF export loses speaker names" in messages[0]["content"]
        assert "PDF export loses speaker names" not in messages[-1]["content"]
        assert "Available source labels: [A1]" in messages[0]["content"]
    finally:
        engine.shutdown()


def test_history_budget_preserves_roles_and_drops_orphan_answers():
    history = [
        {"role": "user", "content": "old question " * 100},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "New question"},
        {"role": "assistant", "content": "New answer"},
    ]
    selected, tokens = PromptService._bounded_history(history, 50)
    assert selected == history[-2:]
    assert 0 < tokens <= 50
    assert history[0]["content"].startswith("old question")


def test_conversation_instructions_do_not_embed_old_questions_or_override_current_language():
    config = {
        "prompt_mode": True, "prompt_turns": [],
        "prompt_question": "¿Y el PDF?", "prompt_history": "Answer an older question",
    }
    instructions, _ = LlamaCppSummaryEngine._summary_instructions(config)
    messages = LlamaCppSummaryEngine._conversation_messages(
        "Write in transcript language", instructions, "[A2] Document", config,
    )
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"].endswith("CURRENT QUESTION:\n¿Y el PDF?")
    assert "Answer an older question" not in str(messages)
    assert "latest user message, in its language" in messages[0]["content"]
    assert "Available source labels: [A2]" in messages[0]["content"]


def test_live_assistant_legacy_prompt_mode_retains_its_structured_instructions():
    task, _ = LlamaCppSummaryEngine._summary_instructions({
        "prompt_mode": True, "prompt_question": 'Return JSON with a "text" field.',
        "prompt_history": "Earlier context",
    })
    assert "QUESTION:\nReturn JSON" in task
    assert "RECENT CONVERSATION:\nEarlier context" in task


def test_bonsai_document_prefix_is_stable_but_edits_invalidate_it():
    config = {
        "provider": "local", "profile_id": "bonsai-27b-1bit", "prompt_mode": True,
        "prompt_question": "Who spoke?", "prompt_turns": [],
    }
    evidence = "[A1] Transcript\nAlex: The PDF is broken."
    instructions, _ = LlamaCppSummaryEngine._summary_instructions(config)
    first = LlamaCppSummaryEngine._conversation_messages("Analyst", instructions, evidence, config)
    config.update(prompt_question="What about PDF?", prompt_turns=[
        {"role": "user", "content": "Who spoke?"},
        {"role": "assistant", "content": "Alex [A1]"},
    ])
    instructions, _ = LlamaCppSummaryEngine._summary_instructions(config)
    second = LlamaCppSummaryEngine._conversation_messages("Analyst", instructions, evidence, config)
    assert first[0] == second[0]
    assert evidence in second[0]["content"]
    assert second[-1]["content"] == "CURRENT QUESTION:\nWhat about PDF?"
    assert second[1:3] == config["prompt_turns"]
    changed = LlamaCppSummaryEngine._conversation_messages(
        "Analyst", instructions, "[A1] Different transcript", config,
    )
    assert changed[0] != second[0]
