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
