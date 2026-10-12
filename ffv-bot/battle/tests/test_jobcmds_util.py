"""Utility job commands (jobcmds_util.py) in real battles: every command is forced through the plugin's own Command builder and the result in
the game is checked against what the plugin assumes.  Run with: python3 -m pytest ffv-bot/battle/tests/test_jobcmds_util.py
(or python3 ffv-bot/battle/tests/test_jobcmds_util.py)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import BattleSim, Command, DEFAULT_ROM
from data import GameData
from field import Field, RNG_SEED, MAGIC_BITS
from jobs import JobSwitcher
from knowledge import Bestiary
from policy import VeteranPolicy
import jobcmds
import jobcmds_util as U
import magicpolicy

ROM = open(DEFAULT_ROM, "rb").read() if os.path.exists(DEFAULT_ROM) else None
try:
    import pytest
    needs_rom = pytest.mark.skipif(ROM is None, reason="ROM not available")
except ImportError:
    needs_rom = lambda f: f

LEVEL = 20
ENC = 104                         # three monsters of 700 HP, Defense 20, no counters
BEASTS = 33                       # three Bander Snatches: creature type 4
WHIRL = 126                       # two Whirl Demons of 1000 HP: Release is a magic attack on every enemy
STORE = {}
POTION, ANTIDOTE, ETHER, GOLD_NEEDLE, DARK_MATTER = 0xE0, 0xE8, 0xE2, 0xEC, 0xEB
SWORD = 15                        # a plain sword for everybody: without a weapon a character does not attack at all


def world():
    if not STORE:
        d = GameData(ROM); STORE.update(d=d, b=Bestiary(d), js=JobSwitcher(d, ROM), sim=BattleSim(ROM))
        STORE["sim"].latency = 12
    return STORE["d"], STORE["b"], STORE["js"], STORE["sim"]


def party(job, commands, items=None, spells=()):
    """Four starting characters at LEVEL, the first one switched to `job` with the command ids `commands`."""
    d, b, js, sim = world()
    f = Field.new(d, party=4); bag = f.items(); w = f.to_wram()
    w, _ = js.switch(w, 0, job, bag)
    f.wram[:] = w
    for k, q in (items or {}).items(): bag[k] = bag.get(k, 0) + q
    f.set_items(bag)
    for i in range(4):
        c = f.character(i); c.level = LEVEL; c.rh_weapon = SWORD; f.set_character(i, c)
    w = f.to_wram()
    for i in range(4): w = js.set_commands(w, i, commands if i == 0 else f.character(i).commands)       # HP / MP follow the level
    f.wram[:] = w
    for s in spells: f.wram[MAGIC_BITS + (s >> 3)] |= 0x80 >> (s & 7)
    for i in range(4):
        ch = f.character(i); ch.hp = ch.max_hp; ch.mp = ch.max_mp; f.set_character(i, ch)
    return f.to_wram()


class Probe:
    """Everybody but character 0 guards; character 0 gets the command `make(probe, sim, me, mons)` builds on its first menu, then guards."""
    mode = "direct"; died = False

    def __init__(self, make, enc=ENC, forbidden=None, hard=False, others=None, then=None):
        d, b, js, sim = world()
        self.enc, self.make, self.menus, self.others, self.then = enc, make, [], others, then
        self.policy = VeteranPolicy(d, b, d.battle(enc)["monster_ids"], hard=hard, forbidden=forbidden)
        self.info = None

    def plugin(self, name, kind): return next(c for c in self.policy.job_cmds if c.name == name and c.kind == kind)

    def __call__(self, sim, char):
        if char != 0: return self.others(self, sim, char) if self.others and self.menus else Command.guard()
        self.menus.append(sim.frame)
        if len(self.menus) > 2 or len(self.menus) == 2 and not self.then: return Command.guard()
        me = sim.party[0]; mons = [m for m in sim.monsters if not m.dead and m.hp > 0]
        self.policy(sim, 0)                                            # a look at the scripts fills the policy's threat tables
        return (self.then if len(self.menus) == 2 else self.make)(self, sim, me, mons)


def run(w, probe, seed=0, frames=500, watch=None, limit=1 << 30):
    """Battle until `frames` after character 0's first command; `watch(sim)` is called every frame after it."""
    d, b, js, sim = world()
    ww = bytearray(w); ww[RNG_SEED] = seed
    sim.load_wram(bytes(ww)); sim.log = []; sim.set_direct(None); sim.set_direct(probe); sim.start(probe.enc, 21)
    t0 = None
    while sim.running and sim.frame < limit:
        sim.step()
        if t0 is None and probe.menus: t0 = sim.frame
        if t0 is not None:
            if watch: watch(sim, sim.frame - t0)
            if sim.frame - t0 >= frames: break
    return sim


def hp(sim, i): return sim.combatant(i).hp


@needs_rom
def test_forbidden_by_default():
    d, b, js, sim = world()
    p = VeteranPolicy(d, b, d.battle(ENC)["monster_ids"])
    assert set(jobcmds.FORBIDDEN_DEFAULT) == {"Mix", "Gil Toss"} == p.forbidden
    names = {c.name for c in p.job_cmds}
    assert {"Mix", "Recover", "Revive", "Terrain", "Sing", "Dance", "Flirt", "Mimic", "Catch", "Calm", "Escape"} <= names


@needs_rom
def test_recover_cures_the_whole_party():
    w = party("Chemist", [5, U.CMD_RECOVER, 2, 0])
    def make(pr, sim, me, mons):
        for i, (o, v) in enumerate(((0x1A, 0x05), (0x1B, 0x50), (0x1A, 0x20))):          # poison + blind, sleep + charm, toad
            sim.w[sim.party[i + 1].b + o] |= v
        pr.info = pr.plugin("Recover", "only").propose(sim, 0, me, mons)
        return pr.info[1]
    pr = Probe(make)
    sim = run(w, pr, frames=300)
    assert pr.info[1].command == U.CMD_RECOVER and pr.info[1].party == 0xF0
    assert all(bytes(sim.w[c.b + 0x1A:c.b + 0x1D]) == bytes(3) for c in sim.party)


@needs_rom
def test_revive_gives_a_sixteenth_of_the_hp_and_all_mp():
    w = party("Chemist", [5, U.CMD_REVIVE, 2, 0])
    def make(pr, sim, me, mons):
        for i in (2, 3):
            c = sim.party[i]; sim.w[c.b + 0x1A] = 0x80
            for o in (6, 7, 0xA, 0xB): sim.w[c.b + o] = 0
        pr.info = pr.plugin("Revive", "only").propose(sim, 0, me, mons)
        return pr.info[1]
    pr = Probe(make)
    sim = run(w, pr, frames=300)
    for i in (2, 3):
        c = sim.party[i]
        assert not c.dead and c.hp == c.max_hp // 16 and c.mp == c.max_mp


@needs_rom
def test_item_and_drink_cure_stone():
    for cid, name in ((U.CMD_ITEM, "Item"), (U.CMD_DRINK, "Drink")):
        w = party("Chemist", [5, U.CMD_DRINK, 2, 0], items={ANTIDOTE: 2, GOLD_NEEDLE: 1})
        def make(pr, sim, me, mons):
            sim.w[sim.party[2].b + 0x1A] |= 0x40
            pr.info = pr.plugin(name, "support").propose(sim, 0, me, mons)
            return pr.info[1]
        pr = Probe(make)
        sim = run(w, pr, frames=300)
        assert pr.info[1].command == cid and sim.w[sim.party[2].b + 0x1A] == 0


@needs_rom
def test_terrain_hits_something():
    w = party("Geomancer", [5, U.CMD_TERRAIN, 2, 0])
    seen = []
    for seed in range(4):
        pr = Probe(lambda pr, sim, me, mons: (setattr(pr, "info", pr.plugin("Terrain", "offense").propose(sim, 0, me, mons)), pr.info[1])[1])
        sim = run(w, pr, seed=seed, frames=500)
        seen.append(sum(700 - hp(sim, i) for i in (4, 5, 6)))
    assert pr.info[0] > 0 and max(seen) > 0


@needs_rom
def test_dance_damage_is_drain_or_the_sword_dance():
    """Of the four dances the damaging ones are Drain (a spell) and the sword dance (a Fight with double Attack and M that never misses)."""
    w = party("Dancer", [5, U.CMD_DANCE, 2, 0])
    seen = set()
    for seed in range(0, 256, 8):
        def make(pr, sim, me, mons):
            M = magicpolicy.spells_of(pr.policy)
            pr.info = (pr.plugin("Dance", "offense").propose(sim, 0, me, mons), U.phys.phys_range(sim, me, mons[0], dm=0xD0), M.dmg(sim, me, U.DRAIN, mons[0]))
            return pr.info[0][1]
        pr = Probe(make)
        sim = run(w, pr, seed=seed, frames=500)
        dealt = 700 - hp(sim, 4)
        (_, (slo, shi), (dlo, dhi)) = pr.info
        kind = "drain" if dlo <= dealt <= dhi else "sword" if slo <= dealt <= shi else "other"      # other: Charm (the charmed hit each other), Psyche
        assert kind != "other" or dealt < dlo, (seed, dealt, pr.info)
        seen.add(kind)
    assert {"drain", "sword"} <= seen


@needs_rom
def test_sing_level_song_lifts_the_party_by_about_17():
    w = party("Bard", [5, U.CMD_SING, 2, 0], spells=range(87, 96))
    got = []
    def make(pr, sim, me, mons): return Command(U.CMD_SING, 0x20, 0, Command.party_mask(0), U.SONG_LEVEL)
    pr = Probe(make)
    sim = run(w, pr, frames=4200, watch=lambda s, f: got.append(s.w[s.party[2].b + 0x78]) if f % 100 == 0 else None)
    assert abs(max(got) - U.SONG_BONUS) <= 3 and got == sorted(got)       # 15 .. 17 depending on the singer's timers


@needs_rom
def test_sing_proposes_a_song_in_a_long_fight():
    w = party("Bard", [5, U.CMD_SING, 2, 0], spells=range(87, 96))
    pr = Probe(lambda pr, sim, me, mons: (setattr(pr, "info", pr.plugin("Sing", "attack").propose(sim, 0, me, mons)), Command.guard())[1])
    run(w, pr, frames=10)
    assert pr.info is None or pr.info[1].command == U.CMD_SING


@needs_rom
def test_catch_and_release():
    """A monster at critical HP is caught (it counts as dead, the Tamer keeps its id); Release then does the damage the plugin computes."""
    w = party("Tamer", [5, U.CMD_CATCH, 2, 0])
    d, b, js, sim = world()
    mid = d.battle(WHIRL)["monster_ids"][0]
    def catch(pr, sim, me, mons):
        m = mons[0]; sim.w[m.b + 6] = 5; sim.w[m.b + 7] = 0; sim.w[m.b + 0x1D] |= 2           # critical HP
        pr.info = pr.plugin("Catch", "offense").propose(sim, 0, me, mons)
        return pr.info[1]
    def release(pr, sim, me, mons):
        pr.before = hp(sim, 5); pr.caught = sim.w[me.b + U.O_CAUGHT]; pr.t1 = sim.frame
        pr.release = pr.plugin("Catch", "offense").propose(sim, 0, me, mons)
        return pr.release[1]
    pr = Probe(catch, enc=WHIRL, then=release, others=lambda pr, sim, ch: Command.guard())
    def watch(sim, f):
        if getattr(pr, "t1", None) is not None and sim.frame == pr.t1 + 150: pr.after = hp(sim, 5)
    sim = run(w, pr, frames=2200, watch=watch)
    assert pr.info[1].command == U.CMD_CATCH and pr.caught == mid and pr.release[1].command == U.CMD_RELEASE
    rec, caster = U.release_attack(pr.policy, mid)
    lo, hi = magicpolicy.spells_of(pr.policy).dmg(sim, caster, rec, sim.combatant(5))
    assert lo <= pr.before - pr.after <= hi


@needs_rom
def test_calm_stops_a_beast():
    w = party("Tamer", [5, U.CMD_CALM, 2, 0])
    states = []
    pr = Probe(lambda pr, sim, me, mons: Command(U.CMD_CALM, 0x80, Command.monster_mask(0)), enc=BEASTS)
    run(w, pr, frames=600, watch=lambda s, f: states.append((f, s.w[s.combatant(4).b + 0x1C] & 0x10)))
    on = [f for f, v in states if v]
    assert on and 250 < on[-1] - on[0] < 700           # 2 x 30 + (20 - 9) / 4 timer ticks of about 5.35 frames, give or take the battle speed


@needs_rom
def test_mix_is_forbidden_by_default_and_works_when_allowed():
    items = {DARK_MATTER: 4}
    w = party("Chemist", [5, U.CMD_MIX, 2, 0], items=items)
    d, b, js, sim = world()
    p = VeteranPolicy(d, b, d.battle(ENC)["monster_ids"])
    assert "Mix" in p.forbidden
    pr = Probe(lambda pr, sim, me, mons: (setattr(pr, "info", pr.plugin("Mix", "attack").propose(sim, 0, me, mons)), pr.info[1])[1], forbidden=[])
    sim = run(w, pr, frames=500)
    assert pr.info[1].command == U.CMD_MIX and hp(sim, 4) < 700 and U.stock(sim)[DARK_MATTER][1] == 2


@needs_rom
def test_escape_leaves_the_battle():
    w = party("Thief", [5, U.CMD_ESCAPE, 2, 0])
    pr = Probe(lambda pr, sim, me, mons: Command(U.CMD_ESCAPE, 0x80))
    sim = run(w, pr, frames=2000)
    assert sim.battle_over == 1


@needs_rom
def test_mime_guards_with_nothing_to_copy_and_berserker_needs_no_menu():
    w = party("Mime", [U.CMD_MIMIC, 0, 0, 0])
    got = []
    pr = Probe(lambda pr, sim, me, mons: (got.append(pr.plugin("Mimic", "only").propose(sim, 0, me, mons)), Command.guard())[1])
    run(w, pr, frames=20)
    assert got[0][1].command == Command.guard().command
    w = party("Berserker", [5, 0, 0, 0])
    pr = Probe(lambda pr, sim, me, mons: Command.guard())
    run(w, pr, limit=600)
    assert not pr.menus                                            # always berserk: the game attacks on its own


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
