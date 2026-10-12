"""Magic beyond Fire / Cure for the battle policy: Time, Red, Blue, Summon and Spellblade magic, DuCast, and the white / black spells the base
policy does not weigh (Holy, Flare, Bio, Drain, Death, Esuna, Poisona ...), as job command plugins (see jobcmds.py).

Everything comes from the game's own tables and formulas (battle-main.asm; the spell records at ATTACK_PROP, 8 bytes each):

    record      target flags, category, hits - 1, MP, attack type, param1, param2, param3
    hit %       min(99, max(1, param1 + caster level - target level)); param1 bit 7 = never misses; halved for a Shell target and when a spell
                that does not hit everybody is widened to several targets; then the target's magic evade
    damage      (Attack - Defense) x M, Attack = power .. power + power/8 (Flare and Bio: /32, Comet and Meteo: 50..199), M = level x magic / 256 + 4,
                Shell halves M; elements: absorb / immune = nothing, half = half, weak = double and no defence
    effects     Slow / Haste (type 16), Stop (14), Break / Death / X-Zone (12, 17, 18) work on the status bytes a monster carries; its StatusImmune
                bytes stop them (X-Zone and Odin ignore them but miss Heavy monsters)

A veteran may use these formulas and their minimum / maximum, never the next roll: damage is a (lowest, highest) range and spells are ranked by
its mean.  Values are HP equivalents: HP taken off the enemy, or HP the party would lose without the action."""
from sim import Command
from jobcmds import JobCommand, register

MAGIC_COMMAND = 0x2C
CHAR_SPELLS, SPELL_SLOT = 0x2D34, 650
ATTACK_PROP = 0x110B80
BLUE_FIRST, BLUE_LAST, BLUE_SLOT_SHIFT = 0x82, 0x9F, 0x23     # blue spell ids sit at CharSpells slot id - 0x23 (and Command.selected is the slot)
SUMMON_ROW_OFFSET = 23
PHOENIX = 84

# CharStruct offsets (battle_ram.inc)
O_LEVEL, O_ST, O_MAG, O_EVADE, O_MEVADE, O_MDEF = 0x02, 0x1A, 0x2B, 0x2C, 0x2E, 0x2F
O_ABSORB, O_BLOCK, O_IMMUNE, O_HALF, O_WEAK = 0x30, 0x31, 0x32, 0x33, 0x34
O_IMMUNE_ST, O_CANT_EVADE, O_CREATURE, O_ALWAYS, O_BONUS_LEVEL, O_DEF, O_BONUS_MAG, O_VIT, O_BONUS_VIT = 0x35, 0x64, 0x65, 0x70, 0x78, 0x2D, 0x77, 0x2A, 0x76
GEAR, GEAR_SIZE, GEAR_RH = 0x4049, 84, 60                      # per character: equipment records of 12 bytes (right-hand weapon at +60)

# statuses as (status byte 0..3, mask); the immunity bytes only exist for the first three
POISON, BLIND, TOAD, MINI, FLOAT, ZOMBIE, STONE, DEAD = (0, 0x04), (0, 0x01), (0, 0x20), (0, 0x10), (0, 0x08), (0, 0x02), (0, 0x40), (0, 0x80)
AGING, SLEEP, PARALYZE, CHARM, BERSERK, MUTE = (1, 0x80), (1, 0x40), (1, 0x20), (1, 0x10), (1, 0x08), (1, 0x04)
REFLECT, ARMOR, SHELL, STOP, HASTE, SLOW, REGEN = (2, 0x80), (2, 0x40), (2, 0x20), (2, 0x10), (2, 0x08), (2, 0x04), (2, 0x01)
HEAVY, UNDEAD = 0x20, 0x01                                    # CreatureType bits

# attack types whose hit needs a roll (HitMagic)
ROLLS = {0x07, 0x0D, 0x0E, 0x0F, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x28, 0x29, 0x2E, 0x3D, 0x50, 0x51, 0x52, 0x53, 0x54, 0x58, 0x6D}
DAMAGE_KINDS = {0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0F, 0x28, 0x2A, 0x2D, 0x4D, 0x4E, 0x5F, 0x6C}
KILL_KINDS = {0x12, 0x17, 0x18, 0x4B}

FAMILY = {
    "MgcSwrd": range(0, 18), "White": range(18, 36), "Black": range(36, 54), "Time": range(54, 72), "Summon": range(72, 87),
    "Blue": range(BLUE_FIRST, BLUE_LAST + 1), "Red": range(18, 54), "DuCast": range(18, 87),
}
# Esuna (29) and Poisona (20) clear the statuses in these masks; how much a turn of an ally under the status is worth (share of a normal turn)
CURES = {20: {POISON}, 29: {STONE, TOAD, MINI, POISON, BLIND, AGING, SLEEP, PARALYZE, CHARM, MUTE}}
LOSS = {STONE: 1.0, CHARM: 1.0, PARALYZE: 0.7, SLEEP: 0.6, TOAD: 0.5, MUTE: 0.4, AGING: 0.3, POISON: 0.2, BLIND: 0.25, MINI: 0.15}


class Caster:
    """Stand-in for a caster that is not a party member (a released monster): level and magic from its table."""
    def __init__(self, lv, mag, hp=1, max_hp=1): self.lv, self.mag, self.hp, self.max_hp = lv, mag, hp, max_hp


class Rec:
    """One 8-byte spell record."""
    __slots__ = ("id", "tgt", "cat", "hits", "mp", "kind", "p1", "p2", "p3")
    def __init__(self, rom, sp, base=ATTACK_PROP):
        o = base + 8 * sp
        self.id = sp
        self.tgt, self.cat, self.hits, self.mp, k, self.p1, self.p2, self.p3 = rom[o:o + 8]
        self.kind = k & 0x7F; self.hits = (self.hits & 7) + 1
    @property
    def side(self): return bool(self.tgt & 0x40)                # always hits the whole side
    @property
    def can_widen(self): return bool(self.tgt & 0x80)          # the player may aim it at the whole side
    @property
    def roulette(self): return bool(self.tgt & 0x04)           # picks a random combatant of either side (Roulette)


class Spells:
    """Spell knowledge of one policy: what a character can cast, and the numbers the plugins weigh (hit chance, damage range, kill chance)."""
    def __init__(self, policy):
        self.p = policy; self.d = policy.d; self.r = policy.d.r
        self._rec = {}

    def rec(self, sp, base=ATTACK_PROP):
        r = self._rec.get((base, sp))
        if r is None: r = self._rec[base, sp] = Rec(self.r, sp, base)
        return r

    def effect(self, sp):
        """The record whose effect is cast: a summon's own record only names it, damage and status are in the record 23 further on.  A Rec
        (a command ability, an item, a monster's attack) stands for itself."""
        if isinstance(sp, Rec): return sp
        return self.rec(sp + SUMMON_ROW_OFFSET) if 72 <= sp <= 86 and sp != PHOENIX else self.rec(sp)

    @staticmethod
    def slot(sp): return sp - BLUE_SLOT_SHIFT if sp >= BLUE_FIRST else sp

    # ---- availability ---------------------------------------------------------------------------------------------------
    def cost(self, sim, char, sp):
        """MP cost of spell `sp` for party member `char` when it can be cast now (known, enough MP, not muted), else None."""
        b = CHAR_SPELLS + SPELL_SLOT * char; w = sim.w; s = self.slot(sp)
        if w[b + s] != sp or w[b + 520 + s] & 0x80: return None
        return w[b + 260 + s]

    def known(self, sim, char, ids):
        return [(sp, c) for sp, c in ((sp, self.cost(sim, char, sp)) for sp in ids) if c is not None]

    # ---- combatants -----------------------------------------------------------------------------------------------------
    @staticmethod
    def status(sim, c):
        w = sim.w
        return [w[c.b + O_ST + k] | w[c.b + O_ALWAYS + k] for k in range(4)]

    def has(self, sim, c, st): return bool(self.status(sim, c)[st[0]] & st[1])

    @staticmethod
    def raw(sim, c, st):
        """The status as the battle can remove it (the always-on status of gear and monsters excluded)."""
        return bool(sim.w[c.b + O_ST + st[0]] & st[1])

    def claimed(self, sim, frames=300):
        """Keys taken care of within the last `frames`."""
        return {k for k, f in self.__dict__.get("_claims", {}).items() if 0 <= sim.frame - f < frames}

    def claim(self, key, sim, frames=300):
        """True when nobody has taken care of `key` (an ally's status, a fallen ally) in the last `frames`; marks it taken.  Two members whose
        turns overlap would otherwise both cure the same ally."""
        c = self.__dict__.setdefault("_claims", {}); f = c.get(key)
        if f is not None and 0 <= sim.frame - f < frames: return False
        c[key] = sim.frame
        return True

    @staticmethod
    def immune(sim, c, st): return st[0] < 3 and bool(sim.w[c.b + O_IMMUNE_ST + st[0]] & st[1])

    @staticmethod
    def level(sim, c):
        return c.lv if isinstance(c, Caster) else sim.w[c.b + O_LEVEL] + sim.w[c.b + O_BONUS_LEVEL]

    @staticmethod
    def creature(sim, c): return sim.w[c.b + O_CREATURE]

    @staticmethod
    def allies(sim): return [c for c in sim.party if not c.dead and c.max_hp and c.hp > 0]

    def threat(self, m):
        """Strongest single hit monster `m` can do (the policy's script scan), 0 when the policy has no scripts."""
        return getattr(self.p, "mthreat", {}).get(m.i, 0)

    # ---- hit chance -----------------------------------------------------------------------------------------------------
    def hit_p(self, sim, me, rec, t, n=1):
        """Probability that spell `rec` lands on `t`: the level-based hit % (halved by Shell and when several targets are aimed at), then the
        magic evade roll."""
        w = sim.w
        if rec.kind == 0x0A and not rec.p1 & 0x80:                                     # physical magic rolls against the physical evade
            return rec.p1 / 100 * (1 - w[t.b + O_EVADE] / 100)
        if rec.kind not in ROLLS or rec.p1 & 0x80: return 1.0
        if rec.cat & w[t.b + O_CANT_EVADE]: return 1.0
        hit = min(99, max(1, rec.p1 + self.level(sim, me) - self.level(sim, t)))
        if self.has(sim, t, SHELL): hit = max(1, hit >> 1)
        if n > 1 and not rec.side: hit = max(1, hit >> 1)
        ev = 0 if self.has(sim, t, TOAD) else w[t.b + O_MEVADE]
        return hit / 100 * (1 - ev / 100)

    # ---- damage ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def element_of(rec):
        k = rec.kind
        return rec.p3 if k in (0x06, 0x08, 0x09, 0x0A, 0x2D, 0x4E) else rec.p1 if k in (0x0B, 0x0C, 0x2A) else 0

    def absorbs(self, sim, rec, t):
        el = self.element_of(rec)
        return bool(el and sim.w[t.b + O_ABSORB] & el)

    def dmg(self, sim, me, sp, t, n=1):
        """(lowest, highest) HP one hit of spell `sp` takes off monster `t`; None when it does not hurt it (absorbed, immune, wrong target)."""
        rec = self.effect(sp); k = rec.kind; w = sim.w
        mdef = 0 if self.has(sim, t, TOAD) else w[t.b + O_MDEF]
        cr = self.creature(sim, t)
        M = ((self.level(sim, me) * (me.mag if isinstance(me, Caster) else min(99, w[me.b + O_MAG] + w[me.b + O_BONUS_MAG]))) >> 8) + 4
        if self.has(sim, t, SHELL) and k != 0x0A: M >>= 1
        if k == 0x0F:                                                           # Gravity-like: HP down to 1..8, not Heavy ones
            return None if cr & HEAVY else (max(0, t.hp - 8), max(0, t.hp - 1))
        if k in (0x07, 0x4D):                                                   # a share of the current HP (Demi, Quarter): not Heavy ones
            if cr & HEAVY or k == 0x4D and self.level(sim, t) % max(1, rec.p1): return None
            v = min(9999, t.hp * rec.p2 >> 4)
            return (v, v)
        if k == 0x28: v = min(9999, rec.p2 + (rec.p3 << 8)); return (v, v)       # fixed damage (Blowfish)
        el = self.element_of(rec)
        if k == 0x2A: a0 = a1 = max(1, t.max_hp * rec.p2 >> 4); M = 1; mdef = 0   # share of the max HP
        elif k in (0x08, 0x4E, 0x0C, 0x5F):                                     # Flare, L3 Flare, Bio, Requiem: the defence counts a 32nd
            if k == 0x4E and self.level(sim, t) % max(1, rec.p1): return None
            if k == 0x5F and not cr & rec.p1: return None                        # Requiem: only its creature type
            a0, a1 = rec.p2, rec.p2 + (rec.p2 >> 5); mdef >>= 5
        elif k == 0x09: a0, a1 = 50, 199; M = rec.p2                            # Comet, Meteo
        elif k == 0x0B:                                                         # level based: 10..99, M = Level / 8 + 2
            a0, a1 = 10, 99; M = (self.level(sim, me) >> 3) + 2 >> (1 if self.has(sim, t, SHELL) else 0)
        elif k == 0x0A:                                                         # physical magic: the physical defence, no Shell, toad / mini = none
            a0, a1 = rec.p2, rec.p2 + (rec.p2 >> 3); mdef = 0 if self.has(sim, t, TOAD) or self.has(sim, t, MINI) else w[t.b + O_DEF]
        elif k in (0x06, 0x0D, 0x2D, 0x6C):
            if k == 0x2D and self.has(sim, t, FLOAT): return None                # ground magic (Titan): not floating ones
            a0, a1 = rec.p2, rec.p2 + (rec.p2 >> 3)
            if k == 0x6C and cr & rec.p3: a0, a1 = a0 << 3, a1 << 3             # Aqua Rake: eight times against its creature type
        else: return None
        if el and k != 0x2A:
            if w[t.b + O_ABSORB] & el or w[t.b + O_BLOCK] & el or w[t.b + O_IMMUNE] & el: return None
            if w[t.b + O_HALF] & el: a0 >>= 1; a1 >>= 1
            elif w[t.b + O_WEAK] & el: a0 <<= 1; a1 <<= 1; mdef = 0
        if n > 1 and not rec.side: a0 >>= 1; a1 >>= 1
        return (min(9999, max(0, a0 - mdef) * M), min(9999, max(0, a1 - mdef) * M))

    def kill_p(self, sim, me, sp, t):
        """Chance that the spell kills monster `t` outright (Death, Break, X-Zone, L5 Doom ...)."""
        rec = self.effect(sp); k = rec.kind; cr = self.creature(sim, t)
        if k == 0x17 and rec.p3 & 0x80:                                                    # Death: heals the undead instead
            return 0.0 if cr & UNDEAD or self.immune(sim, t, DEAD) else self.hit_p(sim, me, rec, t)
        if k == 0x18: return 0.0 if cr & HEAVY else self.hit_p(sim, me, rec, t)           # X-Zone / Odin ignore status immunity
        if k == 0x4B:
            return 0.0 if self.level(sim, t) % max(1, rec.p1) or cr & UNDEAD or self.immune(sim, t, DEAD) else 1.0
        if k == 0x12 and rec.p3 & 0x40:                                                    # Break: stone on a monster is death
            return 0.0 if self.immune(sim, t, STONE) else self.hit_p(sim, me, rec, t)
        return 0.0

    def gain(self, sim, me, sp, t, n=1, share=1.0):
        """HP one cast is expected to take off monster `t`: a kill counts all its HP, damage is capped by what is left.  `share`: part of a
        multi-hit spell's hits that land on `t`."""
        kp = self.kill_p(sim, me, sp, t); g = kp * t.hp
        r = self.dmg(sim, me, sp, t, n)
        if r is not None:
            rec = self.effect(sp)
            g += (1 - kp) * self.hit_p(sim, me, rec, t, n) * min(t.hp, (r[0] + r[1]) / 2 * rec.hits * share)
        return g

    # ---- the cast -------------------------------------------------------------------------------------------------------
    @staticmethod
    def mons_mask(ms):
        m = 0
        for t in ms: m |= 0x80 >> (t.i - 4)
        return m

    def cast(self, sp, monsters=(), party=(), second=None):
        """Command that casts spell `sp` (the menu's index is looked up from the id); `second` = a second cast for Dual-cast."""
        return Command(MAGIC_COMMAND, 0x28 if second else 0x20, self.mons_mask(monsters), Command.party_mask(*party), self.slot(sp), second)

    def options(self, sim, me, sp, mons):
        """[(gain, targets, per monster)] ways to aim damaging / killing spell `sp`: each single monster, or the whole side where the spell can."""
        rec = self.effect(sp); n = len(mons)
        if rec.roulette: return []
        banned = getattr(self.p, "banned", ()); s = self.slot(sp)
        usable = [m for m in mons if self.dmg(sim, me, sp, m) is not None or self.kill_p(sim, me, sp, m) > 0]
        bad = lambda m: self.has(sim, m, REFLECT) or (m.i, s) in banned or self.absorbs(sim, rec, m)
        if rec.side:                                                                       # hits every enemy: one that would bounce / absorb it rules it out
            if not usable or any(bad(m) for m in mons): return []
            share = 1.0 / n if rec.hits > 1 else 1.0
            per = {m.i: self.gain(sim, me, sp, m, 1, share) for m in mons}
            return [(sum(per.values()), list(mons), per)]
        out = [(g, [m], {m.i: g}) for m in usable if not bad(m) for g in [self.gain(sim, me, sp, m)]]
        if rec.can_widen and n > 1 and len(usable) == n and not any(bad(m) for m in mons):
            per = {m.i: self.gain(sim, me, sp, m, n) for m in mons}
            out.append((sum(per.values()), list(mons), per))
        return out

    def floor(self, sim, char, me):
        """MP kept back: one Cure's worth in hard fights, `mp_floor` of the pool in ordinary ones (the base policy's rule)."""
        return self.p._cure_reserve(sim, char) if self.p.hard else int(self.p.mp_floor * me.max_mp)

    def best_attack(self, sim, char, me, mons, ids, floor=0):
        """(gain, cost, spell, targets) of the best damaging / killing spell among `ids`; in ordinary fights the cheapest one within 85% of
        the best (the strongest in hard fights), None when nothing hurts."""
        cands = []
        for sp, cost in self.known(sim, char, ids):
            rec = self.effect(sp)
            if cost > me.mp - floor or rec.kind not in DAMAGE_KINDS | KILL_KINDS: continue
            if rec.kind == 0x12 and not rec.p3 & 0x40: continue                           # a status that is not death
            for g, tg, per in self.options(sim, me, sp, mons):
                if g > 0: cands.append((g, cost, sp, tg))
        if not cands: return None
        top = max(c[0] for c in cands)
        keep = [c for c in cands if c[0] >= (0.97 if self.p.hard else 0.85) * top]
        return min(keep, key=lambda c: (c[1], -c[0]))

    def value_of(self, sim, me, cmd, mons):
        """HP-equivalent value of the command the base policy chose (a spell cast or a Fight): what a plugin proposal has to beat."""
        if cmd.command == 0x05: return self.p.fight_value(sim, me, mons)
        if cmd.command != MAGIC_COMMAND or not cmd.monsters: return 0.0
        ts = [m for m in mons if cmd.monsters & (0x80 >> (m.i - 4))]
        if not ts or cmd.selected >= 95: return 0.0
        return sum(self.gain(sim, me, cmd.selected, t, len(ts)) for t in ts)

    # ---- pace of the fight ----------------------------------------------------------------------------------------------
    def turn_value(self, sim, ally, mons):
        """What one turn of party member `ally` is worth: its plain Fight, or its best spell."""
        v = self.p.fight_value(sim, ally, mons)
        if ally.mp:
            b = self.best_attack(sim, ally.i, ally, mons, [sp for sp, _ in self.known(sim, ally.i, range(36, 87))])
            if b: v = max(v, b[0])
        return v

    def rounds(self, sim, mons):
        """About how many turns each party member still has before the fight ends (monster HP over what the party deals per round), and the
        party's damage per round."""
        per = sum(self.turn_value(sim, a, mons) for a in self.allies(sim))
        return (sum(m.hp for m in mons) / per if per > 0 else 0.0), per

    # ---- time magic: haste and slow ---------------------------------------------------------------------------------------
    def haste_options(self, sim, char, me, mons, ids, floor):
        """[(value, command)] for Haste spells: a hasted ally acts twice as often for the rest of the fight (Haste has no timer), worth its
        turn value for the turns that are left."""
        R, per = self.rounds(sim, mons); out = []
        if R < 3: return out
        todo = [a for a in self.allies(sim) if not any(self.has(sim, a, s) for s in (HASTE, STOP, STONE))]
        if not todo: return out
        tv = {a.i: self.turn_value(sim, a, mons) for a in todo}
        for sp, cost in self.known(sim, char, ids):
            rec = self.rec(sp)
            if rec.kind != 0x16 or not rec.p3 & HASTE[1] or cost > me.mp - floor: continue
            if rec.side: out.append((sum(tv.values()) * 0.6 * (R - 1), self.cast(sp, party=[a.i for a in todo])))
            else: out += [(tv[a.i] * 0.6 * (R - 1), self.cast(sp, party=[a.i])) for a in todo]
        return out

    REGEN_FRAMES = 1150                                 # measured: Regen heals Level x Vitality / 16 about every 1150 frames

    def regen_options(self, sim, char, me, mons, ids, floor):
        """[(value, command)] for Regen on one ally: the healing it brings over the rest of the fight, as far as the monsters' scripted hits
        will take that much HP off it (the policy's own Cure costs a turn each time)."""
        R, per = self.rounds(sim, mons); out = []
        if R < 3 or not mons: return out
        allies = self.allies(sim)
        T = R * sum(self.cycle(sim, a) for a in allies) / len(allies) * self.FRAMES_PER_TICK
        incoming = sum(0.6 * self.threat(m) * T / (self.cycle(sim, m) * self.FRAMES_PER_TICK) for m in mons) / len(allies)
        for sp, cost in self.known(sim, char, ids):
            rec = self.rec(sp)
            if rec.kind != 0x14 or rec.p3 != REGEN[1] or cost > me.mp - floor: continue
            for a in allies:
                if self.has(sim, a, REGEN) or self.has(sim, a, STOP): continue
                lvl, vit = self.level(sim, a), min(99, sim.w[a.b + O_VIT] + sim.w[a.b + O_BONUS_VIT])
                heal = min(lvl * vit >> 4, a.max_hp) * T / self.REGEN_FRAMES
                out.append((0.4 * min(heal, incoming, a.max_hp), self.cast(sp, party=[a.i])))
        return out

    FRAMES_PER_TICK = 5.35                              # measured: one ATB tick lasts about 5.35 frames

    def status_frames(self, sim, me, rec, t):
        """Frames a timed status (Stop ...) of `rec` lasts on `t` (CalcStatusDuration: 2 x Param2 + caster level / 4 - target level / 4, 30 on a Heavy
        target, at least 1; a high bit in Param2 skips the target part), a tick being FRAMES_PER_TICK."""
        n = 2 * (rec.p2 & 0x7F) + (self.level(sim, me) >> 2)
        if not rec.p2 & 0x80: n = 30 if self.creature(sim, t) & HEAVY else max(1, n - (self.level(sim, t) >> 2))
        return min(255, n) * self.FRAMES_PER_TICK

    def cycle(self, sim, c):
        """ATB ticks between two turns of combatant `c` (ResetATB: weight / 2 + 120 - agility; Haste halves, Slow doubles)."""
        from jobcmds_phys import cycle
        return cycle(sim.w, c.b, *getattr(self.p, "atb", (120, 1)))

    def slow_options(self, sim, char, me, mons, ids, floor):
        """[(value, command)] for debuffs that make monsters act less (Slow: half as often; Stop: not at all for a while): the damage their
        actions would have done over the rounds the monster still lives."""
        R, per = self.rounds(sim, mons); out = []
        if R < 2 or per <= 0: return out
        banned = getattr(self.p, "banned", ())
        party_cycle = sum(self.cycle(sim, a) for a in self.allies(sim)) / len(self.allies(sim))
        for sp, cost in self.known(sim, char, ids):
            rec = self.rec(sp)
            if cost > me.mp - floor: continue
            if rec.kind == 0x16 and rec.p3 & SLOW[1]: st, frac = SLOW, 0.5
            elif rec.kind == 0x14 and rec.p3 & STOP[1]: st, frac = STOP, 1.0
            else: continue
            val = {}
            for m in mons:
                if self.has(sim, m, st) or self.immune(sim, m, st) or self.has(sim, m, REFLECT) or (m.i, self.slot(sp)) in banned: continue
                life = min(R, m.hp / per * len(mons))                                 # focus fire: it lives about hp / (damage per round) rounds
                acts = lambda rounds: rounds * party_cycle / self.cycle(sim, m)       # its turns in that many party rounds
                turns = acts(life) if st is SLOW else min(acts(life), self.status_frames(sim, me, rec, m) / (self.FRAMES_PER_TICK * self.cycle(sim, m)))
                val[m.i] = self.hit_p(sim, me, rec, m, len(mons) if rec.side else 1) * 0.6 * self.threat(m) * turns * frac
            if rec.side and val: out.append((sum(val.values()), self.cast(sp, monsters=mons)))
            elif val:
                m = max((m for m in mons if m.i in val), key=lambda m: val[m.i])
                out.append((val[m.i], self.cast(sp, monsters=[m])))
        return [(v, c) for v, c in out if v > 0]

    # ---- summons ----------------------------------------------------------------------------------------------------------
    def odin_option(self, sim, char, me, mons, floor):
        """Odin: Zantetsuken kills every monster at once with chance Level + 80 - level of the first monster (capped 1..99%) when none of them
        is Heavy; with a Heavy one around he throws Gungnir at a random slot instead (not counted)."""
        cost = self.cost(sim, char, 83)
        if cost is None or cost > me.mp - floor or any(self.creature(sim, m) & HEAVY for m in mons): return []
        q = min(99, max(1, self.level(sim, me) + 80 - sim.w[sim.combatant(4).b + O_LEVEL])) / 100
        return [(q * sum(m.hp for m in mons), self.cast(83, monsters=mons))]

    # ---- white magic: statuses on the party -----------------------------------------------------------------------------
    def afflicted(self, sim, mons, sts):
        """[(ally, value)] of the allies under a status of `sts` and what curing it is worth: the turns it costs them over the fight."""
        R, per = self.rounds(sim, mons)
        if not mons or R < 1: return []
        nominal = per / max(1, len(self.allies(sim))); out = []
        for a in sim.party:
            if not a.max_hp or self.raw(sim, a, DEAD): continue
            have = [s for s in sts if self.raw(sim, a, s)]
            if have: out.append((a, max(LOSS[s] for s in have) * max(nominal, self.turn_value(sim, a, mons)) * min(R, 4)))
        return out

    def cure_options(self, sim, char, me, mons, ids):
        """[(value, command)] for Esuna / Poisona on an ally under a status that costs it turns."""
        out = []
        for sp, cost in self.known(sim, char, ids):
            sts = CURES.get(sp)
            if not sts or cost > me.mp: continue
            for a, v in self.afflicted(sim, mons, sts):
                if a.i == char and self.has(sim, a, MUTE): continue
                out.append((v, self.cast(sp, party=[a.i]), a))
        return out

    def revive_option(self, sim, char, me, mons):
        """Phoenix: fire on every enemy and the chosen fallen ally comes back with full HP and MP."""
        cost = self.cost(sim, char, PHOENIX)
        dead = [c for c in sim.party if c.dead and c.i != char and c.max_hp and not self.has(sim, c, STONE)]
        if cost is None or cost > me.mp or not dead: return []
        a = max(dead, key=lambda c: c.max_mp + c.max_hp)
        return [(a.max_hp + a.max_mp, self.cast(PHOENIX, party=[a.i]))]

    def wind_option(self, sim, char, me, mons, ids):
        """White Wind (Blue): every ally gets HP equal to the caster's current HP."""
        out = []
        for sp, cost in self.known(sim, char, ids):
            if self.rec(sp).kind != 0x5A or cost > me.mp: continue
            hurt = [a for a in self.allies(sim) if a.hp < a.max_hp * self.p.heal_below]
            heal = sum(min(me.hp, a.max_hp - a.hp) for a in self.allies(sim))
            if len(hurt) >= 2 or hurt and me.hp * 0.8 <= hurt[0].max_hp - hurt[0].hp:
                out.append((heal, self.cast(sp, party=[a.i for a in self.allies(sim)])))
        return out

    # ---- dual cast -------------------------------------------------------------------------------------------------------
    def dual_attack(self, sim, char, me, mons, ids, floor):
        """(gain, command) of the best pair of damaging spells cast in one turn (Dual-cast): both cost MP, damage on one monster adds up
        but cannot exceed its HP."""
        parts = []
        for sp, cost in self.known(sim, char, ids):
            rec = self.effect(sp)
            if cost > me.mp - floor or rec.kind not in DAMAGE_KINDS | KILL_KINDS or rec.kind == 0x12 and not rec.p3 & 0x40: continue
            parts += [(cost, sp, tg, per) for g, tg, per in self.options(sim, me, sp, mons) if g > 0]
        best = None
        hp = {m.i: m.hp for m in mons}
        for i, a in enumerate(parts):
            for b in parts[i:]:
                if a[0] + b[0] > me.mp - floor: continue
                g = sum(min(hp[k], a[3].get(k, 0) + b[3].get(k, 0)) for k in hp)
                if best is None or g > best[0] * (1.0 if self.p.hard else 1.02) or g >= best[0] * 0.98 and a[0] + b[0] < best[1]: best = (g, a[0] + b[0], a, b)
        if best is None: return None
        g, _, a, b = best
        second = Command(MAGIC_COMMAND, 0x20, self.mons_mask(b[2]), 0, self.slot(b[1]))
        return g, Command(MAGIC_COMMAND, 0x28, self.mons_mask(a[2]), 0, self.slot(a[1]), second)

    # ---- spellblade ------------------------------------------------------------------------------------------------------
    def imbue_option(self, sim, char, me, mons, ids, floor):
        """Magic Sword: the spell is stored on the caster until replaced and every later Fight (with a weapon that allows it) uses it: elemental
        weakness doubles (L1), triples (L2) or kills outright (L3, four times the damage on a Heavy monster); an absorbing target would be healed,
        an immune one not hurt.  Worth: the better Fights over the turns that are left."""
        w = sim.w
        rh = GEAR + GEAR_SIZE * me.i + GEAR_RH                                  # the right-hand weapon record of the battle's gear table
        if not w[me.b + 0x38] & 0x04 or w[rh + 8] & 0x7F == 0x32: return []      # needs a Magic Sword weapon; a knife's imbued strike came out tiny in the sim
        R, per = self.rounds(sim, mons)
        if R < 1.5: return []
        have = [w[me.b + 0x50 + k] for k in range(3)] + [w[me.b + 0x53 + k] for k in range(3)]
        def fight(el1, el2, el3, special, m):
            """Expected damage of one Fight of `me` on `m` under the imbue (elements per level, status special)."""
            base = self.p.fight_value(sim, me, [m])
            el = el1 | el2 | el3
            if el & w[m.b + O_ABSORB]: return -base
            if el & w[m.b + O_IMMUNE]: return 0.0
            f = base
            if special & 0x80:                                                    # Flare sword: +100 Attack, defence / 4
                a, df = w[rh + 7], w[m.b + O_DEF]
                f = base * (a + 100 - df / 4) / max(1, a - df) if a > df else base * 4
            if el & w[m.b + O_HALF]: f *= 0.5
            weak = w[m.b + O_WEAK]
            if el3 & weak: f = m.hp if not self.creature(sim, m) & HEAVY and not self.has(sim, m, ZOMBIE) else f * 4
            elif el2 & weak: f *= 3
            elif el1 & weak: f *= 2
            return min(f, m.hp)
        def best(el1, el2, el3, special):
            return max(fight(el1, el2, el3, special, m) for m in mons)
        cur = best(have[0], have[1], have[2], have[5])
        out = []
        for sp, cost in self.known(sim, char, ids):
            rec = self.rec(sp)
            if rec.kind not in (3, 4, 5) or cost > me.mp - floor: continue
            v = best(rec.p2 if rec.kind == 3 else 0, rec.p2 if rec.kind == 4 else 0, rec.p2 if rec.kind == 5 else 0, rec.p3 if rec.kind == 5 else 0)
            if rec.kind != 5 and rec.p2 == 0: continue                              # status only swords: not valued
            if rec.kind == 5 and rec.p2 == 0 and not rec.p3 & 0x80: continue
            out.append(((v - cur) * min(R, 6), self.cast(sp, party=[char])))
        return [(v, c) for v, c in out if v > 0]


def spells_of(policy):
    s = getattr(policy, "spells", None)
    if s is None: s = policy.spells = Spells(policy)
    return s


class Magic(JobCommand):
    """The magic command `name` (White, Black, Time, Summon, Red, Blue, MgcSwrd, DuCast): attack spells that beat the base policy's choice, and
    the buffs / debuffs worth a turn.  Registered once per command name."""
    kind = "attack"
    def __init__(self, policy):
        super().__init__(policy)
        self.m = spells_of(policy)
        policy._base_value = self.m.value_of

    def ids(self): return FAMILY[self.name]

    def propose(self, sim, char, me, mons):
        M = self.m; n = self.name
        if me.dead or not mons: return None
        ids = list(self.ids()); floor = M.floor(sim, char, me); fv = self.p.fight_value(sim, me, mons)
        out = []
        if n == "MgcSwrd": return max(M.imbue_option(sim, char, me, mons, ids, floor), key=lambda x: x[0], default=None)
        b = M.best_attack(sim, char, me, mons, ids, floor)
        if b and (self.p.hard or me.mp - b[1] >= 0.6 * me.max_mp or b[0] >= 2 * fv):
            out.append((b[0], M.cast(b[2], monsters=b[3])))
        if n == "DuCast":
            d = M.dual_attack(sim, char, me, mons, ids, floor)
            if d and (self.p.hard or d[0] >= 2 * fv): out.append(d)
        if n in ("Time", "Red", "DuCast"):
            out += M.haste_options(sim, char, me, mons, ids, floor)
            out += M.slow_options(sim, char, me, mons, ids, floor)
            out += M.regen_options(sim, char, me, mons, ids, floor)
        if n in ("Summon", "DuCast"): out += M.odin_option(sim, char, me, mons, floor)
        return max(out, key=lambda x: x[0]) if out else None


class Cure(JobCommand):
    """Support of the magic commands: Esuna / Poisona on a stoned, charmed, asleep ... ally, Phoenix as a revive, White Wind for a hurt party."""
    kind = "support"
    def __init__(self, policy):
        super().__init__(policy)
        self.m = spells_of(policy)

    def propose(self, sim, char, me, mons):
        M = self.m
        if me.dead: return None
        ids = list(FAMILY[self.name])
        out = [(v, c, None) for v, c in M.revive_option(sim, char, me, mons)] if self.name in ("Summon", "DuCast") else []
        if self.name in ("White", "Red", "DuCast"):
            fv = self.p.fight_value(sim, me, mons) if mons else 0
            out += [(v, c, (a.i, "cure")) for v, c, a in M.cure_options(sim, char, me, mons, ids) if v > fv and (a.i, "cure") not in M.claimed(sim)]
        if self.name == "Blue": out += [(v, c, None) for v, c in M.wind_option(sim, char, me, mons, ids)]
        if not out: return None
        v, c, key = max(out, key=lambda x: x[0])
        if key: M.claim(key, sim)
        return v, c


def _make(name):
    register(type(name + "Magic", (Magic,), {"name": name}))
    if name in ("White", "Red", "DuCast", "Summon", "Blue"): register(type(name + "Cure", (Cure,), {"name": name}))


for _n in FAMILY: _make(_n)
