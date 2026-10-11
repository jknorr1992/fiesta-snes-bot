"""Run with: python3 -m pytest ffv-bot/battle/tests   (or python3 ffv-bot/battle/tests/test_battle.py)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import BattleSim, DEFAULT_ROM
from data import GameData
from field import Field
from scenario import run_battle

ROM = open(DEFAULT_ROM, "rb").read() if os.path.exists(DEFAULT_ROM) else None
try:
    import pytest
    needs_rom = pytest.mark.skipif(ROM is None, reason="ROM not available")
except ImportError:
    needs_rom = lambda f: f

@needs_rom
def test_names():
    d = GameData(ROM)
    assert "Stray Cat" in d.battle_summary(5)
    assert d.map_encounters(166)["battles"][0][0] == 0x5C

@needs_rom
def test_simple_victory_is_deterministic():
    d = GameData(ROM)
    a = run_battle(Field.new(d, party=4), 5, rom=ROM)
    b = run_battle(Field.new(d, party=4), 5, rom=ROM)
    assert a.outcome == "victory" and a.monsters_left == 0
    assert (a.frames, a.gil_gained, a.exp_gained) == (b.frames, b.gil_gained, b.exp_gained)

@needs_rom
def test_ancient_library_crash_is_detected():
    d = GameData(ROM)    # ROM bug at 0x24C75 (0xA9, should be 0xE8): reported as a crash, never patched
    assert run_battle(Field.new(d, party=4), 0x5C, rom=ROM, max_frames=20000).outcome == "crash"

@needs_rom
def test_snapshot_restore_replays_identically():
    d = GameData(ROM)
    sim = BattleSim(ROM); sim.load_wram(Field.new(d, party=4).to_wram()); sim.start(5, 0)
    sim.step(300); snap = sim.snapshot()
    sim.step(600); first = sim.dump_wram()[0x2000:0x2600]
    sim.restore(snap); sim.step(600)
    assert sim.dump_wram()[0x2000:0x2600] == first

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
