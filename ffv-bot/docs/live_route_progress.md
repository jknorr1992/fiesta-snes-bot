# Live-verified goal chain after the Karnak ship (bot.py @../data/goals_after_black_chocobo.txt)

Each goal was planned by the simulator from the live state and walked in the emulator with pad input only (no position writes), starting
from the party with the black chocobo (flag 58, Black Chocobo Forest NPC).

| goal | what it is |
|---|---|
| flag:59 | Ancient Library scene (world (79,119), event 134) |
| flag:60 | Quicksand Desert (map 199), sandworm event 136 |
| map:206 | Ruined City, via desert exit (49,58) -> world (54,131) -> world trigger (64,162) event 779 |
| flag:171, flag:172, flag:256 | Ruined City event spots: 781 at (35,37), then one of 782/783/784, then 785 at (35,34) (live: one spot is enough) |
| map:213, map:192 | event 144 at (34,23) -> maze map 213 -> map 214 event 145 (long scene) -> map 192 |
| map:210 | machine deck, via maps 193/194/211 (events 146, 149) |
| flag:68 | map 210 -> map 5 -> map 6 NPC event 154 -> map 190 NPC event 221 |
| flag:486 | world hook at (61,164) (event 178) |
| flag:70 | land the flying machine (vehicle 6) at (205,202), event 161 at map 210 (28,23) |
| flag:73, flag:74 | maps 122/123 (events 36, 61, 72, 60), then event 162 at map 210 (28,23) |
| flag:181, flag:182, flag:180 | map 221 flight scene (A at the airship, world (205,202)): events 175, 176, 174 in that order, then 1183. Do not use the flag:179 shortcut: it fires event 173 first and spoils the order |
| flag:75, flag:178 | the NPC battle on map 221 (group 451), then the deck scene (flag 178) |
| map:195 ... map:217 | Lonka Ruins entrance (map 195 (20,42)), then the stairs 195 -> 196 -> 197 -> 215 -> 216 -> 217 |
| pos:218:23:29, pos:218:23:15 | on map 217 press A at (25,32) (event 153), take the stair at (25,39) (NOT the one at (19,21): it lands in a dead-end nook with chests), talk to the guardian NPC (event 164), walk to (23,15) (event 163); the doorway (23,14) leads to the Earth Crystal (map 220), whose scene ends on the world map at (64,162) in the airship |
| flag:80, flag:83, flag:40, flag:84, map:123 | the meteor chain after the Earth Crystal (one meteor flag each: 80, 83, 84, plus Karnak NPC flag 40), then back to the Tycoon meteor cave (map 123); the arrival trigger at (21,26) plays event 1193 (sets flag 79, hides Cid/Mid NPCs 307/308). The warp pad trigger 445 (flag 72 clear, 73 set) is not active; the way on is world map event 183 at world 1 (146,115), needs flag 495, lands in world 2 (map 1) |

Walker notes: sand currents/conveyors (tile prop byte 2 bit 7) move the party by themselves; the walker does not tap on them and waits until
the push ends. Battle result windows are detected by the frozen frame counter ($094A).

## World 2 (data/goals_world2.txt)

Arrival island map 1 (206,110): open the menu (X), Item, use the Tent. The capture dream (map 223) ends with Galuf alone in Castle Exdeath (map 225). Set Bat.Speed and Msg.Speed to 1 in Config first (saves a lot of time). Castle: map 263 event 1085 side room is a dead end; rescue chain 225 -> 230 -> 231 -> 226 (event 827 at (49,19)), then passages on map 230 (events 1214/1218), exit by event 1631 at map 225 (18,36) onto world 2 (160,156). Then Big Bridge area 315/316 (event 211, 1219, event 220 warps to world (99,45)), Regole 247, Kuza 307/308/309, 253/259, waterway 261 (event 1337 exit), Moogle Village 262 (flag 302), waterway event 235 (map 261 (37,15) facing down + A) to 270/269, Bal castle 265 (event 240), Quelb 281-283, Drakenvale 291.

Dragon Valley notes: map 291 is the lower valley and has four cave entrances into the multi-piece map 295; the planner knows the bridge row y=12 on map 292 (do not probe the ledge at (54..56,13..14) by hand, it is a dead end). The planner is very slow for pos goals on event tiles (e.g. 295 (8,61)); step the sim path by hand or use a neighbouring tile. Direction codes in the sim: 1 up, 2 right, 3 down, 4 left.

Zeza's flagship and Barrier Tower: after flag 193 (event 798 at map 327 (31,18)) the control room map 343 gives event 860 (NPC at (13,16)), then walking UP onto (13,12) fires event 861 -> sub dock map 330. Talk to the sub NPC (event 261, sets flag 110), step through the hatch to map 331 (Barrier Tower, event 262 at (17,15) sets flag 111). Tower path: 331 -> 329 -> 328 -> 334 -> 335 -> 336 -> 337 -> 338 (event 263) -> 339 -> 340 -> 341 -> 342, event 264 at map 342 (16,11) is the Atomos fight; it sets flag 490 and returns to the dock. Then event 867 (map 343 (13,12)) boards the submarine on the underwater world map 3. A dives from the surface; map 3 (112,101) event 248 returns to the ship (flag 192), whose (13,12) event 1235 leads to dock map 344 and map 345. Sim note: world triggers and entrances fire for every vehicle on maps >= 3 (CheckTriggers skips other vehicles only on maps 0-2).

Moore: the town island and forest entrance are ringed by airship-only mountains. The submarine reaches them through the underwater map: dive (A on the surface), sail underwater to the lake at surface tiles (26-27,114-116), surface (A), disembark at (28,115) and walk into Moore (map 350) via (23,114); world trigger (35,116) event 1272 leads to the Moore forest map 356. The simulator now has `dive` and `surface` actions (underwater map m+2 lies under surface map m at the same x,y; one surfacing offered per body of water), so `bot.py map:350` plans the whole trip from the sub on the sea.

Castle Exdeath (second visit): the route above 10F (map 255) is a chain of small pockets joined by entrances between maps 255 and 245. Event 1307 at 255 (21,35) sets temporary flag 256 and opens the diamond platform (flag 256 is cleared again by any map change); inside the diamond the six flag-40 entrances warp to map 245, so keep to (26,34), (26,33), (26,32), (25,32), then (25,31) (event 1306, warp to the save point pocket (14,33)). From there (14,36) -> 245 (42,8) -> (42,10) event 1305 -> 245 (46,4) -> entrance (45,8) -> 255 (17,36) -> (21,35) again -> diamond bottom (25,39) event 1308 -> entrance (21,41) -> map 276 (52,53) -> entrance (50,51) -> map 256 -> 257 -> 258. Event 1310 at map 258 (52,12) plays the summit scenes (Gilgamesh, Exdeath) and puts the party on world 3 (map 2) at (183,137), next to Castle Tycoon (map 72). The corridor east of the diamond (255 (29,30..33)) is a dead end behind a wall tile. The planner finds this route from map 255 with `bot.py map:256` once the live state is in the 255 pocket system.

## World 3 to THE END (2026-10-13)

Run from saved states, driven by `bot.py` goals (planner plus live walker), mandatory path only:
- Pyramid of Moore 7F (map 386): the pillar maze is an event timer. Event 1462 (arriving at (14,32) facing up) starts a 2 s timer (command D1); events 1463/1464 flip flag 257 and restart it, and the tiles with trigger scripts 513/514 (event 1465) drop the party back to map 385 in the wrong phase. The sim now models timers (`State.timer`, steps of 8 frames per tile, `Action.frames`); `bot.py pos:386:43:44` finds the crossing (wait for the flip, then 4 steps) and the live walker did it. Then `flag:499` (event 1490 at 386 (43,33)).
- `flag:129` (Elder Tree, map 364 event 190), then `map:476` in three actions (world events 1842 and 1734, map 156 event 1861).
- Cleft of Dimension to the Void as stage goals taken from the map graph (entrances plus event end places): 479, 480, 485, 481, 489-495, 498, 496, 502, 503, 497, 505-509, then `flag:415` (event 1827 at 509 (30,20)) and `pos:509:30:18` (event 1924). The sim plans each stage in seconds; one search over the whole chain does not finish.
- The final battles use the battle cheats. A first run hung in the last battle (garbled screen, frozen game); reloading the state before the trigger and running with FFV_BATTLE_SNAP set got through to the THE END screen.
Not yet done: a start-to-credits run from a new game in one go (earlier segments were driven from saved states), and the approved incremental re-flood.

### Incremental re-flood experiment (2026-10-13)
Tried reusing the unchanged-map flood for a changed map when none of the tiles the flood touches (or their neighbours) changed. It was exact
(265 reused floods compared against full floods, 0 differences) but gave no speedup: pyramid plan 14.3 s vs 14.4 s with identical expansions,
map:46 stage 39 s vs 40 s. Only 41 of 356 attempts could reuse the base flood (the pyramid's timer doors sit on the walked area), and the flood
is about 10 of 16 s spent mostly on distinct tile configurations. Not kept. Event-lock chains need no extra grouping: `$BE` is screen shake,
the engine already locks control while an event runs, and the VM yields one final tile configuration per event, so there is already one flood per used state.

## Fresh new-game run to THE END (2026-10-13, battle cheats on)

`bot.py --fresh @data/goals_full.txt` (planner plus live walker, pad input only, battle cheats on, mandatory path). It ran in eleven pieces
(logs in /mnt/project-files/ffv-bot-notes/live_run_logs/), each piece resumed from the live emulator state with the goals that were left,
and reached the ending's final character-stats screen after the last boss (event 1924, map 509 (30,18)); the saved state is
/mnt/project-files/ffv-bot-notes/live_run_the_end.state. The stats screen shows Krile as LV28 Freelancer; there is no job planning in this run (the bot only walks the route).
Not one uninterrupted process: stalls and a container restart (the emulator died, the run resumed from the last battle snapshot) split it.

Bugs the live run found and the fixes:
- `live_state` did not read object visibility from the live object table (offset 14, bit 0), so NPCs shown by object actions (Boko on map 122) blocked the walker.
- The planner cannot target a plain tile (no `pos:` goal unless a trigger or entrance sits there): `at:X:Y` is a live-only step (breadth-first walk on the live local map); `open:DIR` presses A for the Tule Tent chest.
- Fast-forward toggling: with the frame counter frozen (battle, scenes) `fast_forward` toggled twice, left the emulator running uncapped and every later battle hung. One toggle is one toggle when the rate cannot be judged.
- Submarine: a dive scene ignores the pad until it ends (wait for it), and `settle()` mashing A dived or surfaced again (dive/surface actions now only wait).
- Void study book (map 493): two interactions are needed live (events 1677 then 1653); the bot re-plans and does it.
- Void boss at map 500 (event 1670): the party was wiped before the first command (max HP about 800), the game over reset the console and the A mashing started a new game. The field cheat now sets max HP 9999 as well.

## Held d-pad walking and B sprint (2026-10-13)

`Exec.walk_dirs` used one tap per tile (about 26 frames per tile, plus 24 frames per turn). It now calls `Nav.run_dirs`, which holds the d-pad across
the whole stretch (taps remain the fallback when nothing moved; `FFV_HOLD=0` forces taps).
- Pad reads: the engine reads the d-pad only on the frame a step starts. The position register is floor(scroll/32): it flips at the START of a LEFT/UP step
  and at the END of a RIGHT/DOWN step, so a turn or release triggered by the register is a tile late. The scroll registers `$0B61` (x) and `$0B63` (y) hold
  32 units per tile, so `run_dirs` reads them with the position in one 0x90-byte read per frame and, once the current step has covered `FFV_TURN_AT` (10) units,
  sets the next direction (or releases after the last tile); the engine follows it at the tile boundary.
- Polling is one RAM read per emulator frame (about 52 per second, measured); RetroArch answers network commands once per frame, so 120 Hz is not reachable.
- B held sprints on local maps (about 8 frames per tile instead of 15, no ability needed); it does nothing on the world map. `FFV_SPRINT=0` turns it off.
- Measured on the Tycoon meteorite map (map 122), new-game state to flag 306 with `bot.py flag:306`: taps 97 s, held 67 s, held + sprint 62 s (the walk includes cutscenes and the world-map leg).
  A 4-tile path with turns: 76 frames walking, 46 frames sprinting, exact stops.

## Fresh start-to-finish live run #2 (2026-10-13 17:18 to 2026-10-14 03:25 UTC)
Reached the ending character stats screen (Lenna LV28 Freelancer, battle cheats on, B sprint and held d-pad walker, 232 goals of data/goals_full.txt). Needed no planner edits for the first 100 goals; fixes made on the way:
- boiler valves toggle on every A: no A after the last message box of a press (8050f61)
- airship deck NPCs block (7,5) for minutes: the bot waits 30 s when the party did not move (e8d90f8)
- the cloud container died about every 15 min while the thread's turn was idle: bot.py checkpoints (FFV_BATTLE_SNAP): goal_<token>.state at each goal, ckpt_<token>.state every ~90 s, each with a .rem file (goals left); keep a turn active during runs
- flag:192 (world 3 (112,101) into the Pyramid of Moore): the fast-forwarded A-mash in settle() sent the party back out; run with FFV_FF=0 there
- Pyramid of Moore (map 374): step D onto trigger (14,25) runs event 1499, a scripted move onto the exit entrance (14,26). The sim does not take entrances after scripted moves, the game does. The planner now bans an action the live game answers with an unexpected map (21c3f8c); the model itself is not corrected yet.
- the final battle keeps $18 set while the ending plays: the 240 s battle watchdog fires there (set FFV_BATTLE_TIMEOUT high or stop when the stats screen shows).
