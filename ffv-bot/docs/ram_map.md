# Verified RAM facts (WRAM offsets, snes9x core, READ_CORE_RAM offset 0 = $7E0000)

| Address | Meaning |
|---|---|
| $0018 | In-battle flag (1 = battle) |
| $0500 + 0x50*slot | Party slot struct: +0 flags, +1 job, +2 level, +3 exp (u24), +6 HP, +8 max HP, +A MP, +C max MP |
| $0640 / $0740 | Inventory: 256 slots of item id / quantity. Phoenix Down = 228 (seen after opening a chest); new game has id 224 x3, probably Potion |
| $0947 | Gil (u24 LE), 500 at new game |
| $0AD8 / $0AD9 | Map X / Y tile position |
| $0B4F | Local map id (26 meteor forest, 27 crater). Not cleared on the world map, so only valid while $0B53 = 1 |
| $0B53 | 1 inside a local map, 0 on the world map (consistent over 7 checkpoints) |
| $0ADA | 7 while riding the chocobo, 2 on foot (probably; seen on 7 checkpoints) |
| $10000 (world map only) | 256x64 band of world tile ids; world row y is band row (y & 63), valid near the player. 135 = ocean, 12-14/28-30/44-46 = 3x3 mountain blocks, 157/173/189 = the crater entrance column |
| $10000 (local maps) | 64x64 local-map tile id grid (row-major); passability learned at runtime. Tile id 2 = treasure chest |

Found by WRAM snapshot/diff against checkpoint save states in the test bed (see retroarch-testbed/README.md).
On the world map position is 0..255 (e.g. 175,143); local maps are 0..63.
Unknown so far: dialog flag, menu/battle-cursor state, story flags, treasure-opened flags.

## Event flags (from the wiki dump, confirmed by diff)
- $0A14..$0AD3 (96 bytes): event/story bits; event n = byte (n>>3) bit (n&7). Diffing new-game vs world-after-crater flipped events 306 (byte $0A3A bit 2) and 455 ($0A4C bit 7).
- $09D4..$09F3: treasures-opened bits. $0B57 message dialog open (verified: 1 during the Boco dialog).
- Wiki extracts: /mnt/project-files/ffv-bot-notes/wiki-extracts.md

## Static world map from ROM (verified)
tools/ffv_decomp.py `world_row()` decodes world-map rows: pointer = u16 at file offset 0xFE000 + 512*world + 2*y, data at 0x70000 + pointer. For world 0, rows 136-149 match the RAM band at $10000 exactly except the 3 tiles around the player (entity overlay at x=175). So the whole world map is available without walking it. The ROM has no copier header (CF/E000 = file 0xFE000).

## World map notes
- RAM band at $10000 = ROM world map plus event changes (the crater tiles 157/173/189 at x=175, y=142-144 replace plain tile 5 after the meteor). Nav uses the RAM band near the player and the static ROM map (FFVRomWorld, needs the ROM path) farther away.
- The start pocket at (175,143) is open to the north (grassland); (181,143) is inside a mountain block, so no_path there is correct, not a walker bug (screenshot checked).
- Exit table at file 0xE3AC0 (offsets at 0xE36C0): 6-byte entries x,y,destx,desty,destmap,flags. Index space not yet understood (index 46 holds an entry at (175,143)).

## World passability (ROM, checked against bumps)
- Tile property table at file 0xFEA00, 3 bytes per tile id, 192 ids per world. Byte 0 low nibble 0xF = walkable on foot/chocobo (0x4F plains etc.). 0x44 = mountain (ids 6,7,8,22,23,24,38,39,40,59), 0x7C = ocean/water. FFVRomWorld.walkable_ids() exposes it; nav uses it as a prior under bump learning.
- From the checkpoint at (175,143) the reachable region is closed (mountains and water): only the crater entrance tile (175,142, id 157) leads out of it, into local map 27 (the meteor forest path; chocobo sprite, enter at (10,26)). The area north of the crater, including the town icon at (183,131), is only reachable through the story, not by walking.
- ROM exit table at 0xE3AC0 is indexed per local map and is not the world-entrance list. 2x2 town icons use tile ids 147/148/163/164: world 0 has them at (83,76), (210,114), (183,131).
