"""What the bosses ahead call for (user, 2026-10-12: "it should try and plan ahead for what abilities might be best to have for the bosses
ahead").  Read from the ROM's own tables, never from a simulated fight:

    monster record   HP, element weakness / absorb / immunity bytes, status immunity bytes (ROM 0x12..0x14 -> battle CharStruct 0x35..0x37),
                     creature type (undead ...)
    AI script        which attacks the boss can use (Bestiary.script): physical or magical, which elements, whether they inflict a status

Each boss within the look-ahead has a weight 0.8 ** (stages ahead).  The result is a need 0..1 per spell id, passive and command, which the
job planner multiplies into the worth of learning that ability (1 + 1.5 * need), the shop into the worth of buying that spell, and the
ability loadout into the score of the passive.  The need is a share, not a verdict: a Slow spell is worth more when a long fight is coming
and the boss is not immune to Slow; nothing here says which job to take, the planner still weighs the role, the member and the ABP cost."""
from data import MONSTER_PROP
from magicpolicy import ATTACK_PROP, DAMAGE_KINDS, ROLLS, STOP, SLOW, SLEEP, MUTE, DEAD, UNDEAD

DISTANCE_DECAY = 0.8
LOOKAHEAD_STAGES = 8
IMMUNE_ST = 0x12                       # ROM monster record: status immunity bytes (battle CharStruct 0x35..0x37)
STATUS_KINDS = ROLLS - DAMAGE_KINDS                        # attack types that roll to hit but do no plain damage: statuses, kills
WHITE, BLACK, TIME, SUMMON, MGCSWRD = 18, 36, 54, 72, 0
GROUP_BASE = {"White": WHITE, "Black": BLACK, "Time": TIME, "Summon": SUMMON, "MgcSwrd": MGCSWRD}
EARTH = 0x20

SLOW_IDS, STOP_IDS, HASTE_IDS = (55, 64), (61,), (58, 67)
PROTECT, SHELL, REGEN, FLOAT, MUTE_ID, SLEEP_ID, DEATH, ESUNA, POISONA = 22, 28, 56, 59, 21, 40, 52, 29, 20
HEALS = (18, 24, 30)
LIFES = (25, 33)

PHYS_PASSIVES = ("Barrier", "Cover", "Evade", "A.Dodger", "Counter")
HP_PASSIVES = ("HP +10%", "HP +20%", "HP +30%")


def level_spells(group, level):
    """Spell ids granted by magic level `level` (0-based) of command group `group` (three per level; Red Magic: white and black)."""
    if group == "Red":
        return level_spells("White", level) + level_spells("Black", level)
    base = GROUP_BASE.get(group)
    return [] if base is None else [base + 3 * level + k for k in range(3)]


class Boss:
    """One boss fight read from the tables."""
    def __init__(self, data, bestiary, enc):
        d = data; b = d.battle(enc)
        mons = [d.monster(m) for m in b["monster_ids"] if m is not None]
        self.enc = enc
        self.hp = sum(m["hp"] for m in mons) or 1
        self.weak = self.bad = 0                             # element bits it is weak to / absorbs or is immune to (any of its monsters)
        self.immune = [0, 0, 0]; self.undead = False; self.atk = 0
        phys = mag = infl = 0.0; elements = 0
        for m in mons:
            self.weak |= m["weak"]; self.bad |= m["absorb"] | m["immune"]
            for k in range(3): self.immune[k] |= data.r[MONSTER_PROP + 32 * m["id"] + IMMUNE_ST + k]
            self.undead = self.undead or bool(m["creature"] & UNDEAD)
            self.atk = max(self.atk, m["attack"])
            for e in bestiary.script(m["id"]):
                for a in e.attacks:
                    rec = bestiary.attack(a); f = rec["formula"]
                    if f in (1, 2): phys += 1
                    elif f in DAMAGE_KINDS: mag += 1; elements |= rec["element"]
                    if f in STATUS_KINDS: infl += 1
        tot = max(1.0, phys + mag)
        self.phys = phys / tot; self.mag = mag / tot
        self.infl = min(1.0, infl / max(1.0, phys + mag + infl) * 2)
        self.earth = bool(elements & EARTH)
        self.danger = min(1.0, 0.3 + self.atk / 60.0 + self.hp / 6000.0)
        self.long = min(1.0, self.hp / 2500.0)               # how long the fight lasts: what haste / slow / regen are worth

    def can_hit(self, st): return not (st[0] < 3 and self.immune[st[0]] & st[1])


class BossNeeds:
    """`bosses`: [(battle id, stages ahead)]."""
    def __init__(self, data, bestiary, bosses):
        self.d = data
        self.bosses = [(Boss(data, bestiary, enc), DISTANCE_DECAY ** k) for enc, k in bosses]
        self.spell = {}; self.passive = {}
        W = max(1.0, sum(w for _, w in self.bosses))
        spell = {}
        def add(table, key, v):
            if v > 0: table[key] = table.get(key, 0.0) + v
        passive = {}
        for bs, w in self.bosses:
            f = w / W
            for sp in range(0, 87):
                rid = sp + 23 if sp >= 75 and sp != 84 else sp
                rec = data.r[ATTACK_PROP + 8 * rid: ATTACK_PROP + 8 * rid + 8]
                kind, power, el = rec[4], rec[6], rec[7]
                if kind in DAMAGE_KINDS and (sp < 54 or sp >= 72):           # attack spells, spell blade, summons
                    if el and el & bs.bad: continue
                    heavy = min(1.0, 0.35 + power / 120.0)
                    add(spell, sp, f * heavy * (1.0 if el and el & bs.weak else 0.35))
            for ids, ok, v in ((SLOW_IDS, bs.can_hit(SLOW), 0.8 * bs.long), (STOP_IDS, bs.can_hit(STOP), 0.9), (HASTE_IDS, True, 0.7 * bs.long)):
                if ok:
                    for sp in ids: add(spell, sp, f * v)
            add(spell, PROTECT, f * bs.phys * (0.4 + 0.6 * bs.danger)); add(spell, SHELL, f * bs.mag * (0.4 + 0.6 * bs.danger))
            add(spell, REGEN, f * 0.3 * bs.long)
            if bs.earth: add(spell, FLOAT, f * 0.8)
            if bs.can_hit(MUTE) and bs.mag > 0.5: add(spell, MUTE_ID, f * 0.5)
            if bs.can_hit(SLEEP): add(spell, SLEEP_ID, f * 0.2)
            if bs.can_hit(DEAD) and not bs.undead: add(spell, DEATH, f * 0.8)
            for sp in HEALS: add(spell, sp, f * (0.2 + 0.4 * bs.danger))
            for sp in LIFES: add(spell, sp, f * (0.15 + 0.3 * bs.danger))
            for sp in (ESUNA, POISONA): add(spell, sp, f * bs.infl)
            if bs.undead:
                for sp in HEALS + LIFES: add(spell, sp, f * 0.4)                  # healing hurts the undead
            for nm in PHYS_PASSIVES: add(passive, nm, f * bs.phys * bs.danger)
            for nm in HP_PASSIVES: add(passive, nm, f * bs.danger)
        self.spell = {k: min(1.0, v) for k, v in spell.items()}
        self.passive = {k: min(1.0, v) for k, v in passive.items()}
        self.utility = max([self.spell.get(s, 0.0) for s in SLOW_IDS + STOP_IDS + HASTE_IDS] or [0.0])

    # ---- lookups
    def spell_need(self, sp): return self.spell.get(sp, 0.0)

    def level_need(self, group, level):
        """Need of magic level `level` (0-based) of command group `group`: its most wanted spell."""
        return max([self.spell.get(sp, 0.0) for sp in level_spells(group, level)] or [0.0])

    def passive_need(self, name): return self.passive.get(name, 0.0)

    def top(self, n=6):
        names = {sp: self.d.spell_name(sp) for sp in self.spell}
        return sorted(((round(v, 2), names[sp]) for sp, v in self.spell.items()), reverse=True)[:n]

    def __repr__(self):
        return f"<BossNeeds {len(self.bosses)} bosses: " + ", ".join(f"{n} {v}" for v, n in self.top()) + ">"
