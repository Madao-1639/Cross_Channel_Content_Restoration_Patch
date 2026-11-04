[简体中文](README.md) | English

# CROSS†CHANNEL Content Restoration Patch

Restores the story content that was removed from the Steam release of
[*CROSS†CHANNEL: Steam Edition*](https://store.steampowered.com/app/812560/), while keeping the
Steam achievements fully intact. The patch also ships a Chinese localization as a separate,
optional language pack.

For personal use with a legitimately purchased copy. This repository contains no game assets.

> **Current version: v0.6.0** — restoration is complete and in widescale play-testing.

![Banner](https://shared.steamstatic.com/store_item_assets/steam/apps/812560/library_hero.jpg?t=1573643366)

---

## Installation

Download `CROSS_CHANNEL_Content_Restoration_Patch_Installer_vX.X.X.exe` from
[Releases](../../releases) and run it.

The installer goes through these steps (the **first prompt asks for the installer language** —
Chinese / English; the rest of the run uses only the language you picked, and each stage is shown on
a cleared screen):

1. Locate the game folder (auto-detected on Windows; you can type the path manually)
2. List the files that will be modified and wait for confirmation
3. Merge the resources and overwrite the scripts in place
4. Set the default in-game language (Chinese / English — see below)

The game folder is usually
`<Steam library>\steamapps\common\CROSS†CHANNEL Steam Edition`. You can delete the installer once it
finishes.

On non-Windows systems you can build `payload/` yourself and run `tool/install.py` (it needs
`arcbuild.py`, `payload/` and the script side by side).

## Start-up settings & language

The game shows a settings dialog before it starts (language, resolution, volume, …). Two language
entries exist:

- **English** — the official English text. Restored lines show the original Japanese (see
  [Text in this patch](#text-in-this-patch--contributing-an-english-localization)).
- **中文(简体，中国)** — the Chinese language pack bundled with this patch.

The installer picks a **default language** from your environment (Steam client language or Windows
UI language); an existing choice is never overwritten. You can force it on the command line with
`--lang=zh-CN|en`.

To switch later, change the “Language Pack” entry in that dialog: the game asks for a restart,
restarts itself, and the UI text follows the new language.

## Uninstall

The patch keeps no backups — restoring the original files is Steam's job:

1. Steam library → right-click the game → Properties
2. Installed Files → Verify integrity of game files

Steam detects the modified archives, downloads them again, and the game is back to vanilla. You can
also simply delete the `zh-CN/` folder to drop the Chinese content while keeping the restoration.

## Updating

Verify the game files first (see above), then install the new version the same way.

## Files that are modified

| File | Action | Content |
|---|---|---|
| `Rio.arc` | overwrite | Story scripts, 364 members: the original story is inserted into 12 scripts at their cut points |
| `zh-CN/Rio.arc` | new | 330 members: 328 `.lng` files (Chinese text and choices), `NameTable.txt` (speaker names), script copies carrying subtitles |
| `Chip2.arc` | merge | 199 → 236 members: 37 restored event CGs (`EVCC9000`–`EVCC9018`) |
| `Voice.arc` | merge | 30,288 → 32,416 members: 2,128 restored voice clips |
| `zh-CN/Fonts.arc` | new | two fonts with CJK glyphs (Chinese display) |
| `zh-CN/Script.arc` | new | 14 Lua scripts providing the graphical system menus (absent from the vanilla archive) |
| `zh-CN/Graphic.arc` | new | 3 full-screen images, localized |
| `zh-CN/SysGraphic.arc` | new | system-UI layers, localized |
| `zh-CN/AdvHDLang.dll` | new | language-pack marker + the strings of the start-up settings dialog |

These archives are **left untouched**; the installer keeps your originals: `Chip1.arc`, `SysVoice.arc`.

---

## How it works

The Steam release (App ID 812560, Build ID 812561) cut parts of the story. The patch puts that content
back without touching anything Steam already ships, so achievements and script flow stay exactly as
they were.

### 1. Restoration

The restoration material comes from the original game — but the two releases run on **different
engines**:

|  | Original | Steam release |
|---|---|---|
| Engine | WillPlus (`CROSSCHANNEL.exe`) | AdvHD (`AdvHD.exe`) |
| Script format | `WSC` (each byte `ror 2`) | `WS2` (each byte `rol 6`) |
| Archive format | fixed 13-byte file names | UTF-16LE variable-length names |
| Images | `ANM` / `MSK` and friends | PNA layers / PNG |

The instruction sets are **completely different**, so matching byte offsets do not mean matching
semantics — the original scripts and assets **cannot be reused as-is**. Most of the work is
**decrypting the original `WSC`, converting it to `WS2` instruction by instruction**, and then
splicing the result back into the Steam story flow.

On top of that conversion the patch uses **additive content + in-place insertion**:

- **No Steam resource is replaced** — the original assets, achievement calls and script exits stay as they are
- **Name collisions are isolated** — a few resources share a name between the releases with different content (the engine's layer indices are pure positions, so overwriting them would break other scenes). The restored versions get their own names, and both sets coexist:
  - event CGs always live in the `EVCC9000`–`EVCC9018` range (the engine applies an `EVCC` prefix whitelist to `Chip2.arc`)
  - character sprites are renamed `TC{char}0{nnn}` → `TC{char}1{nnn}`
- **The original story is spliced back in place** — when Steam cut a scene it left a few-line **abridged** version in the same script. The patch replaces that abridged block with the full original scene, still connected to Steam's own text on both sides:

  ```
  script = [Steam intro] + [full original scene] + [Steam tail (with its original exits)]
  ```
  12 scripts are handled this way, with **the same script names, the same jumps, the same exits**;
  scene titles, gallery hooks and other opening content are preserved.

Backgrounds, masks and character sprites referenced by the restored scenes come from the Steam
archives (nothing new is added); event CGs and voices come from the original game.

### 2. Chinese localization (as a separate language pack)

The Chinese text, localized UI layers and fonts are shipped as the engine's **native language pack**
(`zh-CN/`), which only takes effect when the player selects it in the start-up settings dialog.
Internals (why the text has to go through `.lng` files, how the layers are replaced, and how the text
was verified) are documented in **[Chinese](README.md)**.

---

## Text in this patch & contributing an English localization

**This patch does not localize the game.** It only restores content:

- **Restored scenes** carry their **original Japanese** lines — that is the text of the source
  scripts, and it is what you see with the Chinese pack turned off.
- **Everything else** keeps the **official English** text of the Steam release, untouched.

So if you want an **English** version of the restored content, all you have to translate is the
Japanese text of the restored scenes — no English text needs to be touched. The localization layer is
data-only (`.lng` text files + a speaker-name table), and a **localization PR is very welcome**:
see the Chinese README for the exact file layout, or open an issue and we will point you at the
relevant parts.

## Known behaviour

### How the Steam release cut the scenes

Steam compressed each cut scene into a few abridged lines left in the original script. The patch
replaces that block with the full original scene and keeps Steam's own ending of the script. Small
extra lines that only exist in the Steam release (e.g. `Ideal.` in `CCC0000`) are dropped together
with the abridged block.

One exception: in `CCB2017` Steam deleted a whole block and rewrote it into a different scene
(“Chinese jump rope”), leaving no room at either end. That spot is carried by a **choice**: an extra
prompt and two options — “Chinese jump rope” (Steam, unchanged) or “The one-hour H bet” (original,
unchanged) — after which both branches rejoin the same continuation. The cost is one extra choice in
that script and two in-file jumps (the only ones in the whole project).

### Achievements

The patch does not touch Steam's achievement calls and does not change any script's exit set: all 12
scripts that receive restored story keep Steam's own ending, including its achievement hooks (`0b`
instructions), scene titles (`SCENETITLE*`) and gallery hooks.

---

## Repository layout

```
├── asset/              repacked game files (large, not in Git LFS)
├── backup/             vanilla Steam baseline (used to compute the delta)
├── doc/                technical documentation (Chinese)
├── tool/               shared libraries and tools
│   ├── install.py      installer logic
│   ├── arcbuild.py     Arc format reader/writer
│   ├── ws2.py          WS2 script codec
│   ├── ws2disasm.py    WS2 disassembler
│   ├── wsc.py          original WSC disassembler
│   ├── wsc2ws2.py      WSC→WS2 instruction-by-instruction converter
│   ├── lng.py          localization text (.lng) codec
│   └── luac53.py       Lua 5.3 bytecode parser
├── script/             build and development scripts
│   ├── pack.sh         packages the installer exe
│   ├── generate_payload.py  builds the incremental patch
│   └── final_verification.py  final acceptance checks
├── resource/           mapping tables and packed resources
│   ├── scene_slices.json  insertion ranges of the restored scenes
│   ├── cg_map.json        event CG rename map
│   ├── speaker_map.json   character names (single source of truth)
│   └── icon.ico        installer icon
├── payload/            incremental patch output (generated)
├── releases/           installer exe (generated)
└── VERSION             version number (single source of truth)
```

## Test status (full playthrough with `asset/` copied over the game folder)

- [x] All 12 restored scenes trigger, seams are clean, story continues normally
- [x] No crashes across a full playthrough; all CGs collectable
- [x] All achievements unlockable
- [x] Speaker name boxes and dialogue render correctly; localized UI layers display correctly
- [x] The two branches of the `CCB2017` choice

## Feedback

Please use [Issues](../../issues) and include:

- the patch version
- the scene where it happens (route + rough progress; a save file helps — they live in
  `C:\Users\<you>\Saved Games\MoeNovel\CROSS†CHANNEL Steam Edition`)
- any error log the game produced
- what exactly goes wrong

## Credits

- Original-game assets (CGs and voices) used for the restoration, added after item-by-item content comparison
- Tooling and engineering experience from the
  [A Sky Full of Stars Content Restoration Patch](https://github.com/Madao-1639/A_Sky_Full_of_Stars_Content_Restoration_Patch)

## To-Do

- [ ] Full play-testing
- [x] Localized UI
- [ ] Misc leftover content
