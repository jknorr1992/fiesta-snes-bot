# RetroArch test bed (headless)

A real RetroArch for testing FFV-Bot without a screen. Built here in the cloud container, so the binary is for
x86-64 Linux with Ubuntu 24.04-style libraries (X11, GL, freetype, ...). On your own machine just use your RetroArch.

| File | What |
|---|---|
| `retroarch` | RetroArch **1.22.2** built from the v1.22.2 tag (matches the user's version), minimal features |
| `snes9x_libretro.so` | snes9x core built from libretro/snes9x master (commit fae2fea, 2026-09-19) |
| `ra.cfg.template` | config: Network Commands on 55355, Network RetroPad on 55400, null audio |
| `start.sh` | launches everything headless (needs `xvfb` plus the usual X11/GL libs: `apt-get install xvfb libgl1-mesa-dri libgl1 libegl1 libxkbcommon0 libxrandr2 libxinerama1 libxxf86vm1 libfreetype6 libfontconfig1`) |
| `states/newgame.state` | snes9x save state shortly after the opening finished (Gil 500); load with `LOAD_STATE`/F4 |

## Facts learned (verified)
- bsnes core: no RAM access over network commands. **Use snes9x.**
- RAM is read with `READ_CORE_RAM <hex offset> <len>`; offset 0 = $7E0000 (WRAM). `READ_CORE_MEMORY` always says "no memory map defined" with snes9x.
- `VERSION` replies just `1.22.2` (no command prefix).
- Gil = WRAM 0x947, 3 bytes little-endian (500 on a new game), confirmed on both the user's machine and here.
- `GET_STATUS` crashed this minimal build (segfault); the bot doesn't need it.
- `SCREENSHOT` over the command port writes a PNG into RetroArch's screenshot directory - handy for checking what the game shows.
- In the test build the video driver must be `gl` (the `null` driver segfaults), under xvfb with `LIBGL_ALWAYS_SOFTWARE=1`.
- ROM CRC32 reported by RetroArch: `ac77cac4` (patched ROM).

## Running the bot against it
1. `retroarch-testbed/start.sh &` (waits ~10 s to boot)
2. `godot --headless --path FFV-Bot -s tests/e2e_newgame.gd` - mashes START/A through the intro using only the SNES pad,
   and prints Gil (verified: reaches 500 after ~100 presses).
Do not `pkill -f` patterns that appear in your own command line; use `pgrep -x retroarch`.

> The repo copy omits the `retroarch` binary, `snes9x_libretro.so` and `states/` (large, and the state embeds ROM data).
> Build RetroArch v1.22.2 and libretro/snes9x yourself, or get them from the project library, and drop them next to `start.sh`.
