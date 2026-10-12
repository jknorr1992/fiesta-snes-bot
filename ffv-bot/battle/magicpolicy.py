"""Magic beyond Fire / Cure: what a veteran does with Time, Red, Blue, Summon, Spellblade magic and the white / black spells the base
policy does not weigh (Holy, Flare, Bio, Drain, Death, Esuna, Poisona, Mini ...), as job command plugins (see jobcmds.py).

Everything is derived from the game's own tables and formulas (battle-main.asm attack types; ROM records at ATTACK_PROP):

    record     8 bytes per spell / attack: target flags, category, hits-1, MP, attack type, param1, param2, param3
    attack type decides the formula (06 magic attack, 08 flare, 09 random, 07 gravity, 12-16 status, 19 remove status, 1A revive, 4B-4E level
               based, 17/18 death ...); the parameters mean what the type says (battle_ram.inc, "Battle Params")
    hit%       min(99, max(1, param1 + caster level - target level)), then the target's magic evade; param1 bit 7 = never misses
    damage     (Attack - Defense) x M, Attack = power .. power + power/8 (flare: /32, random: 50..199), M = level x magic / 256 + 4

A veteran may use these formulas and their minimum / maximum, never the next roll: damage is given as (lowest, highest) and ranked by its
mean.  Values are HP equivalents: HP taken off the enemy, or HP the party would lose without the action."""
from sim import Command
from jobcmds import JobCommand, register

MAGIC_COMMAND = 0x2C
CHAR_SPELLS, SPELL_SLOT = 0x2D34, 650
ATTACK_PROP = 0x110B80
BLUE_FIRST, BLUE_SLOT_SHIFT = 0x82, 0x23          # blue spell ids 0x82..0x9F sit at CharSpells slot id - 0x23 (Command.selected is the slot)
SUMMON_ROW_OFFSET = 23

# CharStruct offsets (battle_ram.inc)
O_LEVEL, O_ST, O_EVADE, O_MEVADE, O_DEF, O_MDEF, O_ELEM = 0x02, 0x1A, 0x2C, 0x2E, 0x2D, 0x2F, 0x30
O_IMMUNE_ST, O_CREATURE, O_ALWAYS, O_BONUS_LEVEL, O_CANT_EVADE, O_MAG, O_WEAPON_PROP = 0x35, 0x65, 0x70, 0x78, 0x64, 0x2B, 0x38

# status bits as (byte of the four status bytes, mask)
POISON, BLIND, TOAD, MINI, FLOAT, ZOMBIE, STONE, DEAD = (0, 0x04), (0, 0x01), (0, 0x20), (0, 0x10), (0, 0x08), (0, 0x02), (0, 0x40), (0, 0x80)
AGING, SLEEP, PARALYZE, CHARM, BERSERK, MUTE = (1, 0x80), (1, 0x40), (1, 0x20), (1, 0x10), (1, 0x08), (1, 0x04)
REFLECT, ARMOR, SHELL, STOP, HASTE, SLOW, REGEN = (2, 0x80), (2, 0x40), (2, 0x20), (2, 0x10), (2, 0x08), (2, 0x04), (2, 0x01)
HEAVY, UNDEAD = 0x20, 0x01

# kinds whose hit needs a roll (HitMagic / HitMagicConditionalAutohit); 0x0A uses the physical evade
ROLLS = (7, 0x0D, 0x0E, 0x0F, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x28, 0x29, 0x2E, 0x3D, 0x50, 0x51, 0x52, 0x53, 0x54, 0x58, 0x6D, 0x0A)
STATUS_KINDS = {0x12: 0, 0x13: 1, 0x14: 2, 0x15: 0, 0x16: 2, 0x17: 0}      # kind -> which status byte param3 sets
DAMAGE_KINDS = (6, 8, 9, 0x0A, 0x0B, 0x0C, 0x0D, 0x28, 0x2A, 0x2D, 0x4E, 0x54, 0x58, 0x5F, 0x6C, 0x6D, 7, 0x4D)

FAMILIES = {
    "Sword": range(0, 18), "White": range(18, 36), "Black": range(36, 54), "Time": range(54, 72), "Summon": range(72, 87),
    "Blue": range(BLUE_FIRST, 0xA0),
}
FAMILIES["Red"] = FAMILIES["DuCast"] = list(FAMILIES["White"]) + list(FAMILIES["Black"])
FAMILIES["MgcSwrd"] = FAMILIES["Sword"]


class Rec:
    """One 8-byte ATTACK_PROP record."""
    __slots__ = ("id", "tgt", "cat", "hits", "mp", "kind", "p1", "p2", "p3")
    def __init__(self, r, sp):
        o = ATTACK_PROP + 8 * sp
        self.id = sp
        self.tgt, self.cat, self.hits, self.mp, k, self.p1, self.p2, self.p3 = r[o:o + 8]
        self.kind = k & 0x7F
    @property
    def all_enemies(self): return bool(self.tgt & 0x40)         # always hits the whole side
    @property
    def can_multi(self): return bool(self.tgt & 0x80)          # the player may widen it to the whole side
    @property
    def status(self):
        """(byte, mask) of the status a status spell sets, None for the others."""
        b = STATUS_KINDS.get(self.kind)
        return None if b is None else (b, self.p3)


class Spells:
    """Spell knowledge shared by the plugins of one policy: availability, statuses, hit chances, damage ranges and the fight's pace."""
    def __init__(self, policy):
        self.p = policy; self.d = policy.d; self.r = policy.d.r
        self._rec = {}
        self.ids = {}
        for i in range(18, 72):
            n = self.d.spell_name(i)
            if n and self.d.spell_icon(i) in ("white", "black", "time"): self.ids[n] = i
        self.turns = 0; self.hp0 = None; self.frame = -1

    def rec(self, sp):
        r = self._rec.get(sp)
        if r is None: r = self._rec[sp] = Rec(self.r, sp)
        return r

    def effect_rec(self, sp):
        """The record whose effect is cast: summons take their damage / status from the record 23 further on."""
        return self.rec(sp + SUMMON_ROW_OFFSET) if 72 <= sp <= 86 and sp != 84 else self.rec(sp)

    # ---- availability ---------------------------------------------------------------------------------------------------
    @staticmethod
    def slot(sp): return sp - BLUE_SLOT_SHIFT if sp >= BLUE_FIRST else sp

    def cost(self, sim, char, sp):
        """MP cost of spell id `sp` for party member `char`; None when it cannot be cast now (not learned, level not enabled, muted or too
        little MP: the game's own disabled flag)."""
        b = CHAR_SPELLS + SPELL_SLOT * char; w = sim.w; s = self.slot(sp)
        if w[b + s] != sp or w[b + 520 + s] & 0x80: return None
        return w[b + 260 + s]

    def known(self, sim, char, ids):
        return [(sp, c) for sp, c in ((sp, self.cost(sim, char, sp)) for sp in ids) if c is not None]

    # ---- combatant state ------------------------------------------------------------------------------------------------
    @staticmethod
    def status(sim, c):
        """The four status bytes with the permanent ones (equipment / monster innate) merged in."""
        w = sim.w; b = c.b
        return [w[b + O_ST + k] | w[b + O_ALWAYS + k] for k in range(4)]

    def has(self, sim, c, st): return bool(self.status(sim, c)[st[0]] & st[1])

    @staticmethod
    def immune_to(sim, c, st): return st[0] < 3 and bool(sim.w[c.b + O_IMMUNE_ST + st[0]] & st[1])
    @staticmethod
    def level(sim, c): return sim.w[c.b + O_LEVEL] + sim.w[c.b + O_BONUS_LEVEL]
    @staticmethod
    def creature(sim, c): return sim.w[c.b + O_CREATURE]
    @staticmethod
    def living(sim): return [m for m in sim.monsters if not m.dead and m.hp > 0]
    @staticmethod
    def allies(sim): return [c for c in sim.party if not c.dead and c.max_hp and c.row & 0x40 == 0 and c.hp > 0]
    def reflecting(self, sim, m): return self.has(sim, m, REFLECT)

    def floor(self, sim, char, me):
        """MP a caster keeps back: a Cure in hard fights, `mp_floor` of the pool in ordinary ones (the base policy's rule)."""
        reserve = self.p._cure_reserve(sim, char) if hasattr(self.p, "_cure_reserve") else 0
        return reserve if self.p.hard else max(reserve, int(getattr(self.p, "mp_floor", 0.3) * me.max_mp))

    # ---- hit chance -----------------------------------------------------------------------------------------------------
    def hit_p(self, sim, me, rec, t, n_targets=1):
        """Probability that a spell lands on `t`: the hit% roll (param1 + caster level - target level, halved for several targets and for a
        Shell target), then the evade roll."""
        if rec.kind not in ROLLS or rec.p1 & 0x80: return 1.0
        w = sim.w
        if rec.cat & w[t.b + O_CANT_EVADE]: return 1.0
        hit = min(99, max(1, rec.p1 + self.level(sim, me) - self.level(sim, t)))
        if rec.kind == 0x0A: hit = rec.p1; ev = w[t.b + O_EVADE]                    # physical magic: hit% = param1 against the physical evade
        else:
            ev = 0 if self.has(sim, t, TOAD) else w[t.b + O_MEVADE]
            if self.has(sim, t, SHELL): hit = max(1, hit >> 1)
            if n_targets > 1 and not rec.all_enemies: hit = max(1, hit >> 1)
        return hit / 100 * (1 - ev / 100)

    # ---- damage ---------------------------------------------------------------------------------------------------------
    def dmg(self, sim, me, sp, t, n_targets=1):
        """(lowest, highest) HP one hit of the spell can take off monster `t`, None when it does not hurt it (absorbed, immune, wrong type).
        `n_targets` > 1 halves the damage of spells that are not 'hits all' (the game's multi-target rule)."""
        rec = self.effect_rec(sp); k = rec.kind; w = sim.w
        mdef, defn = w[t.b + O_MDEF], w[t.b + O_DEF]
        toad = self.has(sim, t, TOAD); cr = self.creature(sim, t)
        M = ((self.level(sim, me) * w[me.b + O_MAG]) >> 8) + 4
        if self.has(sim, t, SHELL): M >>= 1
        if k in (7, 0x4D):                                                           # Demi / Quarter / Missile: a fraction of the current HP
            if cr & HEAVY or k == 0x4D and self.level(sim, t) % max(1, rec.p1): return None
            v = min(9999, t.hp * rec.p2 >> 4); return (v, v)
        if k == 0x28: v = min(9999, rec.p2 + (rec.p3 << 8)); return (v, v)
        if k == 0x2A: v = max(1, t.max_hp * rec.p2 >> 4); a = (v, v); el = rec.p1
        elif k == 0x54: v = max(0, me.max_hp - me.hp); a = (v, v); el = 0
        elif k == 0x6D: v = max(0, (me.max_hp - me.hp) >> 1); a = (v, v); el = 0
        elif k in (6, 0x2D, 0x0D, 0x6C, 0x1B):
            a = (rec.p2, rec.p2 + (rec.p2 >> 3)); el = rec.p3
            if k == 0x6C and cr & rec.p3: a = (a[0] << 3, a[1] << 3)
            if k == 0x6C: el = 0
            if k == 0x2D and self.has(sim, t, FLOAT): return None
            if k == 0x0D and cr & UNDEAD: return None
        elif k in (8, 0x4E, 0x0C, 0x58, 0x5F):                                       # flare family: defence / 32
            a = (rec.p2, rec.p2 + (rec.p2 >> 5)); mdef >>= 5; el = rec.p3 if k in (8, 0x4E) else rec.p1 if k == 0x0C else 0
            if k == 0x4E and self.level(sim, t) % max(1, rec.p1): return None
            if k == 0x5F and not cr & rec.p1: return None
            if k == 0x4E: el = rec.p3
        elif k == 9: a = (50, 199); M = rec.p2; el = rec.p3
        elif k == 0x0B: a = (10, 99); M = (self.level(sim, me) >> 3) + 2; el = rec.p1
        elif k == 0x0A: a = (rec.p2, rec.p2 + (rec.p2 >> 3)); mdef = defn; el = rec.p3
        else: return None
        if k in (0x2A, 0x54, 0x6D):
            lo, hi = a
        else:
            a0, a1 = a
            if k == 0x6D: pass
            if el:
                if w[t.b + 0x30] & el or w[t.b + 0x31] & el or w[t.b + 0x32] & el: return None
                if w[t.b + 0x33] & el: a0 >>= 1; a1 >>= 1
                elif w[t.b + 0x34] & el: a0 <<= 1; a1 <<= 1; mdef = 0
            if toad and k != 0x0A: mdef = 0
            if n_targets > 1 and not rec.all_enemies: a0 >>= 1; a1 >>= 1
            lo, hi = min(9999, max(0, a0 - mdef) * max(1, M)), min(9999, max(0, a1 - mdef) * max(1, M))
        if k in (0x2A,) and el and (w[t.b + 0x30] & el or w[t.b + 0x32] & el or w[t.b + 0x31] & el): return None
        return (lo, hi)

    def hits(self, sp): return (self.effect_rec(sp).hits & 7) + 1

    def kill_p(self, sim, me, sp, t):
        """Probability that the spell kills monster `t` outright (Death, X-Zone, Break, L5 Doom, Odin ...)."""
        rec = self.effect_rec(sp); k = rec.kind; cr = self.creature(sim, t)
        if self.has(sim, t, ZOMBIE): return 0.0
        if k == 0x17 and rec.p3 & 0x80:                                              # Death: heals the undead instead
            return 0.0 if cr & UNDEAD or self.immune_to(sim, t, DEAD) else self.hit_p(sim, me, rec, t)
        if k == 0x18: return 0.0 if cr & HEAVY else self.hit_p(sim, me, rec, t)        # X-Zone / Odin: ignores status immunity, misses Heavy
        if k == 0x4B:                                                                # L5 Doom: level multiple of param1
            return 0.0 if self.level(sim, t) % max(1, rec.p1) or cr & UNDEAD or self.immune_to(sim, t, DEAD) else 1.0
        if k == 0x12 and rec.p3 & 0x40:                                              # Petrify / Break: stone on a monster is death
            return 0.0 if self.immune_to(sim, t, STONE) else self.hit_p(sim, me, rec, t)
        return 0.0

    def gain(self, sim, me, sp, t, n_targets=1, share=1.0):
        """Expected HP taken off monster `t` by one cast: kills count the monster's whole HP, damage is capped by what it has left.
        `share`: fraction of the spell's hits that land on this monster (random-target multi-hit spells)."""
        kp = self.kill_p(sim, me, sp, t)
        r = self.dmg(sim, me, sp, t, n_targets)
        g = kp * t.hp
        if r is not None:
            rec = self.effect_rec(sp)
            per = (r[0] + r[1]) / 2 * self.hits(sp) * share
            g += (1 - kp) * self.hit_p(sim, me, rec, t, n_targets) * min(t.hp, per)
        return g

    # ---- the fight's pace -----------------------------------------------------------------------------------------------
    def observe(self, sim, mons):
        """Count party turns and remember the monsters' starting HP: with them the damage the party really deals per turn is known."""
        if sim.frame < self.frame: self.turns = 0; self.hp0 = None
        self.frame = sim.frame
        tot = sum(m.hp for m in mons)
        if self.hp0 is None: self.hp0 = tot
        self.hp_now = tot; self.turns += 1

    def per_turn(self, sim, me, mons, guess):
        """Damage the party does per turn: measured once a few turns have passed, else `guess` (the acting character's best action x allies)."""
        if self.turns >= 4 and self.hp0 is not None and self.hp0 > self.hp_now: return (self.hp0 - self.hp_now) / self.turns
        return max(1.0, guess * max(1, len(self.allies(sim))) * 0.8)

    def threat(self, sim, m):
        """Highest single hit monster `m` can do to a party member: the AI-script scan of a Veteran policy, else its plain Fight."""
        v = getattr(self.p, "mthreat", {}).get(m.i)
        if v: return v
        from knowledge import damage_range
        w = bytes(sim.w); best = 0
        atk = dict(formula=1, power=0, element=0, raw=bytes(8), id=0, target=0, category=0, cost=0)
        for a in self.allies(sim):
            r = damage_range(w, {}, m.i, a.i, atk)
            if r: best = max(best, r[1])
        return best

    # ---- commands -------------------------------------------------------------------------------------------------------
    @staticmethod
    def mon_mask(ms):
        m = 0
        for t in ms: m |= 0x80 >> (t.i - 4)
        return m

    def cast(self, sp, monsters=(), party=(), second=None):
        """Command that casts spell id `sp` (the menu's slot is looked up from the id); `second` makes it a Dual-cast."""
        return Command(MAGIC_COMMAND, 0x28 if second else 0x20, self.mon_mask(monsters), Command.party_mask(*party), self.slot(sp), second)

    # ---- choosing an attack spell ---------------------------------------------------------------------------------------
    def options(self, sim, me, sp, mons):
        """[(gain, targets)] ways to cast damaging spell `sp`: each single target, or everybody (hits-all spells; widened ones at half damage)."""
        rec = self.effect_rec(sp); out = []
        if any(self.reflecting(sim, m) for m in mons) and len(mons) == 1: return out
        banned = getattr(self.p, "banned", ())
        n = len(mons)
        if rec.all_enemies or rec.hits > 1 and rec.tgt & 0x40:
            tg = [m for m in mons if not self.reflecting(sim, m)]
            if len(tg) < n or any((m.i, sp) in banned for m in tg): return out
            hits_each = 1.0 / n                                                          # every hit of a multi-hit spell picks a random enemy
            gs = []
            for m in tg:
                if self.absorbs(sim, rec, m): return out
                gs.append(self.gain(sim, me, sp, m, 1, hits_each if self.hits(sp) > 1 else 1.0))
            out.append((sum(gs), tg))
            return out
        for m in mons:
            if self.reflecting(sim, m) or (m.i, sp) in banned or self.absorbs(sim, rec, m): continue
            out.append((self.gain(sim, me, sp, m), [m]))
        if rec.can_multi and n > 1 and not any(self.reflecting(sim, m) or self.absorbs(sim, rec, m) or (m.i, sp) in banned for m in mons):
            out.append((sum(self.gain(sim, me, sp, m, n) for m in mons), list(mons)))
        return out

    def absorbs(self, sim, rec, m):
        el = rec.p1 if rec.kind in (0x0B, 0x0C, 0x2A) else rec.p3 if rec.kind in (6, 8, 9, 0x0A, 0x2D, 0x4E) else 0
        return bool(el and sim.w[m.b + 0x30] & el)

    def best_attack(self, sim, char, me, mons, ids, floor=0, spare=None):
        """(gain, cost, spell id, targets) of the best damaging / killing spell among `ids`, None when nothing is worth it.  In hard fights the
        biggest gain wins; in ordinary ones the cheapest spell that is nearly as good (85%) does."""
        cands = []
        for sp, cost in self.known(sim, char, ids):
            rec = self.effect_rec(sp)
            if cost > me.mp - floor or rec.kind not in DAMAGE_KINDS and rec.kind not in (0x17, 0x18, 0x12, 0x4B, 0x13): continue
            if rec.kind in (0x12, 0x13, 0x17, 0x18, 0x4B) and not rec.status in (None, (0, 0x40)) and rec.kind != 0x4B and rec.kind != 0x18 and rec.kind != 0x17: continue
            for g, tg in self.options(sim, me, sp, mons):
                if g > 0: cands.append((g, cost, sp, tg))
        if not cands: return None
        top = max(c[0] for c in cands)
        keep = [c for c in cands if c[0] >= (0.97 if self.p.hard else 0.85) * top]
        return min(keep, key=lambda c: (c[1], -c[0]))

    def attack_command(self, sp, tg): return self.cast(sp, monsters=tg)


def spells_of(policy):
    s = getattr(policy, "spells", None)
    if s is None: s = policy.spells = Spells(policy)
    return s


class Family(JobCommand):
    """Plugin over one magic command's spell family; `kind` decides when the policy asks it."""
    family = ()
    def __init__(self, policy):
        super().__init__(policy)
        self.m = spells_of(policy)

    def ids(self): return self.family


def _family_plugins():
    out = []
    for name, ids in FAMILIES.items():
        if name == "Sword": continue
        out.append((name, list(ids)))
    return out


def make_attack(name, ids):
    @register
    class Attack(Family):
        kind = "attack"
        family = ids
        def propose(self, sim, char, me, mons):
            M = self.m; M.observe(sim, mons)
            best = M.best_attack(sim, char, me, mons, self.family, M.floor(sim, char, me))
            if best is None: return None
            g, cost, sp, tg = best
            if not self.p.hard and g < 1.5 * self.p.fight_value(sim, me, mons): return None
            return g, M.attack_command(sp, tg)
    Attack.name = name; Attack.__name__ = Attack.__qualname__ = name + "Attack"
    return Attack


for _n, _i in _family_plugins():
    if _n not in ("MgcSwrd",): make_attack(_n, _i)
