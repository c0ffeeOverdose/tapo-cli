"""Terminal colour, aligned tables and a spinner.

Plain text when stdout is not a terminal, when NO_COLOR is set, or with
--color never.
"""

import os
import re
import sys
import time

RESET = "\x1b[0m"

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def visible_width(text):
    """Length of ``text`` without ANSI escapes, for column alignment."""
    return len(_ANSI.sub("", text))


def pad(text, width):
    return text + " " * max(0, width - visible_width(text))


class Style:
    """ANSI styling that can be switched off entirely."""

    def __init__(self, enabled=True):
        self.enabled = enabled

    def _wrap(self, name, text):
        if not self.enabled:
            return text
        return f"\x1b[{_CODES[name]}m{text}{RESET}"

    def bold(self, text):
        return self._wrap("bold", text)

    def dim(self, text):
        return self._wrap("dim", text)

    def red(self, text):
        return self._wrap("red", text)

    def green(self, text):
        return self._wrap("green", text)

    def yellow(self, text):
        return self._wrap("yellow", text)

    def blue(self, text):
        return self._wrap("blue", text)

    def magenta(self, text):
        return self._wrap("magenta", text)

    def cyan(self, text):
        return self._wrap("cyan", text)


style = Style(False)  # replaced by configure()


def _auto_color(stream):
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def configure(mode="auto", stream=None):
    """Set the module-wide style from ``auto``, ``always`` or ``never``."""
    global style
    stream = stream or sys.stdout
    enabled = {"always": True, "never": False}.get(mode, None)
    if enabled is None:
        enabled = _auto_color(stream)
    style = Style(enabled)
    return style


def table(headers, rows):
    """Borderless aligned table; cells may already contain styling."""
    widths = [
        max(visible_width(row[column]) for row in [headers] + rows)
        for column in range(len(headers))
    ]
    lines = [
        "  ".join(
            style.dim(pad(head, width))
            for head, width in zip(headers, widths)
        ).rstrip()
    ]
    for row in rows:
        lines.append(
            "  ".join(
                pad(cell, width) for cell, width in zip(row, widths)
            ).rstrip()
        )
    return "\n".join(lines)


def kv(pairs, indent="  "):
    """Aligned ``key   value`` lines for the info command."""
    width = max((visible_width(key) for key, _ in pairs), default=0)
    return "\n".join(
        f"{indent}{style.dim(pad(key, width))}  {value}"
        for key, value in pairs
    )


class Spinner:
    """Manual-tick spinner; silent unless the stream is a terminal.

    Ticks are throttled, so callers can tick as often as they like (the CLI
    sleeps in small slices while waiting for a device to confirm).
    """

    def __init__(self, message, stream=None, enabled=None):
        self.stream = stream or sys.stderr
        self.message = message
        self.enabled = (
            enabled
            if enabled is not None
            else hasattr(self.stream, "isatty") and self.stream.isatty()
        )
        self._started = time.monotonic()
        self._last = 0.0
        self._frame = 0

    def __enter__(self):
        self.tick()
        return self

    def __exit__(self, *exc_info):
        if self.enabled:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
        return False

    def tick(self):
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last < 0.08:
            return
        self._last = now
        elapsed = int(now - self._started)
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        self._frame += 1
        self.stream.write(
            f"\r\x1b[2K {style.cyan(frame)} {self.message} ({elapsed}s)"
        )
        self.stream.flush()
