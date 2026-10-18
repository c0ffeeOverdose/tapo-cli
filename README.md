# tapo-cli

Control Matter-era Tapo devices (plugs, switches, bulbs) through TP-Link's
own cloud. No LAN, no VPN, no phone.

```
$ tapo-cli list
DEVICE             MODEL     STATE
Living Room Plug   P110M     ● on
```

## Tested devices

| Device | Verified |
|---|---|
| P110M(US), firmware 1.4.3, Tapo app 3.20.753 | `list`, `status`, `on`, `off`, `toggle`, `info` |

That is the only device this has been tried on. The code makes no
model-specific assumptions — it writes the `on` property of the device's
shadow and reads it back — so other devices on the same cloud (P100/P110/P115
plugs, S505 switches, L530 bulbs, …) should work for on/off, but nobody has
confirmed that. Devices without an `on` property (routers, hubs, cameras)
appear in `list` and cannot be switched.

## Install

### uv (recommended)

```console
uv tool install git+https://github.com/c0ffeeOverdose/tapo-cli
uvx --from git+https://github.com/c0ffeeOverdose/tapo-cli tapo-cli list
```

From a checkout, `uv run tapo-cli …` works without installing anything;
`uv.lock` pins the dependency tree, whose only direct entry is `requests`.

### pip

```console
pip install .        # or: python -m tapo_cli …
```

### Nix

```nix
inputs.tapo-cli.url = "github:c0ffeeOverdose/tapo-cli";
# ...
home.packages = [ inputs.tapo-cli.packages.${pkgs.system}.default ];
```

```console
nix run github:c0ffeeOverdose/tapo-cli -- list
```

### Docker

```console
docker build -t tapo-cli .
docker run --rm --user "$(id -u):$(id -g)" \
  -v ~/.config/tapo-cli:/home/tapo/.config/tapo-cli \
  tapo-cli list
```

With rootless podman use `--userns=keep-id` instead of `--user`, otherwise the
container cannot read the mode-600 session file.

## Use

```console
tapo-cli auth                 # once: prompts, saves a token (mode 600)
tapo-cli list                 # devices, cloud status, live state
tapo-cli status <name>        # on/off of one device
tapo-cli on|off|toggle <name> # switch it
tapo-cli info <name>          # ip, mac, firmware, ssid, signal
```

`auth status` shows the saved session, `auth logout` drops it, `auth
--save-credentials` also writes the password file, and `auth --show-token`
(or `--json`) prints the full token for scripts.

Flags — `--json`, `--exact`, `--wait SECONDS`, `--color auto|always|never`,
`-v` — work before or after the subcommand. Output is plain when piped and
honours `NO_COLOR`.

Without a session, commands fall back to `--username`/`--password`,
`TPLINK_USERNAME`/`TPLINK_PASSWORD`, then
`~/.config/tapo-cli/credentials.env` (mode 600), logging in and saving the
resulting session. Two-factor accounts ask for the emailed code once.

## Why the usual tools don't work

| Approach | Works for | Fails for |
|---|---|---|
| [python-kasa](https://github.com/python-kasa/python-kasa) (local) | devices on your LAN | anything you want to reach from elsewhere |
| [tplink-cloud-api](https://github.com/piekstra/tplink-cloud-api) / [tplc](https://github.com/piekstra/tplink-cloud-cli) (legacy cloud) | Kasa and older Tapo devices | Matter-era Tapo devices, which answer `-20571 "Device is offline"` while the Tapo app controls them fine |

Those devices never join the legacy relay; they live on the app-server cloud
the app uses. That is the API this speaks — endpoints, auth and the dead ends
we hit are written down in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Caveats

- Unofficial and reverse engineered: TP-Link can change the API at any time
  (verified against Tapo app 3.20.753, October 2026). Not affiliated with or
  endorsed by TP-Link.
- Only a P110M has been tested — see [Tested devices](#tested-devices).
- The device has to be reachable through TP-Link's cloud, exactly like the
  phone app when you are away.
- Switching and reading state is all there is; brightness, colour and energy
  monitoring are not implemented (the thing model has them — see
  `tools/probe_thing_api.py`).
- Set `TAPO_CLI_CA_BUNDLE` to your own bundle to debug through a proxy.

## Development

```console
uv run pytest                     # 48 tests
uv run python -m tapo_cli list    # run from the checkout
nix develop -c pytest             # same suite, if you live in Nix
tools/probe_thing_api.py          # read-only dump of the cloud's view
```

## License

MIT. Nothing from the Tapo app is copied; the certificates in
`tapo_cli/certs/tplink-ca-chain.pem` are TP-Link's public CA anchors, which
the cloud does not serve itself and TLS verification needs.
