"""tapo-cli - control Tapo devices through TP-Link's new cloud.

Matter-era devices such as the P110M are not on the legacy relay that
tplink-cloud-api speaks; they live on the app-server cloud the Tapo app
uses, which this talks to. Endpoints: docs/PROTOCOL.md.

Auth, in order: --username/--password, a session from `tapo-cli auth`,
TPLINK_USERNAME/TPLINK_PASSWORD, then ~/.config/tapo-cli/credentials.env.
"""

import argparse
import base64
import getpass
import json
import os
import pathlib
import re
import sys
import time

import requests

from . import __version__, auth, render, session

RETRY_DELAY_SECONDS = 2  # tests set this to 0
POLL_SECONDS = 2

APP_NAME = auth.APP_NAME
APP_VERSION = auth.APP_SERVER_VERSION
APP_SERVICE = "nbu.iot-app-server.app-v2"

CONFIG_ROOT = pathlib.Path(
    os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")
)
CONFIG_DIR = CONFIG_ROOT / "tapo-cli"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.env"
# fallback: the pre-1.0 location next to the legacy CLI
LEGACY_CREDENTIALS_FILE = CONFIG_ROOT / "kasa-cloud" / "credentials.env"


def die(message, code=1):
    print(f"tapo-cli: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_credentials_file(path=None):
    values = {}
    path = path or CREDENTIALS_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve_credentials(args):
    stored = load_credentials_file()
    if not stored and LEGACY_CREDENTIALS_FILE.exists():
        stored = load_credentials_file(LEGACY_CREDENTIALS_FILE)
    username = (
        args.username
        or os.environ.get("TPLINK_USERNAME")
        or stored.get("TPLINK_USERNAME")
    )
    password = (
        args.password
        or os.environ.get("TPLINK_PASSWORD")
        or stored.get("TPLINK_PASSWORD")
    )
    if not username or not password:
        die(
            "no session and no credentials. Run `tapo-cli auth` to log "
            "in once, or export TPLINK_USERNAME/TPLINK_PASSWORD, or "
            f"create {CREDENTIALS_FILE} (chmod 600) with lines "
            "TPLINK_USERNAME=... and TPLINK_PASSWORD=..."
        )
    return username, password


def handle_mfa(mfa_type, email):
    if not sys.stdin.isatty():
        die(
            f"MFA required ({mfa_type}) for {email}; run tapo-cli from "
            "an interactive terminal to enter the code"
        )
    return input(f"TP-Link MFA code sent to {email}: ").strip()


def decode_name(value):
    """Tapo reports nicknames base64-encoded; decode when that fits."""
    if not value or len(value) < 8 or len(value) % 4:
        return value
    try:
        text = base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value
    return text if text.isprintable() and text.strip() else value


class Cloud:
    """A logged-in Tapo app-server session."""

    def __init__(self, auth_obj, email=None):
        self.auth = auth_obj
        self.email = email
        self.token = auth_obj.token
        self.term_id = auth_obj.term_id
        self.base = self._app_server()
        self.session = requests.Session()
        self.verify = auth.ca_bundle()

    @property
    def headers(self):
        return {
            "app-cid": f"app:{APP_NAME}:{self.term_id}",
            "x-app-name": APP_NAME,
            "x-app-version": APP_VERSION,
            "x-term-id": self.term_id,
            "x-ospf": "Android 14",
            "x-net-type": "wifi",
            "x-strict": "0",
            "x-locale": "en_US",
            "Authorization": f"ut|{self.token}",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}(Pixel/;Android 14)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _app_server(self):
        try:
            return self.auth.get_app_server_url(APP_SERVICE)
        except auth.AuthError as exc:
            if exc.error_code != auth.ERR_TOKEN_EXPIRED or not self.refresh():
                raise
            return self.auth.get_app_server_url(APP_SERVICE)

    def refresh(self):
        """Extend the session; persists the rotated token when possible."""
        if not self.auth.refresh():
            return False
        self.token = self.auth.token
        self.save_session()
        return True

    def save_session(self):
        if not self.email:
            return False
        try:
            session.save(
                self.email,
                self.auth.host,
                self.auth.token,
                self.auth.refresh_token,
                self.term_id,
            )
        except OSError:
            return False
        return True

    def _request(self, method, path, params=None, body=None, timeout=30):
        payload = None
        for attempt in range(2):
            response = self.session.request(
                method,
                self.base + path,
                headers=self.headers,
                params=params,
                data=None if body is None else json.dumps(body),
                verify=self.verify,
                timeout=timeout,
            )
            payload = self._json(response)
            code = payload.get("code") if isinstance(payload, dict) else None
            expired = (
                response.status_code in (401, 403)
                or code in (401, 403, auth.ERR_TOKEN_EXPIRED)
            )
            if expired and attempt == 0 and self.refresh():
                continue
            return payload
        return payload

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def patch(self, path, body):
        return self._request("PATCH", path, body=body, timeout=60)

    def _json(self, response):
        try:
            payload = response.json()
        except ValueError:
            die(f"unexpected reply {response.status_code}: "
                f"{response.text[:200]}")
        if response.status_code >= 400 and "message" in payload:
            payload["httpStatus"] = response.status_code
        return payload

    def things(self):
        data = self.get(
            "/v2/things",
            {
                "page": 0,
                "pageSize": 100,
                "includePcDevice": "true",
                "includeKasaShareDevices": "true",
                "includeMatterDevice": "true",
            },
        )
        return data.get("data", []) if isinstance(data, dict) else []

    def shadows(self, thing_names):
        """Live state, from the Matter shadow when the device has one."""
        names = list(thing_names)
        for path in ("/v1/things/matter/shadows", "/v1/things/shadows"):
            data = self.get(path, {"thingNames": names})
            entries = data.get("shadows") if isinstance(data, dict) else None
            if entries:
                return {entry["thingName"]: entry for entry in entries}
        return {}


def thing_name(thing):
    return decode_name(thing.get("nickname")) or thing.get("deviceName") or "?"


def is_online(thing):
    return thing.get("status") == 1


def reported_state(shadow):
    if not shadow:
        return None
    state = shadow.get("state") or {}
    reported = state.get("reported") or {}
    value = reported.get("on")
    return value if isinstance(value, bool) else None


def format_state(state):
    if state is None:
        return "unknown"
    return "on" if state else "off"


def state_badge(payload):
    """A coloured `● on` / `○ off` / `· offline` fragment."""
    if not payload["online"]:
        return render.style.red("· offline")
    if payload["state"] == "on":
        return render.style.green("● on")
    if payload["state"] == "off":
        return render.style.dim("○ off")
    return render.style.yellow("· unknown")


def find_thing(cloud, name, exact=False):
    things = cloud.things()
    if not things:
        die("no devices found in this account")
    if exact:
        matches = [t for t in things if thing_name(t) == name]
    else:
        needle = name.lower()
        matches = [
            t for t in things
            if needle in thing_name(t).lower()
            or needle in (t.get("thingName") or "").lower()
        ]
    if not matches:
        die(f"no device matching {name!r} (try: tapo-cli list)")
    if len(matches) > 1:
        names = ", ".join(sorted(thing_name(t) for t in matches))
        die(f"{name!r} matches {len(matches)} devices: {names} (use --exact)")
    return matches[0]


def describe(cloud, thing, shadow):
    online = is_online(thing)
    state = reported_state(shadow) if online else None
    return {
        "name": thing_name(thing),
        "model": thing.get("model"),
        "category": thing.get("category"),
        "cloud": "tapo",
        "online": online,
        "state": None if state is None else ("on" if state else "off"),
        "thingName": thing.get("thingName"),
    }


def cmd_list(cloud, args):
    things = cloud.things()
    shadows = cloud.shadows([t.get("thingName") for t in things])
    rows = [
        describe(cloud, thing, shadows.get(thing.get("thingName")))
        for thing in things
    ]
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print("no devices found in this account")
        return
    print(
        render.table(
            ("DEVICE", "MODEL", "STATE"),
            [
                (
                    render.style.bold(row["name"]),
                    render.style.dim(row["model"] or "?"),
                    state_badge(row),
                )
                for row in rows
            ],
        )
    )


def cmd_status(cloud, args):
    thing = find_thing(cloud, args.name, args.exact)
    shadow = cloud.shadows([thing["thingName"]]).get(thing["thingName"])
    payload = describe(cloud, thing, shadow)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    model = payload["model"] or "?"
    print(
        f"{state_badge(payload)}  {render.style.bold(payload['name'])}"
        f"  {render.style.dim('(' + model + ')')}"
    )


def cmd_info(cloud, args):
    thing = find_thing(cloud, args.name, args.exact)
    name = thing["thingName"]
    detail = cloud.get(f"/v1/things/{name}/details")
    realtime = cloud.get(f"/v1/things/{name}/rt-info")
    payload = {
        "name": thing_name(thing),
        "thingName": name,
        "model": thing.get("model"),
        "online": is_online(thing),
        "details": detail,
        "realtime": realtime,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    pairs = [
        ("thingName", name),
        ("online", "yes" if payload["online"] else "no"),
    ]
    for key, label in (("ip", "ip"), ("mac", "mac"), ("fwVer", "firmware")):
        if key in detail:
            pairs.append((label, detail[key]))
    if "ssid" in detail:
        pairs.append(("ssid", decode_name(detail["ssid"])))
    signal = realtime.get("signalLevel")
    rssi = realtime.get("rssi")
    parts = []
    if signal is not None:
        parts.append(str(signal))
    if rssi is not None:
        parts.append(f"rssi {rssi}")
    if parts:
        pairs.append(("signal", "  ".join(parts)))
    model = payload["model"] or "?"
    title = f"{payload['name']} {render.style.dim('(' + model + ')')}"
    print(render.style.bold(title))
    print(render.kv(pairs))


# a 4xx/5xx whose message is only a trace id means the server hiccupped
TRACE_ONLY = re.compile(r"^\[[0-9a-f]{32}\.\d+\.\d+\]")


def patch_shadow(cloud, name, desired, attempts=4):
    """Set the shadow's desired state; retries transient cloud failures."""
    paths = (
        f"/v1/things/{name}/matter/shadows",
        f"/v1/things/{name}/shadows",
    )
    last = None
    for path in paths:
        version = None
        for attempt in range(attempts):
            if version is None:
                shadow = cloud.shadows([name]).get(name) or {}
                version = shadow.get("version", 0) + 1
            reply = cloud.patch(
                path, {"version": version, "state": {"desired": desired}}
            )
            if not reply.get("httpStatus"):
                return reply
            message = str(reply.get("message") or "")
            last = message or str(reply)
            current = (reply.get("data") or {}).get("curVersion")
            if current is not None:
                # the cloud tells us which version to beat
                version = current + 1
            elif reply["httpStatus"] >= 500 or TRACE_ONLY.match(message):
                version = None  # transient: re-read, then try again
            else:
                break  # hard error here: try the other endpoint
            time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    die(f"cloud refused the shadow update: {last or 'unknown error'}")


def set_switch(cloud, thing, on, timeout):
    name = thing["thingName"]
    if cloud.shadows([name]).get(name) is None:
        die(f"no shadow for {thing_name(thing)!r}")
    reply = patch_shadow(cloud, name, {"on": bool(on)})
    deadline = time.time() + timeout
    state = None
    label = "switching on" if on else "switching off"
    with render.Spinner(f"{label} - waiting for the device") as spinner:
        while True:
            refreshed = cloud.shadows([name]).get(name)
            state = reported_state(refreshed)
            if state == bool(on) or time.time() >= deadline:
                break
            for _ in range(max(1, int(POLL_SECONDS / 0.1))):
                spinner.tick()
                time.sleep(0.1)
    if state != bool(on):
        print(
            "tapo-cli: warning: request sent (requestId "
            f"{reply.get('requestId')}) but the device has not "
            f"confirmed yet ({format_state(state)})",
            file=sys.stderr,
        )
    return state, reply


def cmd_power(cloud, args):
    thing = find_thing(cloud, args.name, args.exact)
    if args.command == "toggle":
        shadow = cloud.shadows([thing["thingName"]]).get(thing["thingName"])
        current = reported_state(shadow)
        if current is None:
            die(f"cannot read the power state of {thing_name(thing)!r}")
        target = not current
    else:
        target = args.command == "on"
    state, reply = set_switch(cloud, thing, target, args.wait)
    payload = describe(
        cloud,
        thing,
        cloud.shadows([thing["thingName"]]).get(thing["thingName"]),
    )
    if args.json:
        print(json.dumps({**payload, "requestId": reply.get("requestId")},
                         indent=2, ensure_ascii=False))
        return
    print(f"{state_badge(payload)}  {render.style.bold(payload['name'])}")


def open_cloud(args):
    """A Cloud session: saved token first, otherwise log in and remember it."""
    stored = session.load()
    if stored and not args.username and not args.password:
        auth_obj = auth.CloudAuth(
            token=stored.get("token"),
            host=stored.get("host"),
            term_id=stored.get("term_id"),
            refresh_token=stored.get("refresh_token"),
            verbose=args.verbose,
        )
        return Cloud(auth_obj, email=stored.get("email"))
    username, password = resolve_credentials(args)
    auth_obj = auth.CloudAuth(
        username, password, mfa_callback=handle_mfa, verbose=args.verbose
    )
    cloud = Cloud(auth_obj, email=username)
    if not (args.username or args.password):
        cloud.save_session()
    return cloud


def cmd_auth(args):
    """`auth` / `auth status` / `auth logout` - mint and manage a token."""
    action = args.action or "login"

    if action == "logout":
        if session.remove():
            print(f"{render.style.green('✓')} session removed")
        else:
            print("no session to remove")
        return

    if action == "status":
        stored = session.load()
        if not stored:
            print("not logged in - run: tapo-cli auth")
            raise SystemExit(1)
        show = args.show_token or args.json
        payload = {
            "email": stored.get("email"),
            "host": stored.get("host"),
            "token": stored.get("token") if show
            else session.preview(stored.get("token")),
            "saved_at": stored.get("saved_at"),
            "path": str(session.default_path()),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        saved = time.strftime(
            "%Y-%m-%d %H:%M %Z", time.localtime(payload["saved_at"] or 0)
        )
        print(
            f"{render.style.green('✓')} "
            f"{render.style.bold(payload['email'] or 'unknown account')}"
        )
        print(
            render.kv(
                [
                    ("token", payload["token"]),
                    ("saved", saved),
                    ("host", payload["host"] or "?"),
                    ("file", payload["path"]),
                ]
            )
        )
        return

    username = args.username or input("TP-Link account: ").strip()
    if not username:
        die("no account given")
    password = args.password or getpass.getpass("Password: ")
    if not password:
        die("no password given")
    auth_obj = auth.CloudAuth(
        username, password, mfa_callback=handle_mfa, verbose=args.verbose
    )
    session.save(
        username,
        auth_obj.host,
        auth_obj.token,
        auth_obj.refresh_token,
        auth_obj.term_id,
    )
    if args.save_credentials:
        path = CREDENTIALS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"TPLINK_USERNAME={username}\nTPLINK_PASSWORD={password}\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    token = auth_obj.token if (args.show_token or args.json) \
        else session.preview(auth_obj.token)
    payload = {
        "email": username,
        "token": token,
        "host": auth_obj.host,
        "path": str(session.default_path()),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"{render.style.green('✓')} logged in as {render.style.bold(username)}")
    print(
        render.kv(
            [
                ("token", token),
                ("saved", payload["path"]),
                ("host", auth_obj.host),
            ]
        )
    )


def _common_flags():
    """Flags accepted both before and after the subcommand.

    The subcommand copies use SUPPRESS defaults so that a value given before
    the subcommand is not overwritten when the subparser fills in defaults.
    """
    common = argparse.ArgumentParser(add_help=False)
    for args, kwargs in (
        (("-u", "--username"), {"help": "TP-Link account email"}),
        (("-p", "--password"), {"help": "TP-Link account password"}),
        (("--json",), {"action": "store_true", "help": "JSON output"}),
        (
            ("-v", "--verbose"),
            {
                "action": "store_true",
                "help": "print the raw cloud requests/responses (debugging)",
            },
        ),
        (
            ("--color",),
            {
                "choices": ("auto", "always", "never"),
                "help": "colour output (auto, off when not a terminal)",
            },
        ),
        (
            ("--exact",),
            {"action": "store_true", "help": "match the alias exactly"},
        ),
        (
            ("--wait",),
            {
                "type": int,
                "metavar": "SECONDS",
                "help": "how long to wait for the device to confirm",
            },
        ),
    ):
        common.add_argument(*args, default=argparse.SUPPRESS, **kwargs)
    return common


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tapo-cli",
        description=(
            "Control Tapo (Matter-era) devices through TP-Link's new "
            "cloud API - works from anywhere, no LAN needed"
        ),
    )
    parser.add_argument("-u", "--username", help="TP-Link account email")
    parser.add_argument("-p", "--password", help="TP-Link account password")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="print the raw cloud requests/responses (debugging)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour output (default: auto, off when not a terminal)",
    )
    parser.add_argument(
        "--exact", action="store_true", help="match the device name exactly"
    )
    parser.add_argument(
        "--wait", type=int, default=20, metavar="SECONDS",
        help="how long to wait for the device to confirm (default 20)",
    )
    common = _common_flags()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "list", parents=[common], help="list devices in the account"
    )

    p_auth = sub.add_parser(
        "auth",
        parents=[common],
        help="log in and save a token, or inspect/forget it",
    )
    p_auth.add_argument(
        "action",
        nargs="?",
        default="login",
        choices=("login", "status", "logout"),
        help="login (default), status or logout",
    )
    p_auth.add_argument(
        "--save-credentials",
        action="store_true",
        help="also write ~/.config/tapo-cli/credentials.env (mode 600)",
    )
    p_auth.add_argument(
        "--show-token",
        action="store_true",
        help="print the full token instead of a preview",
    )
    for name, help_text in (
        ("status", "show the on/off state of a device"),
        ("info", "show connection details (ip, mac, firmware, rssi)"),
        ("on", "turn a device on"),
        ("off", "turn a device off"),
        ("toggle", "flip a device on/off"),
    ):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("name", help="device name (or part of it)")
    return parser


def main():
    args = build_parser().parse_args()
    render.configure(args.color)

    if args.command == "auth":
        try:
            cmd_auth(args)
        except KeyboardInterrupt:
            die("interrupted", 130)
        except auth.AuthError as exc:
            die(f"login failed: {exc}")
        return

    try:
        cloud = open_cloud(args)
    except auth.MFARequired as exc:
        die(str(exc))
    except auth.AuthError as exc:
        if exc.error_code in (
            auth.ERR_TOKEN_EXPIRED,
            auth.ERR_REFRESH_TOKEN_EXPIRED,
        ):
            die("the saved session expired - run: tapo-cli auth")
        die(f"login failed: {exc}")
    except SystemExit:
        raise
    except Exception as exc:
        die(f"login failed: {exc}")
    handlers = {
        "list": cmd_list,
        "status": cmd_status,
        "info": cmd_info,
        "on": cmd_power,
        "off": cmd_power,
        "toggle": cmd_power,
    }
    try:
        handlers[args.command](cloud, args)
    except KeyboardInterrupt:
        die("interrupted", 130)
    except SystemExit:
        raise
    except Exception as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
