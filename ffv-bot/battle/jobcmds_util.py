"""Utility job commands for the battle policy (jobcmds.py plugins): Mime (Mimic), Chemist (Recover, Revive, Drink, Mix), Item use beyond
Potions, Tamer (Catch, Release, Calm), Geomancer (Terrain), Bard (Sing), Dancer (Dance, Flirt), Escape and Row.  The magic families are in
magicpolicy.py.

Everything is worked out from the game's own tables (battle-main.asm) with the formulas of magicpolicy.Spells (hit %, damage range, kill chance;
lowest / highest roll, never the next roll).  What the code relies on, measured in the simulator:

  Mimic    repeats the last action of any party member (SavedAction, $41D4) for free: no MP, with the Mime's own level and stats
  Recover  Esuna on the whole party for nothing;  Revive  Life with a sixteenth of the HP and all MP on the whole party for nothing
  Sing     a stat song lifts Strength / Agility / Magic / Level of the whole party by one about every 175 frames, 17 after 3000 frames, and the
           bonus stays; the singer cannot act for those 3000 frames.  Stop and Charm songs reach every enemy
  Dance    one of four at random: Psyche (MP), Drain, Charm (never misses) or the sword dance (a Fight that never misses with double Attack and M)
  Flirt    the monster loses its next action (a Heavy one does not fall for it); Hit% = 50 + level - target level, then its magic evade
  Terrain  one of four attacks of the battle background, picked by a random number from 0 to the user's level, on a random enemy
  Catch    a monster at critical HP is taken (counts as dead) and the Catch slot turns into Release: that monster's attack, its own level / magic
  Calm     Stop on a beast (creature type 4) for a short while, no hit roll
  Mix      two consumables make one of the abilities of MixComboTbl; forbidden by default (jobcmds.FORBIDDEN_DEFAULT)
  Escape   always works unless the battle cannot be left (FleeChance $3EF0 = 80h)"""
import jobcmds_phys as phys
from sim import Command
from jobcmds import JobCommand, register
import magicpolicy
from magicpolicy import (spells_of, Caster, Rec, CURES, REFLECT, STOP, CHARM, STONE, DEAD, HEAVY, DAMAGE_KINDS, KILL_KINDS)

ABILITY_PROP = 0x116AB1             # ROM: SpecialAbilityAttackProp, 8 bytes per ability (Mix results, Terrain)
BATTLE_CMD_PROP = 0x1159E0          # ROM: BattleCmdProp, 8 bytes per command (Flirt, Calm ...)
MIX_COMBOS = 0x116EF9               # ROM: 12 x 12 table [second item][first item] -> ability row
TERRAIN_TABLE = 0x116DF9            # ROM: 4 abilities per battle background
MONSTER_RELEASE = 0x108600          # ROM: spell row of the attack of a released monster (per monster id)
MONSTER_PROP = 0x100000             # ROM: 32 bytes per monster (magic at +5, level at +31)
SAVED_ACTION, TERRAIN_TYPE, FLEE_CHANCE = 0x41D4, 0x04F2, 0x3EF0
O_CAUGHT, O_STATUS4, O_CMDST_BYTE, O_CMD_IMMUNITY, O_ARMOR = 0x15, 0x1D, 0x1E, 0x66, 0x39
CMD_ITEM, CMD_ROW, CMD_ESCAPE, CMD_FIGHT = 2, 3, 0x0A, 5
CMD_CALM, CMD_CATCH, CMD_RELEASE, CMD_MIX, CMD_DRINK, CMD_RECOVER, CMD_REVIVE = 0x1B, 0x1D, 0x1E, 0x1F, 0x20, 0x21, 0x22
CMD_TERRAIN, CMD_SING, CMD_FLIRT, CMD_DANCE, CMD_MIMIC = 0x23, 0x28, 0x29, 0x2A, 0x2B
SONG_STR, SONG_AGI, SONG_MAG, SONG_LEVEL, SONG_STOP, SONG_CHARM = 87, 88, 90, 91, 93, 94
SONG_FRAMES, SONG_BONUS, SONG_STEP, CHARM_FRAMES = 3000, 17, 175, 1500       # charm: measured 1590 and more than 2500 frames
DRAIN = 0x7A
NO_CATCH = 0xFF
ESUNA_STATUS = CURES[29]
ATTACK_ITEMS = (0xFC, 0xFD, 0xFE)                      # Flame, Water, Thunder
KEEP_FOR_HEALING = (0xE0, 0xE1, 0xE2, 0xE3, 0xE4)      # Potions, Ether, Elixir, Phoenix Down: Mix leaves them to the healers
FOREVER = 10 ** 9


def stock(sim):
    """{item id: (inventory slot, quantity)} of the consumables (ids 0xE0..) in the battle inventory."""
    w = sim.w
    return {it: (s, w[phys.INV_QTY + s]) for s, it in sim.inventory_slots.items() if it >= 0xE0 and w[phys.INV_QTY + s]}


def commands_of(sim, me): return [c for c in sim.w[me.b + 0x16:me.b + 0x1A] if c and not c & 0x80]


def to_all(mons): return Command.monster_mask(*[m.i - 4 for m in mons])


class Util(JobCommand):
    def __init__(self, policy):
        super().__init__(policy)
        self.M = spells_of(policy)

    def fv(self, sim, me, mons): return self.p.fight_value(sim, me, mons)

    def pace(self, sim, mons):
        """(turns each member still has before the fight ends, damage the party deals per round)."""
        return self.M.rounds(sim, mons)

    def frames(self, sim, c): return self.M.cycle(sim, c) * self.M.FRAMES_PER_TICK


# ---- Mime ---------------------------------------------------------------------------------------------------------------------------------
@register
class MimicOnly(Util):
    """The Mime has no Fight: Mimic is all there is.  With nothing saved yet the command does nothing, so Guard keeps the turn from being lost."""
    name, kind = "Mimic", "only"
    def propose(self, sim, char, me, mons):
        if CMD_FIGHT in commands_of(sim, me) or me.dead: return None
        return 1.0, (Command(CMD_MIMIC, 0x80) if sim.w[SAVED_ACTION + 1] else Command.guard())


@register
class Mimic(Util):
    """Mimic beside a Fight: worth what the saved action does when the Mime does it, for nothing (damage spells with the Mime's own level and
    magic; a Fight is no gain over a Fight)."""
    name, kind = "Mimic", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; flag, cmd, mon, party, sel, flag2 = sim.w[SAVED_ACTION:SAVED_ACTION + 6]
        ts = [m for m in mons if mon & (0x80 >> (m.i - 4))]
        if not ts or not flag & 0x20 or flag & 0x08 or cmd < 0x2C or flag2: return None
        sp = sel + magicpolicy.BLUE_SLOT_SHIFT if sel >= magicpolicy.BLUE_FIRST - magicpolicy.BLUE_SLOT_SHIFT else sel
        rec = M.effect(sp)
        if rec.kind not in DAMAGE_KINDS | KILL_KINDS or rec.kind == 0x12 and not rec.p3 & 0x40: return None
        v = sum(M.gain(sim, me, sp, t, len(ts)) for t in ts)
        return (v, Command(CMD_MIMIC, 0x80)) if v > 0 else None


# ---- Chemist: Recover, Revive, Drink ---------------------------------------------------------------------------------------------------
@register
class Recover(Util):
    """Esuna on the whole party, for nothing: worth the turns the afflicted allies lose.  Asked before anything else ('only'): it costs no MP and
    no item."""
    name, kind = "Recover", "only"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; taken = M.claimed(sim)
        need = [(a, v) for a, v in M.afflicted(sim, mons, ESUNA_STATUS) if (a.i, "cure") not in taken]
        v = sum(x for _, x in need)
        if not need or v <= self.fv(sim, me, mons): return None
        for a, _ in need: M.claim((a.i, "cure"), sim)
        return v, Command(CMD_RECOVER, 0x80, 0, Command.party_mask(*range(4)))


@register
class Revive(Util):
    """Life on the whole party for nothing: a fallen ally comes back with a sixteenth of its HP and all its MP and is worth its turns again."""
    name, kind = "Revive", "only"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; R, per = self.pace(sim, mons); taken = M.claimed(sim)
        down = [a for a in sim.party if a.max_hp and M.raw(sim, a, DEAD) and not M.raw(sim, a, STONE) and (a.i, "revive") not in taken]
        if not down or R < 1: return None
        nominal = per / max(1, len(M.allies(sim)))
        v = sum(max(nominal, M.turn_value(sim, a, mons)) * min(R, 4) * 0.5 for a in down)
        if v <= self.fv(sim, me, mons): return None
        for a in down: M.claim((a.i, "revive"), sim)
        return v, Command(CMD_REVIVE, 0x80, 0, Command.party_mask(*range(4)))


class ItemUse(Util):
    """Consumables beyond the Potions and Phoenix Downs the base policy throws around: cures for statuses (Antidote, Eyedrops, Maiden's Kiss,
    Gold Needle, Mallet) and Ether for a caster out of MP.  `cid` is Item or the Chemist's Drink (the same thing)."""
    cid = CMD_ITEM

    def cures(self, items):
        """{item id: statuses it removes} for the cure items in `items` (attack type 0x99: the three params are AND masks of the status bytes)."""
        out = {}
        for it in items:
            if it > 0xED: continue
            o = phys.CONSUMABLE_PROP + 8 * (it - 0xE0); row = self.p.d.r[o:o + 8]
            if row[4] != 0x99: continue
            cured = {s for s in ESUNA_STATUS if not row[5 + s[0]] & s[1]}
            if cured: out[it] = cured
        return out

    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; have = stock(sim); fv = self.fv(sim, me, mons); best = None
        taken = M.claimed(sim)
        for it, cured in self.cures(have).items():
            for a, v in M.afflicted(sim, mons, cured):
                if v > fv and (a.i, "cure") not in taken and (best is None or v > best[0]):
                    best = (v, Command(self.cid, 0x40, 0, Command.party_mask(a.i), have[it][0]), a.i, "cure")
        if 0xE2 in have:                                            # Ether
            R, per = self.pace(sim, mons)
            for a in M.allies(sim):
                tv = M.turn_value(sim, a, mons)
                if a.max_mp >= 40 and a.mp < 0.2 * a.max_mp and R >= 2 and tv > 1.5 * self.fv(sim, a, mons) and (a.i, "ether") not in taken:
                    v = tv * min(R, 3) * 0.5
                    if v > fv and (best is None or v > best[0]): best = (v, Command(self.cid, 0x40, 0, Command.party_mask(a.i), have[0xE2][0]), a.i, "ether")
        if best is None: return None
        M.claim((best[2], best[3]), sim)
        return best[:2]


@register
class ItemSupport(ItemUse):
    name, kind = "Item", "support"


@register
class DrinkSupport(ItemUse):
    name, kind, cid = "Drink", "support", CMD_DRINK


class ItemAttack(Util):
    """Flame / Water / Thunder (consumables with the power of a spell, on every enemy): a limited stock, so only for a clear gain."""
    cid = CMD_ITEM
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; have = stock(sim); best = None
        for it in ATTACK_ITEMS:
            if it not in have: continue
            rec = Rec(self.p.d.r, it - 0xE0, phys.CONSUMABLE_PROP)
            if rec.kind not in DAMAGE_KINDS or any(M.absorbs(sim, rec, m) for m in mons): continue
            v = sum(M.gain(sim, me, rec, m) for m in mons)
            if v > 0 and (best is None or v > best[0]): best = (v, Command(self.cid, 0x40, to_all(mons), 0, have[it][0]))
        if best is None or best[0] < (1.0 if self.p.hard else 2.0) * self.fv(sim, me, mons): return None
        return best


@register
class ItemAttackOffense(ItemAttack):
    name, kind = "Item", "offense"


@register
class DrinkAttackOffense(ItemAttack):
    name, kind, cid = "Drink", "offense", CMD_DRINK


# ---- Mix (forbidden by default) -----------------------------------------------------------------------------------------------------------
@register
class Mix(Util):
    """Mix two consumables: every pair is looked up in the combination table; the result is an ability row valued like a spell (damage, kill).
    Healing supplies (Potions, Ether, Elixir, Phoenix Down) are not mixed away."""
    name, kind = "Mix", "attack"

    def pairs(self, have):
        ids = sorted(have)
        for i, a in enumerate(ids):
            for b in ids[i:]:
                if a > 0xEB or b > 0xEB or a in KEEP_FOR_HEALING or b in KEEP_FOR_HEALING: continue
                if a == b and have[a][1] < 2: continue
                yield a, b, self.p.d.r[MIX_COMBOS + 12 * (b - 0xE0) + (a - 0xE0)]

    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; have = stock(sim); best = None
        for a, b, row in self.pairs(have):
            rec = M.rec(row, ABILITY_PROP)
            if rec.kind not in DAMAGE_KINDS | KILL_KINDS or rec.kind == 0x12 and not rec.p3 & 0x40 or rec.roulette or not rec.tgt & 0x08: continue
            if rec.side:
                if any(M.has(sim, m, REFLECT) or M.absorbs(sim, rec, m) for m in mons): continue
                g = sum(M.gain(sim, me, rec, m, 1, 1.0 / len(mons) if rec.hits > 1 else 1.0) for m in mons); tg = list(mons)
            else:
                cand = [(M.gain(sim, me, rec, m), m) for m in mons if not M.absorbs(sim, rec, m)]
                if not cand: continue
                g, m = max(cand, key=lambda c: c[0]); tg = [m]
            if g > 0 and (best is None or g > best[0]): best = (g, a, b, tg)
        if best is None: return None
        g, a, b, tg = best
        return g, Command(CMD_MIX, 0x40, to_all(tg), 0, have[a][0], Command(CMD_MIX, 0x40, 0, 0, have[b][0]))


# ---- Tamer ---------------------------------------------------------------------------------------------------------------------------------
def release_attack(policy, mid):
    """(Rec, Caster) of the attack a released monster `mid` uses: its own spell row with its own level and magic."""
    r = policy.d.r
    return Rec(r, r[MONSTER_RELEASE + mid]), Caster(r[MONSTER_PROP + 32 * mid + 31], r[MONSTER_PROP + 32 * mid + 5])


def release_value(M, sim, rec, caster, mons):
    """HP the released monster's attack takes off `mons` (what one Release is worth)."""
    if rec.kind not in DAMAGE_KINDS | KILL_KINDS or rec.roulette or not rec.tgt & 0x08: return 0.0
    ok = [m for m in mons if not M.absorbs(sim, rec, m)]
    if rec.side: return sum(M.gain(sim, caster, rec, m, 1, 1.0 / len(mons) if rec.hits > 1 else 1.0) for m in ok)
    return max((M.gain(sim, caster, rec, m) for m in ok), default=0.0)


@register
class Catch(Util):
    """Catch: a monster at critical HP (or at half HP with the Catch gear) is taken for good and counts as dead (same rewards as a kill); the
    character's Catch slot then works as Release (the battle keeps the slot's command id in CharStruct, so the plugin is asked under the name
    Catch): the monster's own attack, free of MP, when it does more than a Fight.  Worth the kill (a Fight would do the same on such a monster)
    plus half of how much better its attack is than a Fight, so only monsters whose Release beats the party's Fights are caught."""
    name, kind = "Catch", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        caught = sim.w[me.b + O_CAUGHT]
        return self.release(sim, me, mons, caught) if caught != NO_CATCH else self.catch(sim, me, mons)

    def release(self, sim, me, mons, caught):
        M = self.M; rec, c = release_attack(self.p, caught)
        v = release_value(M, sim, rec, c, mons)
        if v <= 0: return None
        if rec.side: return v, Command(CMD_RELEASE, 0x80, to_all(mons))
        tgt = max(mons, key=lambda m: M.gain(sim, c, rec, m))
        return v, Command(CMD_RELEASE, 0x80, Command.monster_mask(tgt.i - 4))

    def catch(self, sim, me, mons):
        M = self.M; w = sim.w; best = None
        if len(mons) < 2: return None
        for m in mons:
            mid = phys.monster_id(sim, m)
            critical = w[m.b + O_STATUS4] & 0x02 or w[me.b + O_ARMOR] & 0x01 and m.hp * 2 <= m.max_hp
            if mid > 255 or w[m.b + O_CMD_IMMUNITY] & 0x08 or not critical or phys.counter_unsafe(self.p, sim, me, m, CMD_CATCH): continue
            rec, c = release_attack(self.p, mid)
            rest = [x for x in mons if x is not m]
            extra = release_value(M, sim, rec, c, rest) - self.fv(sim, me, rest)
            if extra > 0 and (best is None or extra > best[0]): best = (extra, m)
        if best is None: return None
        extra, m = best
        return m.hp + 0.5 * extra, Command(CMD_CATCH, 0x80, Command.monster_mask(m.i - 4))


@register
class Calm(Util):
    """Calm: Stop on a beast (creature type 4) with no hit roll, for about 365 frames (a Stop spell: 912)."""
    name, kind = "Calm", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; R, per = self.pace(sim, mons)
        if R < 2 or per <= 0: return None
        rec = Rec(self.p.d.r, CMD_CALM, BATTLE_CMD_PROP); best = None
        for m in mons:
            if not M.creature(sim, m) & 0x04 or M.has(sim, m, STOP) or M.immune(sim, m, STOP) or phys.counter_unsafe(self.p, sim, me, m, CMD_CALM): continue
            turns = min(m.hp / per * len(mons), R, M.status_frames(sim, me, rec, m) / self.frames(sim, m))
            v = 0.6 * M.threat(m) * turns
            if best is None or v > best[0]: best = (v, m)
        return (best[0], Command(CMD_CALM, 0x80, Command.monster_mask(best[1].i - 4))) if best and best[0] > 0 else None


# ---- Geomancer ----------------------------------------------------------------------------------------------------------------------------
@register
class Terrain(Util):
    """Terrain: one of four attacks of the battle background, chosen by a random number from 0 to the user's level (up to 10 the first, to 20
    the second, to 49 the third, above that the fourth); single-target ones hit a random enemy.  Worth the mean of what each would do."""
    name, kind = "Terrain", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; lvl = min(99, M.level(sim, me)); o = TERRAIN_TABLE + 4 * sim.w[TERRAIN_TYPE]
        edges = (0, 11, 21, 50, lvl + 1); total = 0.0
        for s in range(4):
            n = min(edges[s + 1], lvl + 1) - edges[s]
            if n <= 0: continue
            rec = M.rec(self.p.d.r[o + s], ABILITY_PROP)
            if not rec.tgt & 0x08: continue
            if rec.side: g = sum(M.gain(sim, me, rec, m, 1, 1.0 / len(mons) if rec.hits > 1 else 1.0) for m in mons)
            else: g = sum(M.gain(sim, me, rec, m) for m in mons) / len(mons)
            total += g * n / (lvl + 1)
        return total, Command(CMD_TERRAIN, 0x80)


# ---- Dancer -------------------------------------------------------------------------------------------------------------------------------
def flirt_p(M, sim, me, rec, t):
    """Chance the monster falls for it: Hit% = Param1 + level - target level (1..99), then its magic evade."""
    hit = min(99, max(1, rec.p1 + M.level(sim, me) - M.level(sim, t)))
    return hit / 100 * (1 - sim.w[t.b + 0x2E] / 100)


@register
class Dance(Util):
    """Dance: a quarter each of Psyche (drains MP: no use), Drain, Charm (never misses) and the sword dance (a Fight that never misses with
    double Attack and double M); the monster is chosen first.  A charmed monster acts at random for a while, worth a Slow."""
    name, kind = "Dance", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; best = None; drain = M.rec(DRAIN)
        R, per = self.pace(sim, mons)
        for m in mons:
            if phys.counter_unsafe(self.p, sim, me, m, CMD_DANCE): continue
            v = M.gain(sim, me, drain, m) + min(m.hp, phys.phys_expected(sim, me, m, dm=0xD0))
            if not M.has(sim, m, CHARM) and not M.immune(sim, m, CHARM) and not M.has(sim, m, REFLECT):
                v += 0.6 * M.threat(m) * min(R, 2)
            if best is None or v > best[0]: best = (v, m)
        if best is None: return None
        v, m = best
        return v / 4, Command(CMD_DANCE, 0x80, Command.monster_mask(m.i - 4))


@register
class Flirt(Util):
    """Flirt: the monster wastes its next action.  Worth a part of its strongest hit, for a monster that lives long enough to act."""
    name, kind = "Flirt", "offense"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons: return None
        M = self.M; rec = Rec(self.p.d.r, CMD_FLIRT, BATTLE_CMD_PROP); best = None
        R, per = self.pace(sim, mons)
        if per <= 0: return None
        for m in mons:
            if M.creature(sim, m) & HEAVY or sim.w[m.b + O_CMDST_BYTE] & 0x08 or phys.counter_unsafe(self.p, sim, me, m, CMD_FLIRT): continue
            life = min(1.0, m.hp / per * len(mons))
            v = flirt_p(M, sim, me, rec, m) * 0.6 * M.threat(m) * life
            if best is None or v > best[0]: best = (v, m)
        return (best[0], Command(CMD_FLIRT, 0x80, Command.monster_mask(best[1].i - 4))) if best and best[0] > 0 else None


# ---- Bard ----------------------------------------------------------------------------------------------------------------------------------
@register
class Sing(Util):
    """Sing.  A stat song lifts a stat of the whole party by one every ~175 frames up to 17 (it stays), and keeps the singer busy for 3000
    frames: worth it in a long fight when the party's damage grows with the stat (Strength for fighters, Magic for casters, Level for all,
    Agility for more turns).  Stop and Charm reach every enemy at once."""
    name, kind = "Sing", "attack"

    @staticmethod
    def ramp(T):
        """Mean bonus over the next T frames."""
        full = SONG_BONUS * SONG_STEP
        return T / (2 * SONG_STEP) if T <= full else SONG_BONUS - full * full / (2 * SONG_STEP * T)

    def propose(self, sim, char, me, mons):
        M = self.M; w = sim.w
        if me.dead or not mons: return None
        R, per = self.pace(sim, mons)
        allies = [a for a in M.allies(sim) if a.i != char]
        out = []
        if R > 1 and allies:
            T = R * sum(self.frames(sim, a) for a in allies) / len(allies)        # frames the fight still has
            avg = self.ramp(T)
            lost = M.turn_value(sim, me, mons) * min(R, SONG_FRAMES / self.frames(sim, me))
            def worth(stat):
                tot = 0.0
                for a in allies:
                    lvl = M.level(sim, a); st = phys.stat(w, a.b, phys.O_STR, phys.O_BONUS_STR); mg = phys.stat(w, a.b, phys.O_MAG, phys.O_BONUS_MAG)
                    tv = M.turn_value(sim, a, mons); magic = tv > self.fv(sim, a, mons)
                    Mp = ((lvl * mg) >> 8) + 4 if magic else ((lvl * st) >> 7) + 2
                    if stat == "lvl": rel = (mg / 256 if magic else st / 128) / Mp
                    elif stat == "str": rel = 0.0 if magic else lvl / 128 / Mp
                    elif stat == "mag": rel = lvl / 256 / Mp if magic else 0.0
                    else: rel = 1 / max(1.0, M.cycle(sim, a) - avg)               # fewer ticks between turns
                    tot += tv * R * min(0.5, avg * rel)
                return tot
            for sp, stat in ((SONG_LEVEL, "lvl"), (SONG_STR, "str"), (SONG_MAG, "mag"), (SONG_AGI, "agi")):
                if M.cost(sim, char, sp) is not None:
                    out.append((worth(stat) - lost, Command(CMD_SING, 0x20, 0, Command.party_mask(char), M.slot(sp))))
        for sp, st in ((SONG_STOP, STOP), (SONG_CHARM, CHARM)):
            if M.cost(sim, char, sp) is None: continue
            rec = M.rec(sp); val = 0.0
            for m in mons:
                if M.has(sim, m, st) or M.immune(sim, m, st) or M.has(sim, m, REFLECT): continue
                life = min(R, m.hp / per * len(mons)) if per > 0 else R
                turns = min(life, (M.status_frames(sim, me, rec, m) if st is STOP else CHARM_FRAMES) / self.frames(sim, m))
                val += M.hit_p(sim, me, rec, m, len(mons)) * 0.6 * M.threat(m) * turns
            out.append((val, Command(CMD_SING, 0x20, to_all(mons), 0, M.slot(sp))))
        out = [x for x in out if x[0] > 0]
        return max(out, key=lambda x: x[0]) if out else None


# ---- Escape, Row ---------------------------------------------------------------------------------------------------------------------------
@register
class Escape(Util):
    """Run when the battle is lost and may be left: the monsters' scripted hits would take the party's HP in fewer rounds than the party needs
    to kill them (a fraction of each monster's strongest hit per round), and the encounter can be fled from at all (FleeChance).  The attempt
    always works.  Asked first ('only'): healing a party that is wiped next round only loses time."""
    name, kind = "Escape", "only"
    def propose(self, sim, char, me, mons):
        if me.dead or not mons or sim.w[FLEE_CHANCE] & 0x80: return None
        M = self.M; allies = M.allies(sim); R, per = self.pace(sim, mons)
        if per <= 0: R = float("inf")
        hp = sum(a.hp for a in allies)
        party_cycle = sum(self.frames(sim, a) for a in allies) / len(allies)
        incoming = sum(0.6 * M.threat(m) * party_cycle / self.frames(sim, m) for m in mons)
        if incoming <= 0: return None
        S = hp / incoming                                          # party rounds until the HP is gone
        return (1.0, Command(CMD_ESCAPE, 0x80)) if S < 1.5 and R > 2 * S else None


# Not registered: the Row A/B showed no clear win, so the bot leaves rows alone.
class Row(Util):
    """Rows.  A caster or archer in front steps back when two monster rounds would kill it, once per battle for each member.  Stepping forward again was
    measured to cost more turns than the extra damage returns, so a fighter in the back row stays there."""
    name, kind = "Row", "support"
    def propose(self, sim, char, me, mons):
        M = self.M
        if me.dead or not mons or me.row & 0x80 or (char, "row") in M.claimed(sim, FOREVER): return None
        R, per = self.pace(sim, mons)
        if R < 3: return None
        fv = self.fv(sim, me, mons); tv = M.turn_value(sim, me, mons)
        melee = any(s.kind in phys.BACK_ROW for s in phys.strikes(sim, me))
        hit = phys.phys_threat(self.p, sim, me, mons)
        if tv > 0 and (tv > 1.5 * fv or not melee) and 2 * hit >= me.hp and 0.3 * hit * min(R, 4) > tv: v = 0.3 * hit * min(R, 4)
        else: return None
        M.claim((char, "row"), sim, FOREVER)
        return v, Command(CMD_ROW, 0x80)
