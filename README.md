# tapo-cli

[![ci](https://github.com/c0ffeeOverdose/tapo-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/c0ffeeOverdose/tapo-cli/actions/workflows/ci.yml)

Control TP-Link Tapo devices from a terminal through the TP-Link cloud, the
same API the Tapo app uses when you are away from home.

Matter-era devices such as the P110M never join the legacy cloud relay that
tplink-cloud-api and tplc speak; that relay answers `-20571 "Device is
offline"` while the app still controls the device. This talks to the app
server cloud instead. The wire format, and the endpoints that do not work,
are written down in [docs/PROTOCOL.md](docs/PROTOCOL.md).

**Unofficial.** Not affiliated with or endorsed by TP-Link. It speaks an
undocumented API that TP-Link can change at any time. Use it on your own
account, at your own risk.

## Install

uv (recommended):

```bash
uv tool install git+https://github.com/c0ffeeOverdose/tapo-cli
uvx --from git+https://github.com/c0ffeeOverdose/tapo-cli tapo-cli list
```

pip:

```bash
pip install git+https://github.com/c0ffeeOverdose/tapo-cli
```

Nix:

```nix
inputs.tapo-cli.url = "github:c0ffeeOverdose/tapo-cli";
# ...
home.packages = [ inputs.tapo-cli.packages.${pkgs.system}.default ];
```

```bash
nix run github:c0ffeeOverdose/tapo-cli -- list
```

Docker:

```bash
docker build -t tapo-cli .
docker run --rm --user "$(id -u):$(id -g)" \
  -v ~/.config/tapo-cli:/home/tapo/.config/tapo-cli \
  tapo-cli list
```

With rootless podman pass `--userns=keep-id` instead of `--user`, otherwise
the container runs as another uid and cannot read your session file.

## Usage

```bash
tapo-cli auth                  # log in once, saves a token
tapo-cli list
tapo-cli status "Living Room Plug"
tapo-cli off "Living Room Plug"
```

`auth` stores the token under `~/.config/tapo-cli`. For scripts without a
terminal, set `TPLINK_USERNAME`/`TPLINK_PASSWORD` instead: the first command
logs in and saves a session, so they are only needed once. A password given
on the command line ends up in the shell history, so `auth` or the
environment is better for regular use. Accounts with two-factor
authentication are asked for the mailed code once.

### Commands

| Command | Description |
|---|---|
| `auth [status\|logout]` | log in, show or forget the saved token |
| `list` | devices, cloud status, live state |
| `status <name>` | on/off of one device |
| `info <name>` | ip, mac, firmware, ssid, signal |
| `on\|off\|toggle <name>` | switch it |

### Flags

| Flag | Description |
|---|---|
| `--json` | JSON output (the full token for `auth`) |
| `--exact` | match the device name exactly instead of a substring |
| `--wait SECONDS` | how long to wait for the device to confirm (default 20) |
| `--color auto\|always\|never` | colour output; `auto` is off when piped and honours `NO_COLOR` |
| `-u`, `-p` | credentials for one run, no session written |
| `-v` | print the raw cloud exchange |
| `--version` | version |

`auth` also takes `--show-token` to print the full token.

Flags work before or after the subcommand.

## Devices

| Device | Verified |
|---|---|
| P110M(US), firmware 1.4.3, Tapo app 3.20.753 | `list`, `status`, `on`, `off`, `toggle`, `info` |

Only that device has been tested. The code writes the `on` property of the
device shadow and reads it back, so other devices on the same cloud
(P100/P110/P115 plugs, S505 switches, L530 bulbs) should work for on/off, but
that is unverified. Devices without an `on` property (routers, hubs) appear
in `list` and cannot be switched. Brightness, colour and energy are not
implemented.

## Development

```bash
uv run pytest                     # 48 tests
uv run python -m tapo_cli list    # run from the checkout
tools/probe_thing_api.py          # read-only dump of the cloud's view
```

## License

Released under the [MIT license](LICENSE).
