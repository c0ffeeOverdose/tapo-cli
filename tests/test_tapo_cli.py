"""Unit tests: request signing, login flow, and the shadow-update logic."""

import base64
import hashlib
import hmac
import json
import re

import pytest

from tapo_cli import auth, cli


def b64(text):
    return base64.b64encode(text.encode()).decode()


class FakeCloud:
    """Stands in for the REST session: queued replies, recorded requests."""

    def __init__(self, shadows, replies):
        self._shadows = shadows
        self._replies = list(replies)
        self.patched = []

    def shadows(self, thing_names):
        return dict(self._shadows)

    def patch(self, path, body):
        self.patched.append((path, body))
        if len(self._replies) > 1:
            return self._replies.pop(0)
        return self._replies[0]  # the last reply repeats


def shadow(version, on):
    return {
        "thingName": "th1",
        "version": version,
        "state": {"desired": {}, "reported": {"on": on}},
    }


def make_auth(monkeypatch, replies, mfa_callback=None):
    sent = []

    def fake_post(self, host, path, body, token=None):
        sent.append((host, path, body, token))
        return replies.pop(0)

    monkeypatch.setattr(auth.CloudAuth, "_post", fake_post)
    session = auth.CloudAuth(
        "user@example.com", "hunter2", mfa_callback=mfa_callback
    )
    return session, sent


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(cli, "RETRY_DELAY_SECONDS", 0)


def test_content_md5_matches_the_documented_scheme():
    assert auth.content_md5("") == "1B2M2Y8AsgTpgAmY7PhCfg=="
    body = '{"a": 1}'
    expected = base64.b64encode(
        hashlib.md5(body.encode()).digest()
    ).decode()
    assert auth.content_md5(body) == expected


def test_signing_headers_sign_the_right_string():
    body = json.dumps({"hello": "world"})
    path = auth.LOGIN
    headers = auth.signing_headers(body, path)
    assert set(headers) == {"Content-MD5", "X-Authorization"}

    fields = dict(
        part.strip().split("=", 1)
        for part in headers["X-Authorization"].split(",")
    )
    assert fields["Timestamp"] == auth.SIGNING_TIMESTAMP
    assert fields["AccessKey"] == auth.ACCESS_KEY
    string_to_sign = (
        f"{headers['Content-MD5']}\n{auth.SIGNING_TIMESTAMP}\n"
        f"{fields['Nonce']}\n{path}"
    )
    expected = hmac.new(
        auth.SECRET_KEY.encode(), string_to_sign.encode(), hashlib.sha1
    ).hexdigest()
    assert fields["Signature"] == expected
    assert re.fullmatch(r"[0-9a-f]{40}", fields["Signature"])


def test_signing_headers_use_a_fresh_nonce():
    first = auth.signing_headers("{}", "/x")["X-Authorization"]
    second = auth.signing_headers("{}", "/x")["X-Authorization"]
    assert first != second


def test_login_follows_the_regional_url_and_keeps_the_token(monkeypatch):
    session, sent = make_auth(
        monkeypatch,
        [
            {"error_code": 0, "result": {"appServerUrl": "https://regional"}},
            {"error_code": 0, "result": {"token": "the-token"}},
        ],
    )
    assert session.token == "the-token"
    assert session.host == "https://regional"
    assert sent[0][0] == auth.REGIONAL_HOST
    assert sent[1][0] == "https://regional"
    assert sent[0][1] == auth.ACCOUNT_STATUS
    assert sent[0][2] == {
        "appType": auth.APP_TYPE, "cloudUserName": "user@example.com",
    }
    assert sent[1][2]["cloudPassword"] == "hunter2"
    assert sent[1][2]["cloudUserName"] == "user@example.com"
    assert sent[1][2]["terminalUUID"] == session.term_id


def test_login_keeps_the_default_host_when_the_status_call_fails(monkeypatch):
    session, sent = make_auth(
        monkeypatch,
        [
            {"error_code": -20600, "msg": "account not found"},
            {"error_code": 0, "result": {"token": "tok"}},
        ],
    )
    assert session.host == auth.REGIONAL_HOST
    assert session.token == "tok"


def test_wrong_credentials_raise_autherror(monkeypatch):
    with pytest.raises(auth.AuthError) as excinfo:
        make_auth(
            monkeypatch,
            [
                {"error_code": 0, "result": {}},
                {"error_code": -20601, "msg": "wrong username or password"},
            ],
        )
    assert excinfo.value.error_code == auth.ERR_WRONG_CREDENTIALS
    assert "wrong username" in str(excinfo.value)


def test_mfa_without_a_callback_raises(monkeypatch):
    with pytest.raises(auth.MFARequired) as excinfo:
        make_auth(
            monkeypatch,
            [
                {"error_code": 0, "result": {}},
                {"error_code": -20677, "result": {"mfaType": "verifyCodeLogin"}},
            ],
        )
    assert excinfo.value.mfa_type == "verifyCodeLogin"
    assert excinfo.value.email == "user@example.com"


def test_mfa_callback_is_used_and_its_code_verified(monkeypatch):
    asked = []

    def callback(mfa_type, email):
        asked.append((mfa_type, email))
        return "123456"

    session, sent = make_auth(
        monkeypatch,
        [
            {"error_code": 0, "result": {}},
            {"error_code": -20677, "result": {"mfaType": "verifyCodeLogin"}},
            {"error_code": 0, "result": {"token": "mfa-token"}},
        ],
        mfa_callback=callback,
    )
    assert session.token == "mfa-token"
    assert asked == [("verifyCodeLogin", "user@example.com")]
    assert sent[2][1] == auth.MFA_LOGIN
    assert sent[2][2]["code"] == "123456"


def test_rejected_mfa_code_raises(monkeypatch):
    with pytest.raises(auth.AuthError):
        make_auth(
            monkeypatch,
            [
                {"error_code": 0, "result": {}},
                {"error_code": -20677, "result": {}},
                {"error_code": -20678, "msg": "bad code"},
            ],
            mfa_callback=lambda *_: "000000",
        )


def test_get_app_server_url(monkeypatch):
    session, sent = make_auth(
        monkeypatch,
        [
            {"error_code": 0, "result": {"appServerUrl": "https://regional"}},
            {"error_code": 0, "result": {"token": "tok"}},
            {
                "error_code": 0,
                "result": {"serviceUrls": {"svc": "https://app-server"}},
            },
        ],
    )
    assert session.get_app_server_url("svc") == "https://app-server"
    assert sent[2][3] == "tok"  # the token is passed along


def test_get_app_server_url_reports_a_missing_service(monkeypatch):
    session, _ = make_auth(
        monkeypatch,
        [
            {"error_code": 0, "result": {}},
            {"error_code": 0, "result": {"token": "tok"}},
            {"error_code": 0, "result": {"serviceUrls": {}}},
        ],
    )
    with pytest.raises(auth.AuthError):
        session.get_app_server_url("svc")


def test_decode_name_decodes_base64_nickname():
    assert cli.decode_name(b64("Living Room Plug")) == "Living Room Plug"


def test_decode_name_keeps_plain_names():
    assert cli.decode_name("TL-MR100") == "TL-MR100"
    assert cli.decode_name("") == ""
    assert cli.decode_name(None) is None


def test_reported_state_reads_the_on_property():
    assert cli.reported_state(shadow(1, True)) is True
    assert cli.reported_state(shadow(1, False)) is False
    assert cli.reported_state({"state": {"reported": {}}}) is None
    assert cli.reported_state(None) is None


def test_format_state():
    assert cli.format_state(True) == "on"
    assert cli.format_state(False) == "off"
    assert cli.format_state(None) == "unknown"


def test_thing_name_falls_back_to_device_name():
    assert cli.thing_name({"nickname": b64("Plug")}) == "Plug"
    assert cli.thing_name({"deviceName": "P110M"}) == "P110M"


def test_is_online():
    assert cli.is_online({"status": 1}) is True
    assert cli.is_online({"status": 0}) is False
    assert cli.is_online({}) is False


def test_describe_hides_state_of_offline_devices():
    cloud = FakeCloud({}, [])
    thing = {"nickname": b64("Plug"), "model": "P110M", "status": 0}
    payload = cli.describe(cloud, thing, shadow(1, True))
    assert payload["online"] is False
    assert payload["state"] is None
    assert json.dumps(payload)  # JSON-serialisable


def test_patch_shadow_uses_the_matter_endpoint_and_bumps_version():
    cloud = FakeCloud({"th1": shadow(7, False)}, [{"version": 8}])
    reply = cli.patch_shadow(cloud, "th1", {"on": True})
    assert reply == {"version": 8}
    path, body = cloud.patched[0]
    assert path == "/v1/things/th1/matter/shadows"
    assert body == {"version": 8, "state": {"desired": {"on": True}}}


def test_patch_shadow_retries_a_stale_version():
    cloud = FakeCloud(
        {"th1": shadow(7, False)},
        [
            {"httpStatus": 400, "message": "smaller",
             "data": {"curVersion": 9}},
            {"version": 10},
        ],
    )
    assert cli.patch_shadow(cloud, "th1", {"on": True}) == {"version": 10}
    assert [body["version"] for _, body in cloud.patched] == [8, 10]


def test_patch_shadow_retries_trace_id_only_errors():
    trace = "[61a7ca38899d46c6b4e8a18b78b3c64a.5142.17923515085032515]"
    cloud = FakeCloud(
        {"th1": shadow(7, False)},
        [{"httpStatus": 500, "message": trace}, {"version": 8}],
    )
    assert cli.patch_shadow(cloud, "th1", {"on": True}) == {"version": 8}
    assert len(cloud.patched) == 2


def test_patch_shadow_falls_back_to_the_plain_shadow():
    cloud = FakeCloud(
        {"th1": shadow(7, False)},
        [{"httpStatus": 404, "message": "RESOURCE_NOT_FOUND"}],
    )
    with pytest.raises(SystemExit):
        cli.patch_shadow(cloud, "th1", {"on": True})
    paths = [path for path, _ in cloud.patched]
    assert paths[0] == "/v1/things/th1/matter/shadows"
    assert "/v1/things/th1/shadows" in paths


def test_patch_shadow_reports_when_every_attempt_fails():
    cloud = FakeCloud(
        {"th1": shadow(7, False)},
        [{"httpStatus": 500, "message": "[a.b.c]"}],
    )
    with pytest.raises(SystemExit):
        cli.patch_shadow(cloud, "th1", {"on": True})


def test_token_mode_does_not_log_in(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("token mode must not log in")

    monkeypatch.setattr(auth.CloudAuth, "_login", explode)
    session = auth.CloudAuth(
        token="tok", host="https://h", term_id="term", refresh_token="ref"
    )
    assert session.token == "tok"
    assert session.refresh_token == "ref"
    assert session.host == "https://h"


def test_refresh_without_a_refresh_token_returns_false():
    session = auth.CloudAuth(token="tok")
    assert session.refresh() is False


def test_refresh_updates_the_token(monkeypatch):
    replies = [
        {"error_code": 0, "result": {"token": "new", "refreshToken": "newref"}}
    ]
    seen = []

    def fake_post(self, host, path, body, token=None):
        seen.append((path, body))
        return replies.pop(0)

    monkeypatch.setattr(auth.CloudAuth, "_post", fake_post)
    session = auth.CloudAuth(
        token="old", refresh_token="ref", host="https://h"
    )
    assert session.refresh() is True
    assert session.token == "new"
    assert session.refresh_token == "newref"
    assert seen[0][0] == auth.REFRESH_TOKEN
    assert seen[0][1]["refreshToken"] == "ref"


def test_refresh_failure_returns_false(monkeypatch):
    monkeypatch.setattr(
        auth.CloudAuth, "_post", lambda *a, **k: {"error_code": -20655}
    )
    session = auth.CloudAuth(token="old", refresh_token="ref")
    assert session.refresh() is False


def test_auth_subcommand_defaults_to_login():
    args = cli.build_parser().parse_args(["auth"])
    assert args.command == "auth"
    assert args.action == "login"
    assert args.show_token is False


def test_auth_actions_parse():
    for action in ("status", "logout"):
        args = cli.build_parser().parse_args(["auth", action])
        assert args.action == action


def test_auth_show_token_parses():
    args = cli.build_parser().parse_args(["auth", "login", "--show-token"])
    assert args.show_token is True


def test_save_credentials_is_gone():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["auth", "login", "--save-credentials"])


def test_python_m_entry_point_is_importable():
    from tapo_cli import __main__ as entry

    assert callable(entry.main)
