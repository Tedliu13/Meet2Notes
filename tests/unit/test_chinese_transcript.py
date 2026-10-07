# ruff: noqa: RUF001
# Chinese fixtures deliberately include full-width punctuation.
from local_meeting_ai.application.chinese_transcript import ChineseTranscriptConverter
from local_meeting_ai.domain.entities import SegmentDraft


def test_traditional_chinese_preserves_timings_and_original_model_text() -> None:
    original = SegmentDraft(
        index=3, start_ms=100, end_ms=2100, text="头发与发展，AI 2026。",
        confidence=0.9,
        metadata={"words": [{"word": "头发", "start": 0.1, "end": 0.8}], "custom": 7},
    )
    converted = ChineseTranscriptConverter("traditional").segment(original, "zh")
    assert converted.text == "頭髮與發展，AI 2026。"
    assert (converted.index, converted.start_ms, converted.end_ms, converted.confidence) == (
        3, 100, 2100, 0.9,
    )
    assert converted.metadata == {
        "words": [{"word": "頭髮", "start": 0.1, "end": 0.8}], "custom": 7,
        "transcription_original_text": original.text,
    }
    assert original.text == "头发与发展，AI 2026。"
    assert original.metadata["words"][0]["word"] == "头发"


def test_original_other_languages_and_translation_are_not_rewritten() -> None:
    segment = SegmentDraft(index=0, start_ms=0, end_ms=10, text="汉字 / 日本語 / AI")
    assert ChineseTranscriptConverter("original").segment(segment, "zh") is segment
    converter = ChineseTranscriptConverter("traditional")
    for language in ["ja", "en", None]:
        assert converter.segment(segment, language) is segment
    assert ChineseTranscriptConverter("traditional", task="translate").segment(
        segment, "zh",
    ) is segment


def test_taiwan_forms_preserve_vocabulary_without_regional_rephrasing() -> None:
    segment = SegmentDraft(index=0, start_ms=0, end_ms=10, text="软件，网络。")
    converted = ChineseTranscriptConverter("traditional").segment(segment, "zh-TW")
    assert converted.text == "軟件，網絡。"
