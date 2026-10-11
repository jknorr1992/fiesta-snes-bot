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
def test_ancient_library_battles_run():
    d = GameData(ROM)    # these crashed on an older, wrongly patched ROM (blue-magic loop byte 0x24C75)
    for enc in (0x5C, 0x5D, 0x62, 0x65):
        assert run_battle(Field.new(d, party=4), enc, rom=ROM, max_frames=20000).outcome != "crash"

@needs_rom
def test_snapshot_restore_replays_identically():
    d = GameData(ROM)
    sim = BattleSim(ROM); sim.load_wram(Field.new(d, party=4).to_wram()); sim.start(5, 0)
    sim.step(300); snap = sim.snapshot()
    sim.step(600); first = sim.dump_wram()[0x2000:0x2600]
    sim.restore(snap); sim.step(600)
    assert sim.dump_wram()[0x2000:0x2600] == first

@needs_rom
def test_reused_sim_matches_fresh_sim():
    d = GameData(ROM); sim = BattleSim(ROM); fr = []
    for seed in (1, 3, 5):
        f = Field.new(d, party=4, rng_seed=seed)
        a = run_battle(f, 5, sim=sim); b = run_battle(f, 5, rom=ROM)
        assert (a.outcome, a.frames, a.exp_gained) == (b.outcome, b.frames, b.exp_gained)
        fr.append(a.frames)
    assert len(set(fr)) > 1       # the RNG seed really changes the fight

@needs_rom
def test_bulk_runner_matches_serial():
    from bulk import Job, run_many
    f = {"default": Field.new(GameData(ROM), party=4).to_wram()}
    jobs = [Job(5, 0, s) for s in range(4)]
    assert run_many(jobs, f, processes=2) == run_many(jobs, f, processes=1)

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
