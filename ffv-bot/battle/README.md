# Battle simulator

A 1:1 simulator of Final Fantasy V battles. It does not re-implement the battle rules: it runs the **actual battle
program from the ROM** (`ExecBattle`, C2:0000) on a small 65C816 interpreter written in C (`cpu65816.c`, driven from
Python through `cpu.py`). Damage, ATB, AI scripts, status effects, RNG and the FF5r Clean hack's changes (Active ATB
etc.) therefore come from the real code and data. Only the SNES hardware the battle code touches is modelled:
HiROM mapping, 128K WRAM, the multiply/divide unit, joypads, NMI/vblank timing, and WRAM-port DMA. PPU, APU and
graphics DMA are dropped (sound calls are skipped), so it runs at max speed: a whole small battle takes ~0.25 s,
the idle waits between frames are skipped to the next NMI.

ROM: always `/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc` (`sim.DEFAULT_ROM`).

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
* **Validated against real snes9x** (`validate/diff_battle.py`, uses the libretro snes9x core headless): a battle is
  forced in the emulator, then the simulator is resumed from the same CPU+WRAM state with no input. All HP/MP/status
  changes come out identical in value and order (22 events over 2600 frames on a 4-cat fight); event timing is within
  ~0.3-1 %. The residual drift is because the interpreter charges bus cycles only, so `CYC_SCALE` adds 6 % for internal
  cycles and DRAM refresh. Near-simultaneous events (e.g. a poison tick vs an enemy action) can swap order in long fights.
* Not modelled: PPU/APU timing effects (e.g. animation-length waits are as fast as the game's own waits allow),
  decimal-mode 8-bit SBC is approximate (unused by the battle code).

## ROM bug found: Ancient Library battles crash

`ROM[0x24C75]` is `0xA9` in the shipped `(patched)(1).sfc`; the base ROM `Final Fantasy 5 (tr).sfc` has `0xE8` (INX) there
in the blue-magic "learn" loop. `(patched)(1).sfc` is byte-for-byte the base ROM plus `FF5r Super Special Deep Clean.ips`, and
that IPS contains the record (offset 0x24C75, 1 byte, A9). As shipped the code runs `LDA #$E0 / PHP / BRK` and the BRK vector
`$0000` executes RAM as code. All four map-166 (Ancient Library) battles hit it, and any battle with a monster that casts blue
magic; on the base ROM the same battles run normally. The simulator reports `outcome == "crash"`. This is a diagnosis only:
**the ROMs and the IPS are never modified** (project rule), so the bot must avoid or otherwise handle these battles.
Use `run.py --rom PATH` to simulate against either ROM.

Sweep on the unmodified ROM (`run.py sweep`, level-1 party, ~3 min): **87 of 511 non-empty battles crash** (list with
monster names in `crashing_battles.json`); 6 more time out at 20000 frames only because a level-1 party cannot beat them (0xd1, 0x16e, 0x1a1, 0x1b1, 0x1e4, bosses 0x1f7/0x1fe/0x1ff).
Avoiding or handling these battles is up to the bot; the sim never patches the ROM.

## Files

`cpu65816.c`/`cpu.py` interpreter and ctypes wrapper (built on demand into `_build/`); `sim.py` `BattleSim`;
`data.py` ROM tables (monsters, battles, encounter groups, jobs, names); `field.py` field-state builder (the WRAM
the battle reads: characters, equipment, items, magic); `scenario.py` `run_battle`; `dis65816.py` small disassembler
for debugging; `run.py` CLI; `tests/` (`python3 battle/tests/test_battle.py`); `validate/` snes9x differential test.
