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

## Opening forest (map 27) facts
- Talking to an NPC: press the direction toward them (blocked move turns you), then A. Plain A mashing does nothing unless a dialog is already open. nav.interact(dir) does this.
- Lenna lies at (46,12); stand on (46,13) and interact UP. The scene is ~90 A presses. Afterwards event flags 16 and 562 are set and the party slot 1 gets the in-party bit (0x40). Flag 511 flickers during events.
- ROM event places: map 27 has events at (45,18) (id 225) and (45,13) (id 228); event data at 0x18E5E0 via offsets at 0x18E080, places via 0xE2400/0xE2800.
- The world checkpoint (testbed slot 0) is after the goblin battle and naming, per the user.

## Event triggers and their conditions (from github.com/everything8215/ff5, verified on map 27)
- The user's pointer to everything8215/ff5 (notes/ff5-sfc-rom-map.txt, ff5j.asm) is the better reference. Its offsets are file offsets without a header, same as ours.
- Event triggers: u16 pointers at 0xE2400 per map, RELATIVE TO 0xE2400 (the first pointer is 0x400, the table size), 4-byte entries (x, y, trigger script index). Entrance triggers (map exits): pointers at 0xE36C0, relative to 0xE36C0, 6-byte entries. NPC properties: pointers at 0xE59C0 (relative), 7 bytes each (script, ?, gfx, x, y, flags, palette). NPC scripts: pointers at 0xE0000 (relative), condition groups ended by 0xF0. Trigger scripts: pointers at 0x18E080 (relative).
- Trigger scripts: u16 pointers at 0x18E080, data at 0x18E080 + pointer (704 items): groups of conditions followed by FF + u16 event index. Conditions: FE/FD flag 0-255 set/clear, FC/FB flag 256-511 set/clear, FA/F9/F8/F5 RAM compares, F7 facing, F6 A button. tools/event_conditions.py decodes them.
- CORRECTION: my first decode used the wrong base for event triggers. Map 27 really has triggers (11,21) [needs flags 23 and 287 clear -> event 62, which sets 287] and (19,29); NPCs around (10,26) switch from event 1 to event 2 once flag 23 is set. Flag 23 is set by event 63 (map 31 trigger), so 23 is probably the goblin battle.
- Event commands $A2/$A4 set and $A3/$A5 clear event flags (0-255 / 256-511); event scripts live at C83320 (pointers) / C849DC.
- tools/event_scripts.py scans event scripts (3-byte pointers at 0x83320, data from 0x849DC) for flag set/clear commands using command lengths from the disassembly; 1893 of 1940 scripts parse exactly to their end. tools/build_event_graph.py combines triggers, conditions and script effects into a JSON graph (3735 trigger events; 3706 with clean scripts). The map-511 rows in the graph are an artefact of reading past the last pointer. Flags set by NPC scripts (e.g. 562 after Lenna) are not covered yet.
- Flags 512+ (WRAM $0A54...) are NPC visibility bits (event commands $CA set, $CB clear), not story flags. Flag 306/455/562 are all set by event 44 (trigger on ROM map 122 at (27,21)), which is the "crater done" marker we saw between the new-game and world checkpoint states. Flag 23 (set by event 63 on ROM map 31) gates the opening NPCs on ROM map 27, so it is probably the first battle. The ROM map ids used by triggers/NPCs are not the same numbers as RAM $0B4F (ROM 27 has trigger (11,21), the forest entry area; the Lenna scene area is another ROM map).

## World entrances (ROM, decoded)
Entrance triggers: u16 pointers at 0xE36C0 (relative to 0xE36C0), 6-byte entries x, y, dest map (u16, low 9 bits), dest x, dest y. ROM map 0 is world 1 (27 entries, e.g. (183,132) -> map 72 at (32,45), (121,91) -> map 125, (210,115) -> map 89, (182,52) -> map 27, ...), maps 1 and 2 are worlds 2 and 3. Map 122 (the opening forest exit) leads (9,25)/(9,26) -> world (175,143). FFVRomWorld.entrances(m) returns them; the map viewer draws them as cyan squares labelled with the destination map.
The ROM map numbers differ from the RAM $0B4F value; RAM-to-ROM map mapping is still to be established (the post-Boco forest area matches ROM map 165's trigger coordinates).

## Map ids: RAM $0AD4/$110C = ROM map id (u16)
RAM $0AD4 (also $110C) holds the same map id the ROM tables use (the opening forest is ROM map 122; $0B4F = 27 is something else). Use this for trigger/NPC/entrance lookups.
Map 122 (opening forest), from the ROM tables:
- Trigger (16,21): if flag 16 clear -> event 40 (clears 511): the Boco event. NPC (16,21) runs event 1098 (sets 511, clears 510).
- Triggers (46,13) facing up+A and (47,12) facing left+A, if flag 16 clear: events 477/478 (Lenna). NPCs at (46,11)/(46,12).
- Triggers at (27,20), (27,21), (27,22), each only while flag 306 is clear: events 42, 43, 44; event 44 sets flags 306, 455, 562, 510. This is the x=27 column the party walks through before the world checkpoint.
- Trigger (39,16): if flag 70 set and flag 71 clear -> event 36 (sets 71). Flag 70 is set on map 210 (28,23) event 161 (needs flag 486): story gate for the meteor entrance at (39,15) -> map 123 (21,26). NPCs (script 154) at (39,15) and (36,17).
- Entrance (9,25)/(9,26) -> world (175,143).
Note: FFVRam.MAP_ID is now $0AD4 (u16). The old "$0B4F map id 26/27" entries above were that other value; route.json uses ROM map ids (meteor_site = 122).
