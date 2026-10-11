# FFV-Bot screen renderer

Rebuilds what the SNES shows while FFV is on the field, from the game's own data. Two inputs, one render path:

* **Live mode**: reads WRAM from a running RetroArch (network commands, `READ_CORE_RAM`, 4096 bytes per read at most) and combines it
  with ROM graphics. Compared against emulator screenshots (896x672 sampled back to 256x224) it is pixel-exact on standing party states.
* **Sim mode**: `field_state.build_mem` synthesizes the same WRAM variables from a simulator `State` (map, position, facing, event
  flags, NPC flags, tile modifications, moved NPCs), so the screen after a simulated event sequence can be drawn without an emulator.

Both go through `field_screen.Field.render`, which builds a `ppu.PPU` (VRAM, CGRAM, OAM, registers) and renders it.

| module | role |
| --- | --- |
| `ppu.py` | software PPU: mode 1 BG tilemaps (4bpp/2bpp), mode 7, OBJ, windows, colour math (main/sub screen, half), exact RGB565-style output |
| `snes9x_state.py` | reads RetroArch/snes9x save states (VRAM, CGRAM, OAM, registers, WRAM) |
| `field_screen.py` | local map and world map screens from WRAM + ROM: graphics blocks, BG3, party/NPC/overlay sprite graphics, animated tiles, palettes, scroll |
| `field_sprites.py` | transliteration of `DrawPlayerSprite`, `DrawObjSprites`, `UpdateOverlay`, `DrawOverlaySprites`, world `_c02137`/`_c01ec5`/`_c01e64`: rebuilds the OAM shadow ($0200-$041F) from object/party variables |
| `field_state.py` | sim `State` to WRAM image (layers with event mods, tile properties, tileset, palette, scroll, object table, party variables) |
| `screen_check.py` | screenshot sampling and diff tools |

## Facts the renderer depends on (all verified against emulator frames)

* Field direct page is `$0B00`; `$61/$63` BG1 scroll in half pixels (BG2/BG3 follow at `$65..$6B`), `$10A0..$10AA` screen-shake offsets.
* Scroll for a standing party: `((tile * 16 - 0x70) * 2) & 0x7FF` for both axes.
* Tileset arrangement planes are TL, TR, BL, BR (four u16[256] planes, decompressed 0x800 bytes). The old note in `map_render.py` that said
  TL, BL, TR, BR was wrong.
* BG layouts: no layout index means the layer is filled with tile 1; a layout whose first byte is non-zero fills the layer with that byte.
* Palette buffer `$0C00`: `MapPal[$1122]` (0x3BB00), `$0C00=0`, `$0C04=$318C`, `$0C06=$7FFF`, MapSpritePal (0x1FFC00, 128 bytes) at `$0D00` and `$0D80`.
  Entry 1 is the player's menu window colour.
* BG vertical bias +1, sprites none; snes9x freeze blocks are big-endian.
* Animated local-map tiles: 12 slots at `$1417` (flags, counter, offset, source); DMA'd each frame to chars `$2E0..$2FF` and BG3. World water:
  WRAM buffers `$7F8622/$7F86A2/$7F8722` over mode 7 characters `$62/$72/$87`.
* Object table `$1477 + 0x14 * i` (x, y in tile*256, `$147C` frame, `$147E` flags, `$1481/$1483` tile+attribute words, `$1485` visible bit).

* Capture live frames with the emulator paused (`PAUSE_TOGGLE`); un-paused captures race the animation. Walking tours over maps 122, 20, 21 and
  the world map (about 45 positions, emulator paused per capture) were all pixel-exact in live mode.
* snes9x does colour math in RGB565, so green is blended with 6 bits (`ppu.py`); this fixed the last off-by-one green values.
* VRAM characters $300-$3CF are solid tiles written by `_c08b53` (used by shadow overlays).
* Maps with HDMA select 1/2 (about 78) run a BG2/BG3 wave: 16-word tables at $7F6DE2/$7F6E02 per scanline (`Field.wave_hdma`, live mode only).
* BG2/BG3 parallax: `((pos << mult) >> 1) - offset` from the map properties (`field_state`).

* The picture on screen is built from the WRAM of the previous frame: during motion, render the snapshot from one frame earlier
  (`field_screen.LiveScreen.grab`, polled once per frame, e.g. emulator paused and stepped with `FRAMEADVANCE`). Verified exact on 40 consecutive
  frames of a walk (hold DOWN, scrolling every frame).

## Usage

```
# live: compare against the emulator (testbed README: /mnt/project-files/retroarch-testbed/README.md)
python3 tools/screen_check.py            # see its docstring
# sim: picture of a State
python3 tools/field_state.py /tmp/out.png
```

## Known limits

* Sim mode uses frame 0 for palette/tile/water animation, no map title banner, no BG auto-scroll, no HDMA wave effects.
* States captured mid-step (scroll or sprite 1 px off) and during screen shake only match when `$10A0..` are read live.
* World map sim mode (`field_state.world_mem`) uses `world_model.World` for flag-driven tile changes; water phase is not predictable.
