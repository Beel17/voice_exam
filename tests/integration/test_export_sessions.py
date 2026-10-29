"""Session transcripts for testers' feedback, rebuilt from a real session's event log."""

from pathlib import Path

import pytest
from tools.export_sessions import export

from tests.integration.test_session_manager import Harness


async def test_a_transcript_shows_what_was_said_understood_and_replied(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    await h.start()
    s = await h.login()
    await h.manager.handle_text(s, "go to question twelve", confidence=0.71)
    await h.manager.handle_text(s, "B")  # typed
    await h.manager.handle_key(s, "Enter")
    await h.store.close()

    files = await export(tmp_path, tmp_path / "feedback")
    (tmp_path / "sessions.db").unlink()  # the export let go of the database
    assert [f.name for f in files] == ["session-01.txt"]
    text = files[0].read_text(encoding="utf-8")
    question_12 = (await h.platform.get_question(s.state.exam_id, 12)).text
    for expected in (
        "CANDIDATE said (recognition score 0.71): 'go to question twelve'",
        "understood: go_to question=12  [rules",
        question_12,  # the question, read verbatim
        "CANDIDATE typed: 'B'",
        "CANDIDATE pressed Enter",
        "understood: confirm  [key",
    ):
        assert expected in text, expected
    assert text.index("Welcome") < text.index("go to question twelve")


async def test_no_sessions_yet(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no sessions yet"):
        await export(tmp_path, tmp_path / "feedback")
