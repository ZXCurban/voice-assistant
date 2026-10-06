"""Fastpath templates: instant RU replies, no LLM (pure, no DB)."""

from app.ai.fastpath import match_fastpath


def test_greetings_match_case_insensitively() -> None:
    assert match_fastpath("привет") is not None
    assert match_fastpath("Здравствуйте!") is not None
    assert match_fastpath("  ДОБРЫЙ ВЕЧЕР... ") is not None
    assert "город" in (match_fastpath("привет") or "")


def test_who_and_help_templates() -> None:
    who = match_fastpath("кто ты?")
    assert who is not None and "консьерж" in who
    help_text = match_fastpath("что ты умеешь?")
    assert help_text is not None and "записать" in help_text
    assert match_fastpath("как записаться") is not None


def test_politeness_and_junk() -> None:
    assert match_fastpath("спасибо большое!") is not None
    assert match_fastpath("до свидания") is not None
    assert match_fastpath("???") is not None


def test_non_smalltalk_falls_through_to_llm() -> None:
    assert match_fastpath("у меня болит нога") is None
    assert match_fastpath("мне нужен дерматолог, ближайшее окно") is None
    assert match_fastpath("да") is None
    assert match_fastpath("нет, отмени") is None
    assert match_fastpath("") is None


def test_pending_confirmation_disables_fastpath() -> None:
    assert match_fastpath("привет", has_pending_confirmation=True) is None
    assert match_fastpath("спасибо", has_pending_confirmation=True) is None
    assert match_fastpath("привет", has_pending_confirmation=False) is not None


def test_e_normalization() -> None:
    assert match_fastpath("еще раз привет") is None  # not smalltalk anyway
    assert match_fastpath("здравствуйте") == match_fastpath("здравствуйте")
