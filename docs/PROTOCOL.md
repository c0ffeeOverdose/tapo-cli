# The Tapo "app-server" cloud — protocol notes

Everything below was derived by reading the Tapo Android app (3.20.753),
decompiled with `jadx`, and verified live against a P110M(US) plug on
2026-10-19. Nothing here is documented by TP-Link; expect it to move.

## Why the legacy API fails

Two clouds exist in parallel:

| Cloud | Host (example) | Used by |
|---|---|---|
| legacy relay | `n-{region}-wap.tplinkcloud.com`, `n-{region}-wap-gw.tplinkcloud.com` | `tplink-cloud-api`, `tplc`, `kasa-cloud` |
| newer app-server cloud | `{region}-app-server.iot.i.tplinkcloud.com` | the Tapo app itself |

A Matter-era plug such as the P110M is listed by *both* clouds, but it only
maintains a session with the newer one:

- `getDeviceList` on the legacy cloud reports `status: 0` (offline)
- every relay request answers `-20571 "Device is offline"`
- the newer cloud reports `status: 1` for the same device, with a live
  `reported.on` in its shadow

So the legacy path is a dead end for these devices, not a bug or a stale
cache. (Reported upstream as issues #115 and #119 of `tplink-cloud-api`.)

## Session setup

Everything is signed with the scheme the apps use:

```
sig_string = "<content-md5>\n<timestamp>\n<nonce>\n<url path>"
signature  = HMAC-SHA1(app secret key, sig_string)
X-Authorization: Timestamp=9999999999, Nonce=<uuid4>, AccessKey=<app key>,
                 Signature=<hex>
Content-MD5:     base64(md5(body))
```

The access/secret pair identifies the *app* (public constants inside the APK,
not user secrets) and the timestamp is the fixed `9999999999` the apps use.
`tapo_cli/auth.py` implements this in about sixty lines.

Two TLS notes: the cloud presents certificates issued by TP-Link's own CA and
does not serve its roots, so `tapo_cli/certs/tplink-ca-chain.pem` ships the
three CA certificates and requests are verified against them;
`TAPO_CLI_CA_BUNDLE` overrides that bundle for debugging.

1. `POST {regional}/api/v2/account/login` — the ordinary v2 login. The token
   returned is the Tapo session token.
2. `POST {regional}/api/v2/common/getAppServiceUrl`
   body `{"serviceIds": ["nbu.iot-app-server.app-v2"]}`
   → `result.serviceUrls["nbu.iot-app-server.app-v2"]`, e.g.
   `https://{region}-app-server.iot.i.tplinkcloud.com` for your account.
That is the app-server
   base used for everything below.

Both calls are signed the same way and aimed at the regional host, resolved
in the first step (the default is `https://n-wap.i.tplinkcloud.com`).

### Headers

Every app-server request carries the app identity plus the token:

```
Authorization: ut|<tapo token>
app-cid:       app:TP-Link_Tapo_Android:<term_id>
x-app-name:    TP-Link_Tapo_Android
x-app-version: 3.20.753
x-term-id:     <uuid v4>
x-ospf:        Android 14
x-net-type:    wifi
x-strict:      0
x-locale:      en_US
User-Agent:    TP-Link_Tapo_Android/3.20.753(Pixel/;Android 14)
Content-Type:  application/json          <-- required for writes
```

`Content-Type: application/json` is not optional: without it the app-server
fails to parse the body and answers `500` with a bare trace id.

## Device list

```
GET /v2/things?page=0&pageSize=100
```

Each entry is a "thing": `thingName` (== the device id), `nickname`,
`deviceName`, `model`, `deviceType`, `category`, `status` (1 = online),
`familyId`, `roomId`, `fwVer`, `mac`, and the cloud endpoints
(`appServerUrl`, `cloudGatewayUrlV2`, …).

**Nicknames are base64-encoded** (`TGl2aW5nIFJvb20gUGx1Zw==` →
`Living Room Plug`) for most devices; plain text is returned as-is. Decode
only when the value round-trips to printable UTF-8.

## Live state — the shadow

```
GET /v1/things/matter/shadows?thingNames=<thingName>   # Matter devices
GET /v1/things/shadows?thingNames=<thingName>          # everything else
```

```json
{"shadows": [{
  "thingName": "0A1B2C3D4E5F60718293A4B5C6D7E8F901234567",
  "version": 431,
  "state": {
    "desired": {"on": true},
    "reported": {"on": true, "auto_off_remain_time": 0,
                 "power_protection_status": "normal", "on": true,
                 "overcurrent_status": "normal", "charging_status": "normal"}
  }
}]}
```

`reported` is the device's last known state, `desired` the last request. The
plug's switch property is called **`on`** (a boolean, `accessMode: write` in
the thing model) — not `device_on` as in the local/TPAP protocol.

## Switching

```
PATCH /v1/things/{thingName}/matter/shadows
{"version": <current version + 1>, "state": {"desired": {"on": true}}}
```

Answers `{"version": 432, "requestId": "aA1A1c…"}`, and the device applies the
desired state shortly afterwards (`reported.on` flips; poll the shadow to
confirm — the CLI waits up to `--wait` seconds).

Three things to know:

- **The version must be strictly greater than the current one.** Sending the
  current version gets
  `400 {"code":11000,"message":"Update version is smaller than present version.",
  "data":{"curVersion":429}}`. Reading the shadow and sending `version + 1`
  works; on a conflict the `curVersion` from that error is authoritative.
- **Matter-era devices only accept the `matter/shadows` endpoint.** The plain
  `/v1/things/{thingName}/shadows` PATCH works once or twice and then answers
  `500` for the P110M. Read both, write the matter one first.
- **`500` with only a trace id in `message`** (e.g.
  `"[61a7ca38…5142.17923515085…]"`) is a transient server-side hiccup: retry a
  few times instead of giving up. It is also what a missing `Content-Type`
  produces.

## Diagnostics

```
GET /v1/things/{thingName}/details   # model, fwVer, mac, ip, deviceType
GET /v1/things/{thingName}/rt-info   # signalLevel, rssi
GET /v1/things/{thingName}/settings
GET /v1/things/models/{thingModelId}        # property/service model
GET /v1/things/{thingName}/matter/components
GET /v1/things/{thingName}/usage     # energy (slow; the device must answer)
```

The thing model is worth reading before adding features — it lists every
property (`on`, `auto_off_*`, `overcurrent_status`, …) and every service
(`reverseStatus`, `clearEnergyData`, `cancelAutoOff`, `getAutoOffRemainTime`,
`matter`, …) the device supports.

## Services (mostly unexplored)

```
POST /v1/things/{thingName}/services          body {"serviceId": …, "inputParams": {…}}
POST /v1/things/{thingName}/services-sync
POST /v1/things/{thingName}/services/qos0
```

The app calls these for device RPC. On the test device every call answered
`500` (trace id) while shadow writes worked, and an invented `serviceId` is
rejected with `PARAMETER_ERROR`, so the ids are validated. Left for later:
`cancelAutoOff`, `getAutoOffRemainTime`, the `matter` passthrough (which takes
`{"request": …}` — a way to send raw Matter cluster commands, presumably).

## Dead ends (recorded so nobody repeats them)

- **MQTT gateway.** `{region}-app-cloudgateway.iot.i.tplinkcloud.com` answers
  `101 Switching Protocols` for `GET /mqtt` — it is an MQTT-over-WebSocket
  broker (AWS-IoT style, the app subscribes to `$tpiot/things/{id}/shadow/…`
  topics there for live updates). Every MQTT `CONNECT` tried — anonymous,
  token as username/password, `ut|<token>`, several client-id formats, MQTT
  3.1/3.1.1/5 — was answered with `CONNACK rc=2 identifier rejected`, i.e. the
  broker expects an app-specific client id that is not visible in the APK
  strings. Not needed for control anyway: the REST shadow path above does the
  same job.
- **`ata-proxy/proxy-passthrough`.** `POST {url}/v1/ata-proxy/proxy-passthrough`
  exists in the app but belongs to the router UI (`sc.br`).
- **`/v1/things/{thingName}/matter/models/{id}`** returns
  `RESOURCE_NOT_FOUND`; the model is served from the non-matter path.
- **Legacy relay.** `passthrough` with either envelope (Kasa
  `{"system": {"get_sysinfo": null}}` or Tapo `{"method": "get_device_info"}`)
  on both legacy hosts answers `-20571 "Device is offline"`.

## Re-verifying after a TP-Link change

```console
python3 tools/probe_thing_api.py          # read-only, dumps everything above
```

Diff its output against the shapes in this document; the shadow/matter-shadow
pair is the part that breaks first when TP-Link reshuffles the cloud.
