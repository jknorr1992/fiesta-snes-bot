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

@needs_rom
def test_party_spec_builds_legal_party():
    import party
    d = GameData(ROM)
    f = party.build_field(d, {"gil": 99, "party": [
        {"character": "Bartz", "job": "Knight", "level": 25, "equipment": {"right_hand": "Broad", "left_hand": "shield:Mythril", "head": "Plumed"}},
        {"character": "Lenna", "job": "WhiteMage", "level": 25, "hp": 0.5}], "items": {"Potion": 10}, "magic": ["Cure", "black:Fire"]})
    k, w = f.character(0), f.character(1)
    assert (k.job, k.level, k.exp, k.max_hp) == (0, 25, d.level_exp(25), 737) and k.rh_weapon == 13 and k.lh_shield == 0x84 and k.head == 0x90
    assert w.hp == round(w.max_hp * 0.5) and w.raw[0x3D] == 0x06          # white magic level 6 from the job's commands
    assert not f.character(2).in_party and f.gil == 99 and f.items() == {0xE0: 10}
    assert run_battle(f, 5, rom=ROM).outcome == "victory"

@needs_rom
def test_party_spec_rejects_illegal_values():
    import party
    d = GameData(ROM)
    def problems(spec):
        try: party.build_field(d, spec)
        except party.SpecError as e: return "\n".join(e.problems)
        raise AssertionError("accepted illegal spec")
    ok = {"character": "Lenna", "job": "WhiteMage"}
    assert "level" in problems({"party": [{**ok, "level": 100}]})
    assert "unknown job" in problems({"party": [{**ok, "job": "Gunner"}]})
    assert "cannot go there" in problems({"party": [{**ok, "equipment": {"right_hand": "Excalibur"}}]})        # job's weapon classes
    assert "cannot go there" in problems({"party": [{**ok, "equipment": {"head": "Broad"}}]})                 # wrong slot
    assert "ambiguous" in problems({"party": [{**ok}], "items": {"Mythril": 1}})
    assert "quantity" in problems({"party": [{**ok}], "items": {"Potion": 100}})
    assert "no item named" in problems({"party": [{**ok}], "items": {"Nonsense": 1}})
    assert "listed twice" in problems({"party": [ok, ok]})
    assert "1-4" in problems({"party": []})
    assert "hp" in problems({"party": [{**ok, "hp": 99999}]})

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)


@needs_rom
def test_enemy_self_heals_from_scripts():
    from knowledge import Bestiary
    B = Bestiary(GameData(ROM))
    h = B.heals(267)                      # Liquid Flame form 3: Fire2 on itself, an element it absorbs; measured +265 per cast (50..56 x 5)
    assert len(h) == 1 and h[0].whom == "self" and h[0].lo <= 265 <= h[0].hi and h[0].cancelled_by_hit
    assert B.heals(265) == []             # form 1 only attacks
    assert any(x.whom == "allies" for x in B.heals(111))      # Cure Beast heals the others
