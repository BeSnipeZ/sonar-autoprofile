# sonar-autoprofile

Switches SteelSeries Sonar EQ profiles automatically when you change between headphones and speakers.

Sonar keeps one profile per channel, no matter where the sound goes. A headphone profile with virtual
surround sounds hollow on desk speakers, a speaker profile sounds thin on headphones. This script watches
which device the Game channel plays on and rewrites four profiles to match:

| Channel | Profile | Headphones | Speakers |
|---|---|---|---|
| Game | Game Clarity | footsteps band (2.5 kHz) up, virtual 7.1, less sub | more bass, lighter surround, less reverb |
| Media | Movies | dialogue in the centre, virtual 7.1 | dialogue up, more bass |
| Aux | Music | stereo as mixed, gentle loudness curve | a bit more bass and air |
| Chat | Clear Voices | rumble cut, presence up, **no noise reduction** | same |

Noise reduction stays off on the chat channel on purpose: Discord screen-share audio runs through it too,
and Sonar's noise reduction makes stream audio choppy.

Virtual surround positions follow ITU-R BS.775 (front ±30°, side ±100°, rear ±140°) instead of Sonar's defaults.

## Use

Needs Windows, SteelSeries GG with Sonar, Python 3.10+. No packages.

```powershell
python sonar_autoprofile.py --test    # self-test, no Sonar needed
python sonar_autoprofile.py           # apply once for the current output
python sonar_autoprofile.py --watch   # keep running, switch on every output change
```

To run it in the background at logon, start it with `pythonw.exe` from a scheduled task. Tip: copy
`pythonw.exe` to `SonarAuto.exe` in the same folder and start that one, so Windows and Sonar show a
readable name instead of `pythonw`.

## How it works

- GG publishes Sonar's random port on `https://127.0.0.1:6327/subApps`.
- `GET /classicRedirections` and `/audioDevices` tell which device the Game channel uses.
- Names containing `arctis`, `headset` or `headphone` count as headphones (edit `HEADPHONE_WORDS`).
- Profiles are written with `PUT /configs` (the complete object, a partial one returns 400) and selected
  with `PUT /configs/<id>/select`. The script only overwrites Sonar's empty **Custom** slot or a slot it
  named itself, never your own profiles.
- The open GG window may keep showing the old profile name until you restart it; the audio engine already
  uses the new one.

The Sonar API is unofficial and can change with GG updates.

## License

MIT
