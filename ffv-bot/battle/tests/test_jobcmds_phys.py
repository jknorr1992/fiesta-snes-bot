"""Physical job commands (jobcmds_phys.py) in real battles: every command is forced through the plugin's own Command builder and the result in
the game is checked against the formula's lowest / highest roll.  Run with: python3 -m pytest ffv-bot/battle/tests/test_jobcmds_phys.py
(or python3 ffv-bot/battle/tests/test_jobcmds_phys.py)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import BattleSim, Command, DEFAULT_ROM, CHAR
from data import GameData
from field import Field, RNG_SEED
from jobs import JobSwitcher
from knowledge import Bestiary
from policy import VeteranPolicy
import jobcmds
import jobcmds_phys as P

ROM = open(DEFAULT_ROM, "rb").read() if os.path.exists(DEFAULT_ROM) else None
try:
    import pytest
    needs_rom = pytest.mark.skipif(ROM is None, reason="ROM not available")
except ImportError:
    needs_rom = lambda f: f

LEVEL = 15
ENC = 104                         # three monsters of 700 HP, Defense 20, no counters
STORE = {}


def world():
    if not STORE:
        d = GameData(ROM); STORE.update(d=d, b=Bestiary(d), js=JobSwitcher(d, ROM), sim=BattleSim(ROM))
        STORE["sim"].latency = 12
    return STORE["d"], STORE["b"], STORE["js"], STORE["sim"]


def party(job, commands, equip=None, items=None, gil=None):
    """Four starting characters at LEVEL, the first one switched to `job` with `commands` and `equip` ({'rh_weapon': id, ...})."""
    d, b, js, sim = world()
    f = Field.new(d, party=4); bag = f.items(); w = f.to_wram()
    w, _ = js.switch(w, 0, job, bag)
    f.wram[:] = w
    for k, q in (items or {}).items(): bag[k] = bag.get(k, 0) + q
    f.set_items(bag)
    ch = f.character(0)
    for slot, item in (equip or {}).items(): setattr(ch, slot, item)
    f.set_character(0, ch)
    for i in range(4):
        c = f.character(i); c.level = LEVEL; f.set_character(i, c)
    w = f.to_wram()
    for i in range(4): w = js.set_commands(w, i, commands if i == 0 else f.character(i).commands)       # HP / MP follow the level
    f.wram[:] = w
    for i in range(4):
        ch = f.character(i); ch.hp = ch.max_hp; ch.mp = ch.max_mp; f.set_character(i, ch)
    if gil is not None: f.wram[P.GIL:P.GIL + 3] = gil.to_bytes(3, "little")
    return f.to_wram()


def gil_of(sim): return int.from_bytes(bytes(sim.w[P.GIL:P.GIL + 3]), "little")


class Probe:
    """Everybody but character 0 guards; character 0 gets the command `make(probe, sim, me, mons)` builds on its first menu, then guards."""
    mode = "direct"; died = False

    def __init__(self, name, make, forbidden=None, enc=ENC):
        d, b, js, sim = world()
        self.enc, self.make, self.menus = enc, make, []
        self.policy = VeteranPolicy(d, b, d.battle(enc)["monster_ids"], forbidden=forbidden)
        self.plugin = next(c for c in self.policy.job_cmds if c.name == name)

    def __call__(self, sim, char):
        if char != 0: return Command.guard()
        self.menus.append(sim.frame)
        if len(self.menus) > 1: return Command.guard()
        me = sim.party[0]; mons = [m for m in sim.monsters if not m.dead and m.hp > 0]
        self.start = dict(mons=[(m.i, m.hp) for m in mons], gil=gil_of(sim), inv=P.snapshot(sim), hp=me.hp)
        self.fv = self.policy.fight_value(sim, me, mons)
        return self.make(self, sim, me, mons)


def run(w, probe, seed, frames):
    """Battle until `frames` after character 0's first command; fills probe.events [(frames after the command, combatant, hp change)] and
    probe.end (what the shared sim holds at the end: inventory, gil, which monsters are dead, which have been stolen from)."""
    d, b, js, sim = world()
    ww = bytearray(w); ww[RNG_SEED] = (seed * 37 + 1) & 0xFF                      # neighbouring seeds give the same battle: spread them
    sim.load_wram(bytes(ww)); sim.log = []; sim.set_direct(None); sim.set_direct(probe); sim.start(probe.enc, 0)
    probe.events = []; prev = {}; t0 = None
    while sim.running and sim.frame < 60000:
        sim.step()
        if not probe.menus: continue
        t0 = probe.menus[0] if t0 is None else t0
        if sim.frame > t0 + frames: break
        for i in range(12):
            if i >= 4 and not sim.rb(0x3EC2 + i): continue
            hp = sim.combatant(i).hp
            if i in prev and prev[i] != hp: probe.events.append((sim.frame - t0, i, hp - prev[i]))
            prev[i] = hp
    probe.end = dict(inv=P.snapshot(sim), gil=gil_of(sim), dead=[bool(sim.rb(CHAR + 0x80 * i + P.O_ST1) & 0x80) for i in range(12)],
                     stolen=[sim.rb(CHAR + 0x80 * i + P.O_STOLEN) for i in range(12)], hp=sim.party[0].hp)


def forced(name, w, make, seeds=(0,), frames=1100, forbidden=None, enc=ENC):
    """Run the plugin's command `make(probe, sim, me, mons) -> Command` once per seed; returns the probes that got their turn."""
    out = []
    for s in seeds:
        pr = Probe(name, make, forbidden, enc); run(w, pr, s, frames)
        if pr.menus: out.append(pr)                   # (the party can fall before character 0 has its turn)
    assert out, "character 0 never got a turn"
    return out


def inside(x, rng, crit):
    """x is a roll of the formula, or of the critical hit (twice Attack, no Defense)."""
    return rng[0] <= x <= rng[1] or crit[0] <= x <= crit[1]


def ranges(sim, me, m, **kw): return P.phys_range(sim, me, m, **kw), P.phys_range(sim, me, m, crit=True, **kw)


def hits(pr, only=None): return [(t, i, -x) for t, i, x in pr.events if i >= 4 and x < 0 and only in (None, i)]       # damage to the monsters (or one of them)


def single(pr, sim, me, mons, **kw):
    """The plugin's own single-target Command on the first monster."""
    return pr.plugin.command(sim, me, mons, mons[0], **kw)


@needs_rom
def test_fight_value_and_prediction():
    w = party("Knight", [5, 6, 0, 2], equip={"rh_weapon": 15})

    def slap(pr, sim, me, mons):
        pr.range = ranges(sim, me, mons[0])
        return Command(P.CMD_SWDSLAP, 0x80, P.slot_mask(mons[0]))
    for pr in forced("SwdSlap", w, slap, seeds=range(4)):
        got = [x for t, i, x in hits(pr, 4)]
        assert got and all(inside(x, *pr.range) for x in got), (got, pr.range)         # SwdSlap is a plain Fight in the game
        assert pr.fv >= pr.range[0][0] * 0.9 and pr.policy.phys_low is P.phys_low                 # the Fight baseline the offense commands must beat


@needs_rom
def test_jump_lands_inside_the_range_and_the_menu_waits():
    w = party("Dragoon", [5, P.CMD_JUMP, 0, 2], equip={"rh_weapon": 25})

    def jump(pr, sim, me, mons):
        pr.range = ranges(sim, me, mons[0], dm=0x80, jumping=True)
        return single(pr, sim, me, mons)
    for pr in forced("Jump", w, jump, seeds=range(3), frames=1500):
        land = hits(pr, 4)
        assert land and inside(land[0][2], *pr.range), (land, pr.range)
        assert land[0][0] > 200                                                      # airborne for a long while
        assert len(pr.menus) == 1 or pr.menus[1] - pr.menus[0] > land[0][0]          # no second menu before the landing


@needs_rom
def test_lance_drains():
    w = party("Dragoon", [5, P.CMD_LANCE, 0, 2], equip={"rh_weapon": 25})

    def lance(pr, sim, me, mons):
        pr.low = P.magic_low(sim.w, me.b, mons[0].b, 45)
        return single(pr, sim, me, mons)
    for pr in forced("Lance", w, lance, seeds=range(3)):
        dmg = [x for t, i, x in hits(pr, 4)]
        assert dmg and pr.low <= dmg[0] <= pr.low * 9 // 8 + 24, (dmg, pr.low)       # (power + power / 8 - Defense) * M


@needs_rom
def test_throw_shuriken_and_scroll():
    w = party("Ninja", [5, P.CMD_THROW, 0, 2], equip={"rh_weapon": 4, "lh_weapon": 4}, items={90: 3, 0xFC: 2})

    def shuriken(pr, sim, me, mons):
        slot = next(s for s, i in sim.inventory_slots.items() if i == 90)
        st = P.Strike(P.THROW, 50, dm=0xC0)
        pr.range = st.range(sim.w, me.b, mons[0].b)[:2], st.range(sim.w, me.b, mons[0].b, True)[:2]
        return single(pr, sim, me, mons, slot=slot)
    for pr in forced("Throw", w, shuriken, seeds=range(3)):
        got = [x for t, i, x in hits(pr, 4)]
        assert got and (got[0] == 700 or inside(got[0], *pr.range)), (got, pr.range)  # (the monsters have 700 HP)
        assert pr.end["inv"][90] == pr.start["inv"][90] - 1

    def scroll(pr, sim, me, mons):
        slot = next(s for s, i in sim.inventory_slots.items() if i == 0xFC)
        pr.low = [P.magic_low(sim.w, me.b, m.b, 120, 1) for m in mons]
        return pr.plugin.command(sim, me, mons, None, slot=slot)
    for pr in forced("Throw", w, scroll, seeds=range(3)):
        got = sorted(x for t, i, x in hits(pr))
        assert len(got) == 3 and all(x >= pr.low[0] for x in got), (got, pr.low)       # every monster takes at least the lowest roll, not halved
        assert pr.end["inv"][0xFC] == pr.start["inv"][0xFC] - 1


@needs_rom
def test_steal_and_mug_take_the_item_once():
    d, b, js, sim = world()
    for cmd, name in ((P.CMD_STEAL, "Steal"), (P.CMD_MUG, "Mug")):
        w = party("Thief", [5, cmd, 0, 2], equip={"rh_weapon": 4})
        got = 0
        for pr in forced(name, w, lambda pr, sim, me, mons: single(pr, sim, me, mons), seeds=range(12), frames=400, enc=102):      # Bombs: Potion
            item = d.r[P.MONSTER_ITEM + 4 * 54 + 1]
            now = pr.end["inv"].get(item, 0) - pr.start["inv"].get(item, 0)
            assert now in (0, 1)
            if now: got += 1; assert pr.end["stolen"][4] == item                    # remembered: the monster has nothing more to give
            if cmd == P.CMD_MUG: assert hits(pr), "Mug also fights"
        assert 1 <= got <= 11                                                        # 40% per try


@needs_rom
def test_aim_kick_focus():
    def with_range(**kw):
        def make(pr, sim, me, mons):
            pr.range = ranges(sim, me, mons[0], **kw)
            return single(pr, sim, me, mons)
        return make
    w = party("Ranger", [5, P.CMD_AIM, 0, 2], equip={"rh_weapon": 68})
    for pr in forced("Aim", w, with_range(dm=0x80), seeds=range(3)):
        got = [x for t, i, x in hits(pr, 4)]
        assert got and inside(got[0], *pr.range), (got, pr.range)
    w = party("Monk", [5, P.CMD_KICK, P.CMD_FOCUS, 2], equip={"rh_weapon": 1, "lh_weapon": 1})      # (the fists are item 1: with 0 the game does not attack)

    def kick(pr, sim, me, mons):
        pr.range = ranges(sim, me, mons[0], kick=True)
        return Command(P.CMD_KICK, 0x80, P.slot_mask(*mons))
    for pr in forced("Kick", w, kick, seeds=range(3)):
        got = [x for t, i, x in hits(pr)]
        assert len(got) == 3 and all(inside(x, *pr.range) for x in got), (got, pr.range)    # one fist strike on every monster
    for pr in forced("Focus", w, with_range(dm=0x10), seeds=range(3), frames=1000):
        got = hits(pr, 4)
        assert got and got[0][0] > 250, got                                          # lands about 60 ticks later
        assert pr.range[0][0] <= sum(x for t, i, x in got if t < got[0][0] + 20) <= pr.range[1][1], (got, pr.range)       # (two hands, 700 HP)


@needs_rom
def test_chakra_guard_slash():
    w = party("Monk", [5, P.CMD_CHAKRA, 0, 2], equip={"rh_weapon": 1, "lh_weapon": 1})

    def chakra(pr, sim, me, mons):
        sim.w[me.b + 6] = 20; sim.w[me.b + 7] = 0                                    # hurt it first
        pr.low = pr.plugin.heal(sim, me)
        return pr.plugin.propose(sim, 0, me, mons)[1]
    for pr in forced("Chakra", w, chakra, seeds=range(3), frames=200):
        heal = [x for t, i, x in pr.events if i == 0 and x > 0]
        assert heal and heal[0] >= pr.low, (heal, pr.low)
    w = party("Knight", [5, P.CMD_GUARD, 0, 2], equip={"rh_weapon": 15})
    for pr in forced("Guard", w, lambda pr, sim, me, mons: Command.guard(), seeds=range(3), frames=1500):
        assert not [x for t, i, x in pr.events if i == 0 and x < 0], pr.events       # nothing gets through a guard
    w = party("Samurai", [5, P.CMD_SLASH, 0, 2], equip={"rh_weapon": 43})
    kills = tries = 0
    for pr in forced("Slash", w, lambda pr, sim, me, mons: Command(P.CMD_SLASH, 0x80, P.slot_mask(*mons)), seeds=range(8), frames=700):
        kills += sum(pr.end["dead"][4:7]); tries += 3
    assert tries / 2 <= kills                                                         # 75% per monster (85 + Level - Level), instant death


@needs_rom
def test_gil_toss_is_forbidden_by_default():
    d, b, js, sim = world()
    assert set(jobcmds.FORBIDDEN_DEFAULT) == {"Mix", "Gil Toss"}
    w = party("Samurai", [5, P.CMD_GILTOSS, 0, 2], equip={"rh_weapon": 43}, gil=60000)

    def toss(pr, sim, me, mons):
        pr.low = [min(9999, max(0, me.level + 10 - sim.w[m.b + P.O_DEF]) * 150) for m in mons]
        pr.cost = 50 * me.level * len(mons)
        default = VeteranPolicy(d, b, d.battle(ENC)["monster_ids"])                  # the default policy never throws the gil away
        assert "Gil Toss" in default.forbidden and default(sim, 0).command != P.CMD_GILTOSS
        return Command(P.CMD_GILTOSS, 0x80, P.slot_mask(*mons))
    for pr in forced("Gil Toss", w, toss, seeds=range(2), frames=300, forbidden=[]):
        assert pr.end["gil"] == pr.start["gil"] - pr.cost
        assert sorted(x for t, i, x in hits(pr)) == sorted(min(x, 700) for x in pr.low)               # (the monsters have 700 HP)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n, flush=True)
