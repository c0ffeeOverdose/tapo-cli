"""Read-only diagnostic for the Tapo app-server.

    python3 tools/probe_thing_api.py [device-name]

Dumps the device list, both shadows, details and the thing model; changes
nothing on the cloud. Auth like the CLI: the saved session, or
TPLINK_USERNAME/TPLINK_PASSWORD.
"""

import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tapo_cli import cli  # noqa: E402


def show(title, payload):
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:4000])


def main():
    args = types.SimpleNamespace(username=None, password=None, verbose=False)
    cloud = cli.open_cloud(args)
    print(f"app-server: {cloud.base}")

    things = cloud.things()
    show("things", things)
    names = [thing.get("thingName") for thing in things]
    if not names:
        return

    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    if wanted:
        thing = cli.find_thing(cloud, wanted)
        names = [thing["thingName"]]
        print(f"\nselected: {cli.thing_name(thing)} ({thing['thingName']})")

    show("shadows (matter)",
         cloud.get("/v1/things/matter/shadows", {"thingNames": names}))
    show("shadows (plain)",
         cloud.get("/v1/things/shadows", {"thingNames": names}))

    for name in names:
        show(f"details {name}", cloud.get(f"/v1/things/{name}/details"))
        show(f"rt-info {name}", cloud.get(f"/v1/things/{name}/rt-info"))
        model_id = next(
            (t.get("thingModelId") for t in things
             if t.get("thingName") == name), None
        )
        if model_id:
            show(f"model {model_id}",
                 cloud.get(f"/v1/things/models/{model_id}"))


if __name__ == "__main__":
    main()
