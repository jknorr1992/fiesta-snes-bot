# FFV-Bot

Godot 4 bot for Final Fantasy V (FF5r Super Special Deep Clean). Read-only RAM access, SNES-controller input only.

## Setup (Arch Linux, RetroArch + bsnes) - no sudo needed
1. RetroArch > Settings > Network: enable **Network Commands** (UDP 55355) and **Network Gamepad** for user 1 (UDP 55400).
2. Open this folder in Godot 4 and press F5. The **test bench** (`scenes/test_bench.tscn`) opens: connect, press SNES buttons
   (on-screen or keyboard), and read RAM. If buttons do nothing, try the other entries in the "Pad layout" dropdown.
   Set the working one as the default in `VirtualPad.layout`.

## Layout
- `scripts/retroarch_client.gd` read-only memory client (allow-list: READ_CORE_MEMORY, VERSION, GET_STATUS; no writes exist).
- `scripts/virtual_pad.gd` the 12 SNES buttons only, sent via RetroArch Network Gamepad; blocks opposite d-pad presses.
- `scenes/test_bench.tscn` interactive tester. `tools/fake_retroarch.py` fake emulator for tests. `tests/smoke.gd` headless test:
  `python3 tools/fake_retroarch.py &` then `godot --headless --path . -s tests/smoke.gd`.

## Known to verify against real bsnes
RAM address mapping (libretro may expose WRAM at 0x7E0000 or at 0x0). Step 2 will add a configurable base and a check.
