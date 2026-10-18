"""The token minted by ``tapo-cli auth``.

Kept at ``~/.config/tapo-cli/session.json`` (mode 600): token, refresh
token and terminal id. Never a password.
"""

import json
import os
import pathlib
import time

VERSION = 1


def config_dir():
    root = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
    return pathlib.Path(root) / "tapo-cli"


def default_path():
    return config_dir() / "session.json"


def load(path=None):
    """Return the saved session, or None when there is no usable one."""
    path = pathlib.Path(path or default_path())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("token"):
        return None
    return data


def save(email, host, token, refresh_token, term_id, path=None):
    """Write the session atomically with mode 600."""
    path = pathlib.Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": VERSION,
        "email": email,
        "host": host,
        "token": token,
        "refresh_token": refresh_token,
        "term_id": term_id,
        "saved_at": int(time.time()),
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return payload


def remove(path=None):
    """Delete the session; True when a file was actually removed."""
    path = pathlib.Path(path or default_path())
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def preview(token, keep=8):
    """`aA1A1c…Qm9v`: recognisable without leaking the whole token."""
    if not token:
        return ""
    if len(token) <= keep * 2:
        return token
    return f"{token[:keep]}…{token[-4:]}"
