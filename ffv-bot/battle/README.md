# Battle simulator

A 1:1 simulator of Final Fantasy V battles. It does not re-implement the battle rules: it runs the **actual battle
program from the ROM** (`ExecBattle`, C2:0000) on a 65C816 interpreter written in C (`cpu65816.c`, driven from
Python through `cpu.py`). Damage, ATB, AI scripts, status effects, RNG and the FF5r Clean hack's changes (Active ATB
etc.) therefore come from the real code and data. Only the SNES hardware the battle code touches is modelled:
HiROM mapping, 128K WRAM, the multiply/divide unit, joypads, NMI/vblank timing, DMA/HDMA cycle stealing, DRAM refresh and
(optionally) an SPC700 running the real sound driver. PPU output is dropped, so it runs far faster than real time:
~80x real time per core (a typical 25 s fight takes ~0.35 s), ~320x on 4 cores with `bulk.py`. The idle waits between
frames are skipped to the next NMI.

ROM: `/mnt/project-files/FFV/Final Fantasy 5 (Japan) (patched)(2).sfc` (`sim.DEFAULT_ROM`, override with `FFV_ROM` or `--rom`).

## Quick start

```
python3 battle/run.py battle 0x5d            # what's in an encounter
python3 battle/run.py map 166                # random battles of a map (with probabilities)
python3 battle/run.py world 3                # random battles of a world-map region
python3 battle/run.py fight 5                # fight it with a fresh 4-member party
python3 battle/run.py fight 0x5d --state s.state4   # ...with the party, gear and items from a RetroArch save state
python3 battle/run.py sweep                  # run all battles, list crashes
```

```python
from battle.data import GameData
from battle.field import Field
from battle.scenario import run_battle
from battle.sim import BattleSim, Command

d = GameData(open(ROM, "rb").read())
f = Field.new(d, party=4)                       # or Field.from_state(path) / Field.from_live(game) / Field.from_wram(wram)
f.chars[0].set_equipment(d, rh_weapon="MageMash", head=178)   # names (9 chars, often ambiguous: see find_items) or ids
f.chars[0].set_job(d, "Knight")                 # job commands/equip masks/passives from the ROM tables
f.set_items({"Potion": 10, "PhenixDwn": 2}); f.set_magic(spell_ids=[0, 1])
res = run_battle(f, encounter=0x05d, background=0x12)     # BattleResult: outcome, frames, gil, exp, items, party_after
```

Describe the party in plain terms (no WRAM, no save state): `party.json`, checked against the ROM tables (`party.py`):

```json
{"gil": 2000,
 "party": [{"character": "Bartz", "job": "Knight", "level": 25, "row": "front",
            "equipment": {"right_hand": "Broad", "left_hand": "shield:Mythril", "head": "Plumed", "body": "Copper", "accessory": "Guard"}},
           {"character": "Lenna", "job": "WhiteMage", "level": 25, "row": "back", "hp": 0.5}],
 "items": {"Potion": 10, "Hi-Potion": 3},
 "magic": ["Cure", "Cure2", "black:Fire"]}
```

```
python3 battle/run.py fight 0x5d --spec party.json      # also: bulk.py ... --spec party.json, Field.from_spec(data, spec)
python3 battle/run.py check party.json                  # validate and show the resulting characters
python3 battle/run.py items --slot head --job Monk      # what the ROM lets that job wear there (jobs / spells list the names)
```

Anything illegal raises `SpecError` listing every problem. Checked from the ROM: the character (Bartz, Lenna, Galuf/Krile, Faris; once each, 1-4 in
the party), the job, level 1-99 (exp, max HP and max MP are *derived* with the game's own exp table and `LevelUp` HP/MP formula; `hp`/`mp` can
only be "full", a 0-1 fraction or a number up to the maximum), each equipment slot (the item must exist, fit the slot per its property record,
and be wearable by the job per the same equip-class test the in-game menu uses; a hand takes a weapon or a shield), inventory items (real
items only, 1-99 each) and spell ids. Item names are the ROM's 9-character names; when a name is shared write `category:Name`
(`shield:Mythril`, `dagger:Mythril`, `black:Fire`) or give the id, and an ambiguous name is an error that lists the candidates. Which spells a
character can cast comes from the job's commands (`EnableSpells` is derived the way the menu does). Not covered: learned job abilities beyond the
job's own commands, mastery bonuses, status effects.

Statistics over many battles (all cores, `bulk.py`): `python3 battle/bulk.py 0x5d --runs 1024 --jobs 4 [--state s.state4]` prints
outcome rates, duration percentiles, HP lost, gil and item drops. The variation between runs is the game's own RNG state
(`seed` 0-255, the starting index of the ROM's random table); a reused worker sim gives the same result as a fresh one. From Python:
`run_many([Job(0x5d, 0x12, seed) ...], {"default": field.to_wram()}, processes=4)`.

Stepping manually at full speed (this is what the bot would use for what-if search; `snapshot`/`restore` are ~130 KB copies):

```python
sim = BattleSim(); sim.load_wram(f.to_wram()); sim.start(0x5d, 0x12)
sim.set_direct(lambda sim, char: Command.fight(0))   # called whenever a character's ATB fills; returns the command
while sim.running and not sim.battle_over: sim.step(1)                        # one video frame; sim.party / sim.monsters / sim.battle_over
snap = sim.snapshot(); ...; sim.restore(snap)
```

## Fidelity

* **Commands.** `set_direct` (default) injects the command at the point the game opens the menu (`C1:39F9`), which is
  fast and exact for the battle logic. `PadFightPolicy`/`sim.press()` drive the real menu with joypad taps instead;
  use that when menu timing itself matters. The two can differ by a few frames.
* **Timing is cycle-exact, with no fudge factor** (`CYC_SCALE = 0`). The interpreter charges every bus access its real
  master-cycle cost (WRAM 8, PPU/CPU registers 6/12, SlowROM 8, FastROM 6 when `$420D` says so, HiROM banks C0-FF have no I/O
  window), the internal cycles of each opcode (table `iotab.h`, generated by `gen_iotab.py`), indexed-read page-cross and
  16-bit-index penalties, direct-page-low penalty, taken branches, MVN/MVP, DMA (8/byte + 8/channel + 16), HDMA
  (18/line + 8/channel + 8/byte + table reads, lines 0-224), the 40-cycle DRAM refresh each scanline, the auto-joypad busy
  window and H-blank flag in `$4212`, and the H/V counter latch. Sound calls are a constant stall by default;
  `BattleSim.use_spc()` runs the real driver on an SPC700 core (`spc700.h`) so CPU stalls handshaking with it are real
  (it moves whole-battle timing by under 1 frame, so it is off by default).
* **Validated against real snes9x** with the libretro core run headless. Nothing here ever modifies the ROM: battles are forced
  with WRAM pokes and then the same CPU+WRAM+hardware state is resumed in the sim.
  - `validate/diff_battle.py ENC`: HP/MP/status changes of a whole battle. Encounters 5, 20, 150, 300 (2600 frames each, 15-34
    events): identical values and order; event times within 0-1 frame for the first ~1500 frames, worst drift 5 frames after 2600 (0.2 %).
  - `validate/iter_timing.py ENC`: frames per battle main-loop iteration, identical histogram (147x2, 62x3, 7x4 ...) and order.
  - `validate/call_time.py`: calls one routine (e.g. the whole battle NMI handler, C1:19EA, ~141k cycles) from an identical state
    in both emulators: snes9x 141 380 cycles, sim 141 432 (0.04 %); DMA routines within 4 cycles.
  - `validate/opsweep.py` (every non-control opcode in all four M/X modes), `stackbench.py` (stack/call/jump opcodes),
    `hdmabench.py` (HDMA table shapes), `microbench.py` (DMA, ROM speeds, MVN), `nmi_time.py`, `soundtime.py`: per-feature cycle checks.
  Known residuals: `(sr,S),Y` is 1 internal cycle slow, `BIT abs,X` lacks the index penalty, `PER` is 1 cycle fast (none occur in the
  battle code paths measured). Near-simultaneous events (a poison tick vs an enemy action) could in principle still swap order in
  very long fights.
* Not modelled: PPU output; sprite/animation waits run as fast as the game's own waits allow (their cost is real, the pictures are not drawn);
  decimal-mode 8-bit SBC is approximate (unused by the battle code).

## Crash sweep (ROM `Final Fantasy 5 (Japan) (patched)(2).sfc`)

`run.py sweep` (level-1 party, serial ~3 min; the same sweep through `bulk.py` takes 70 s on 4 cores): **none of the 511 non-empty
battles crashes**, Ancient Library (map 166) included. Eight time out at 20000 frames only because a level-1 party cannot beat
them (0xd1, 0x16e, 0x1a1, 0x1b1, 0x1e4, bosses 0x1f7, 0x1fe, 0x1ff); see `crashing_battles.json`.

History: the earlier `(patched)(1).sfc` came from an IPS meant for a different romhack; it wrote `0xA9` at `0x24C75` (base `0xE8`),
which crashed all blue-magic battles (87 of them). That ROM is no longer used. The simulator never modifies any ROM or IPS;
`run.py --rom PATH` (or env `FFV_ROM`) selects the ROM, default `sim.DEFAULT_ROM`.

## Files

`cpu65816.c`/`cpu.py` interpreter and ctypes wrapper (built on demand into `_build/`); `sim.py` `BattleSim`;
`data.py` ROM tables (monsters, battles, encounter groups, jobs, names); `field.py` field-state builder (the WRAM
the battle reads: characters, equipment, items, magic); `scenario.py` `run_battle`; `dis65816.py` small disassembler
for debugging; `run.py` CLI; `bulk.py` parallel runner; `party.py` checked party/equipment/items description; `tests/` (`python3 battle/tests/test_battle.py`); `validate/` snes9x differential test.
