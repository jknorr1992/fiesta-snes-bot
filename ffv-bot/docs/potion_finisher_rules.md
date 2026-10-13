# Potion usefulness and finisher rules (merged)

Branch `wip/byblos-potion-fix`, change in `ffv-bot/battle/policy.py` (VeteranPolicy). Not merged to main because Liquid Flame looked worse.

## What changed
1. `_item_heal(item, deficit)`: HP an item restores = record bytes 5 x 6 at ROM 0x110A80 + 8*(item-0xE0) (Potion 10x5 = 50, Hi-Potion 50x10 = 500, Elixir = the gap).
2. In `_support`, an item heal is used only if it lifts the ally out of reach of the strongest possible hit (`a.hp <= hi < a.hp + heal`) or fills at least 40% of the gap. Before, Black Mages spent whole turns on 50-HP Potions while the party took ~230 per member per round.
3. `_finisher`: when one monster is left and an attack spell's lowest roll ((power - magic defence) x M, weakness doubles and ignores defence) covers its HP, cast it before any revive/heal (skipped if a counter script would kill the caster).

## A/B numbers (60 seeds, hard=True, `/tmp/fd4.py STATE ENC`, 60 = all)
- Byblos (enc 446, state from campaign run cZ2 at the Ancient Library, party lv17 Monk/BlackMage/WhiteMage/BlackMage): baseline 45 flawless, 51 wins (~75% flawless, 85% wins); finisher only: 45 flawless, 55 wins; finisher + potion rule: 60/60 flawless, 5131 frames (was ~7000+).
- Liquid Flame (enc 444, party lv15 at the cap, state /tmp/cZ/fail_444_230.bin): baseline 56/60 wins (4 defeats); with both changes 43 wins, 17 defeats, 37 wins with deaths. NOT yet split into which change causes it (finisher vs potion rule). Next step: toggle each (e.g. make the potion condition `if False: continue`) and rerun on Liquid Flame; likely tune so potions still go to members at risk when no healer has MP, or exempt fights where the party out-heals the boss.

## How to regenerate the states
States are WRAM dumps written by `ffv-bot/battle/campaign.py` as `fail_<enc>_<seed>.bin` in its --out directory when a fight fails the 95% bar. Run:
`FFV_ROM=<rom> python3 campaign.py --out DIR --runs 64 --jobs 4 --extra-towns --loot --boss-deaths --max-level 15`
(`--resume` continues from DIR/state.bin). Liquid Flame fails there at the lv15 cap; Byblos fails at the Ancient Library (stage 25, step 168).

## Run cZ2 result (old policy, commit 4baf6f8)
Finished the whole encounter file but the Ancient Library took 7 failed attempts and grinding to lv22 (56k gil unspent); Byblos failed at 80-94% flawless several times. Karnak escape cleared on its third try. Goal of the fix: pass Byblos with far fewer levels.

## Result of the split (2026-10-13, fresh campaign state at the lv15 cap, VeteranPolicy flags `potion_rule`, `finisher_rule`)
- Finisher rule: harmless everywhere (Liquid Flame 240/240 on its own).
- Potion rule as first written (allowed only if the Potion lifts the ally out of the strongest hit's reach, or fills 40% of the gap): Liquid Flame 232/240 vs 240/240 without.
- Fix: the rule applies only while a healer can still cast, and a Potion is also allowed whenever the ally is within reach of the strongest hit (`a.hp <= hi`). Liquid Flame 240/240. Pages (enc 93, 12 -> 15 of 60), Ifrit (enc 494, 10 -> 15 of 60) and Castle Karnak (enc 86, 57/60 either way) are not worse.
- Not re-measured: Byblos with the relaxed rule (the old state is lost; the relaxed rule only permits more Potions than the version that gave 60/60).
