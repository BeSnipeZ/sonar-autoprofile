"""Switch SteelSeries Sonar EQ profiles automatically when the output device changes.

Every 10 s the script asks the local Sonar API where the Game channel is playing.
When that changes between headphones and speakers, it rewrites four custom profiles
(Game, Media, Aux, Chat) tuned for that output and selects them.

    python sonar_autoprofile.py           apply once for the current output
    python sonar_autoprofile.py --watch   keep running, switch on every output change
    python sonar_autoprofile.py --test    self-test without Sonar

Needs SteelSeries GG with Sonar running. Standard library only.
"""

import copy
import json
import ssl
import sys
import time
import urllib.request

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE  # 127.0.0.1 only: GG uses a self-signed certificate

# Device names containing one of these count as headphones, everything else as speakers.
HEADPHONE_WORDS = ("arctis", "headset", "headphone", "kopfh")


def _eq(*filters):
    """(type, Hz, dB, Q) per band; Sonar wants all 10 bands, the rest stay flat."""
    e = {"enabled": True}
    for i in range(10):
        kind, f, g, q = filters[i] if i < len(filters) else ("peakingEQ", 1000.0, 0.0, 0.7071)
        e[f"filter{i + 1}"] = {"enabled": True, "qFactor": q, "frequency": float(f), "gain": float(g), "type": kind}
    return e


def _room(center=0.0, side=0.0, rear=0.0, sub=0.0):
    """Virtual 7.1 positions after ITU-R BS.775 (front +-30, side +-100, rear +-140 degrees), gain in dB."""
    k = lambda p, g: {"position": p, "gain": g}
    return {"frontLeft": k(30, 0), "frontRight": k(-30, 0), "center": k(0, center), "subWoofer": k(0, sub),
            "sideLeft": k(100, side), "sideRight": k(-100, side), "rearLeft": k(140, rear), "rearRight": k(-140, rear)}


_FLAT = {"bassBoostState": {"enabled": True, "value": 0}, "trebleBoostState": {"enabled": True, "value": 0}}
# Games: localisation over impact (louder sides/rear, less sub). Movies: dialogue in the centre.
_GAME_HP = {"virtualSurroundState": True, "virtualSurroundChannels": _room(side=1.0, rear=1.5, sub=-3.0), "reverbGainDB": -15}
_MOVIE_HP = {"virtualSurroundState": True, "virtualSurroundChannels": _room(center=2.0, sub=1.0), "reverbGainDB": -8}
_GAME_SP = {"virtualSurroundState": True, "virtualSurroundChannels": _room(side=1.0, rear=1.0), "reverbGainDB": -18}
_MOVIE_SP = {"virtualSurroundState": True, "virtualSurroundChannels": _room(center=2.0, sub=1.0), "reverbGainDB": -12}

PROFILES = {
    "headphones": {
        "game": ("Game Clarity", _eq(("lowShelving", 70, 2.0, 0.7071), ("peakingEQ", 250, -1.5, 1.0),
                                     ("peakingEQ", 2500, 2.5, 1.2), ("peakingEQ", 5000, 1.5, 1.5), ("peakingEQ", 9000, -1.0, 2.0)),
                 {**_FLAT, "voiceClarityState": {"enabled": True, "value": 0.5}, **_GAME_HP, "formFactor": "headphones"}),
        "media": ("Movies", _eq(("lowShelving", 60, 2.5, 0.7071), ("peakingEQ", 250, -1.0, 1.0),
                                ("peakingEQ", 2500, 2.0, 1.0), ("highShelving", 10000, 0.5, 0.7071)),
                  {**_FLAT, "voiceClarityState": {"enabled": True, "value": 1.0}, **_MOVIE_HP, "formFactor": "headphones"}),
        "aux": ("Music", _eq(("lowShelving", 80, 2.0, 0.7071), ("peakingEQ", 300, -1.0, 1.0),
                             ("peakingEQ", 3000, 1.0, 1.0), ("highShelving", 10000, 1.0, 0.7071)),
                {**_FLAT, "voiceClarityState": {"enabled": True, "value": 0}, "virtualSurroundState": False,
                 "formFactor": "headphones"}),
    },
    "speakers": {
        "game": ("Game Clarity", _eq(("lowShelving", 80, 3.0, 0.7071), ("peakingEQ", 250, -1.0, 1.0),
                                     ("peakingEQ", 2500, 2.0, 1.2), ("peakingEQ", 5000, 1.0, 1.5)),
                 {**_FLAT, "voiceClarityState": {"enabled": True, "value": 0.5}, **_GAME_SP, "formFactor": "speakers"}),
        "media": ("Movies", _eq(("lowShelving", 80, 3.0, 0.7071), ("peakingEQ", 250, -1.0, 1.0),
                                ("peakingEQ", 2500, 2.5, 1.0), ("highShelving", 10000, 1.0, 0.7071)),
                  {**_FLAT, "voiceClarityState": {"enabled": True, "value": 1.0}, **_MOVIE_SP, "formFactor": "speakers"}),
        "aux": ("Music", _eq(("lowShelving", 90, 3.5, 0.7071), ("peakingEQ", 300, -1.0, 1.0),
                             ("peakingEQ", 3000, 1.0, 1.0), ("highShelving", 10000, 1.5, 0.7071)),
                {**_FLAT, "voiceClarityState": {"enabled": True, "value": 0}, "virtualSurroundState": False,
                 "formFactor": "speakers"}),
    },
}
# Chat voices: same on both outputs. No noise reduction: the chat channel also carries
# Discord screen-share audio, and noise reduction chops it up.
_VOICES = ("Clear Voices", _eq(("lowShelving", 100, -4.0, 0.7071), ("peakingEQ", 300, -1.5, 1.0),
                               ("peakingEQ", 2500, 2.0, 1.0), ("peakingEQ", 4000, 1.5, 1.2), ("peakingEQ", 8000, -1.0, 1.5)),
           {"noiseReductionState": {"enabled": False, "value": 0}})
for _p in PROFILES.values():
    _p["chatRender"] = _VOICES
# Slots this script may overwrite: Sonar's empty "Custom" slot or one it already named.
OWN = {"Custom", "Game Clarity", "Movies", "Music", "Clear Voices"}


def kind(device_name: str) -> str:
    n = device_name.lower()
    return "headphones" if any(w in n for w in HEADPHONE_WORDS) else "speakers"


def _get(url):
    with urllib.request.urlopen(url, context=_ctx, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _put(url, data=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method="PUT", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=_ctx, timeout=10) as r:
        r.read()


def base() -> str:
    """Sonar picks a random port at start; GG publishes it on 6327."""
    return _get("https://127.0.0.1:6327/subApps")["subApps"]["sonar"]["metadata"]["webServerAddress"]


def output(b: str) -> str:
    """Name of the device the Game channel plays on."""
    target = {r["id"]: r["deviceId"] for r in _get(b + "/classicRedirections")}.get("game", "")
    return {d["id"]: d["friendlyName"] for d in _get(b + "/audioDevices")}.get(target, "")


def apply(b: str, which: str) -> None:
    for vad, (name, eq, rest) in PROFILES[which].items():
        slots = [x for x in _get(f"{b}/configs?vad={vad}") if x["name"] in OWN and not x.get("isPreset")]
        if not slots:
            raise LookupError(f"no free 'Custom' slot for {vad}")
        new = copy.deepcopy(slots[0])  # PUT needs the complete object, a partial one returns 400
        new["name"] = name
        new["data"]["parametricEQ"] = eq
        for k, v in rest.items():
            if k in new["data"]:
                new["data"][k] = v
        _put(b + "/configs", new)
        _put(f"{b}/configs/{slots[0]['id']}/select")


def watch() -> None:
    last = None
    while True:
        try:
            b = base()
            which = kind(output(b))
            if which != last:
                apply(b, which)
                print(f"output {output(b)!r} -> {which} profiles", flush=True)
                last = which
        except Exception as e:  # GG closed, restarting or updating: try again later
            if last is not None:
                print(f"Sonar not reachable: {e}", flush=True)
            last = None
        time.sleep(10)


def selftest() -> None:
    assert kind("Headphones (SteelSeries Arctis 9 Game)") == "headphones"
    assert kind("Speakers (Realtek High Definition Audio)") == "speakers"
    assert kind("") == "speakers"
    for which in PROFILES:
        assert set(PROFILES[which]) == {"game", "media", "aux", "chatRender"}
        assert {p[0] for p in PROFILES[which].values()} <= OWN
        assert len([k for k in PROFILES[which]["game"][1] if k.startswith("filter")]) == 10
    assert not _VOICES[2]["noiseReductionState"]["enabled"]
    assert not PROFILES["headphones"]["aux"][2]["virtualSurroundState"]
    r = _room(center=2.0)
    assert r["center"]["gain"] == 2.0 and r["rearLeft"]["position"] == -r["rearRight"]["position"] == 140
    print("self-test ok")


if __name__ == "__main__":
    if "--test" in sys.argv:
        selftest()
    elif "--watch" in sys.argv:
        watch()
    else:
        b = base()
        w = kind(output(b))
        apply(b, w)
        print(f"output {output(b)!r} -> {w} profiles")
