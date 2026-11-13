# ffxi-dps-meter

An external DPS meter for Final Fantasy XI (Windower 4). A small addon, **DPSLink**, runs on each
character and streams combat data to a desktop window, **DPSMeter.exe**.

```
FFXI char 1 ─ DPSLink.lua ─┐
FFXI char 2 ─ DPSLink.lua ─┼─ UDP 127.0.0.1:24250 ─▶ DPSMeter.exe
FFXI char N ─ DPSLink.lua ─┘
```

If several of your characters see the same action (same party), it's counted once.

## Install
1. Download `DPSMeter.exe` and `DPSLink-addon.zip` from the [latest release](../../releases/latest).
2. Extract the zip into `Windower/addons/` so you have `Windower/addons/DPSLink/DPSLink.lua`.
3. Run `DPSMeter.exe`. Until the exe is code-signed, Windows SmartScreen may warn: click *More info → Run anyway*.
4. In game, on every character you want feeding it: `//lua load dpslink`
   (add `lua load dpslink` to `Windower/scripts/init.txt` to auto-load).
5. Connected characters appear in the app's status bar with job and zone.

## App
- **Meter**: damage, DPS and share per player. Click a row to see that player's breakdown.
- **Combats**: a fight ends after the timeout (default 8s) with no damage. Pick past fights with ◀ ▶, the dropdown, or the Combats tab. **Live** follows the current fight; **All** adds up every stored fight.
- **Breakdown tab**: per damage type (Melee, Ranged, Weapon Skill, Magic, Ability, Skillchain, Add. Effect, Spikes), with hits, avg, max, accuracy and crit rate, plus each WS/spell/ability.
- **Mobs tab**: damage per mob. Double-click a mob to filter to it.
- **Filters** (they also apply to past fights): mob name, which character reported it ("Seen by"), damage types, pets/trusts/alliance, and merging pets into their owner.
- **Compact** hides everything but the meter. **Pin** toggles always-on-top. **Copy** puts a text report on the clipboard.
- Settings (window position, opacity, toggles) are saved to `settings.json` next to the exe.

## Addon commands
- `//dpsl`: status (packets sent, target address)
- `//dpsl host <ip> [port]`: send to an app on another PC (set `"bind": "0.0.0.0"` in that PC's `settings.json`)
- `//dpsl port <n>`
- `//dpsl reset`: clear the app's data from in game

## Development
```
DPSLink/            Windower addon (folder name must stay DPSLink)
app/dpsmeter.py     tkinter UI
app/engine.py       parsing, de-duplication, combats, filters (no UI; testable)
app/simulate.py     fake traffic: python app/simulate.py 60
app/build.bat       builds dist/DPSMeter.exe with PyInstaller
```
- Run from source: `python app/dpsmeter.py` (Python 3.10+, standard library only).
- Build locally: `python -m pip install pyinstaller`, then `app\build.bat`.
- To test addon changes in game, run `install-addon.bat` (or `install-addon.bat "C:\path\to\Windower"`), then `//lua reload dpslink`.
  It copies the addon instead of linking it: a junction or symlink inside a OneDrive-synced Windower folder can freeze the game client on load.

## Releases
Push a version tag and GitHub Actions builds the exe and publishes a release with the exe, the addon zip and SHA-256 checksums:
```
git tag v1.0.0
git push origin v1.0.0
```

## Code signing
The release workflow signs `DPSMeter.exe` with Azure Artifact Signing (formerly Trusted Signing) when these repository secrets are set:
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `SIGNING_ENDPOINT`, `SIGNING_ACCOUNT_NAME`, `SIGNING_PROFILE_NAME`.
Without them, the release is built unsigned.

## License
MIT
