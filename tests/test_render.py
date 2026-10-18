"""Unit tests for the terminal rendering helpers."""

import pytest

from tapo_cli import render


@pytest.fixture(autouse=True)
def restore_style():
    original = render.style
    yield
    render.style = original


class Stream:
    def __init__(self, tty=True):
        self.tty = tty
        self.parts = []

    def isatty(self):
        return self.tty

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass


def test_visible_width_ignores_ansi():
    assert render.visible_width("\x1b[32m● on\x1b[0m") == 4
    assert render.visible_width("plain") == 5


def test_style_disabled_is_plain():
    style = render.Style(enabled=False)
    assert style.green("on") == "on"
    assert style.bold("x") == "x"


def test_style_enabled_wraps_in_sgr():
    style = render.Style(enabled=True)
    assert style.green("on") == "\x1b[32mon\x1b[0m"
    assert style.dim("x").startswith("\x1b[2m")


def test_pad_uses_visible_width():
    assert render.pad("\x1b[32mon\x1b[0m", 4) == "\x1b[32mon\x1b[0m  "
    assert render.pad("abcd", 2) == "abcd"


def test_table_aligns_styled_cells(monkeypatch):
    monkeypatch.setattr(render, "style", render.Style(False))
    out = render.table(
        ("DEVICE", "STATE"),
        [("Plug", "on"), ("A very long name", "off")],
    )
    lines = out.splitlines()
    assert lines[0].startswith("DEVICE")
    assert lines[1].index("on") == lines[2].index("off")


def test_kv_aligns_keys(monkeypatch):
    monkeypatch.setattr(render, "style", render.Style(False))
    out = render.kv([("ip", "1.2.3.4"), ("firmware", "1.0")])
    assert out == "  ip        1.2.3.4\n  firmware  1.0"


def test_configure_never_is_plain():
    assert render.configure("never").enabled is False


def test_configure_auto_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert render.configure("auto", stream=Stream(tty=True)).enabled is False


def test_configure_auto_respects_dumb_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert render.configure("auto", stream=Stream(tty=True)).enabled is False


def test_configure_auto_on_a_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert render.configure("auto", stream=Stream(tty=True)).enabled is True


def test_spinner_is_silent_without_a_terminal():
    stream = Stream(tty=False)
    with render.Spinner("working", stream=stream):
        pass
    assert stream.parts == []


def test_spinner_writes_and_clears_on_a_terminal(monkeypatch):
    monkeypatch.setattr(render, "style", render.Style(False))
    stream = Stream(tty=True)
    with render.Spinner("working", stream=stream) as spinner:
        spinner.tick()
    text = "".join(stream.parts)
    assert "working" in text
    assert text.endswith("\r\x1b[2K")
