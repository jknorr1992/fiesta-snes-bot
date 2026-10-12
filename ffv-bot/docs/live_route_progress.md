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
