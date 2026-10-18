"""Unit tests for the on-disk session store."""

import json
import os
import stat

from tapo_cli import session


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "session.json"
    payload = session.save(
        "a@b.c", "https://h", "tok-123", "ref-456", "term", path=path
    )
    assert payload["token"] == "tok-123"
    loaded = session.load(path)
    assert loaded["email"] == "a@b.c"
    assert loaded["refresh_token"] == "ref-456"
    assert loaded["term_id"] == "term"
    assert loaded["version"] == session.VERSION


def test_saved_file_is_private(tmp_path):
    path = tmp_path / "session.json"
    session.save("a", "h", "t", None, "term", path=path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_load_returns_none_for_missing_or_broken_files(tmp_path):
    assert session.load(tmp_path / "nope.json") is None

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert session.load(broken) is None

    tokenless = tmp_path / "tokenless.json"
    tokenless.write_text(json.dumps({"email": "x"}), encoding="utf-8")
    assert session.load(tokenless) is None


def test_remove(tmp_path):
    path = tmp_path / "session.json"
    session.save("a", "h", "t", None, "term", path=path)
    assert session.remove(path) is True
    assert session.remove(path) is False


def test_preview_hides_the_middle_of_a_token():
    token = "a" * 32
    assert session.preview(token) == "aaaaaaaa…aaaa"
    assert session.preview("short") == "short"
    assert session.preview("") == ""
