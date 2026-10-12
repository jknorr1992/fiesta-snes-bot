"""Physical job commands for the battle policy (jobcmds.py plugins): Steal / Mug, Jump / Lance, Throw, Aim / Animals, Slash, Kick, Focus /
Chakra, Guard, SwdSlap, Gil Toss.  Everything is decided from the game's own formulas (battle-main.asm) with their lowest roll and the
monsters' real stat bytes in battle RAM; no roll is ever predicted (veteran-knowledge rule).  Hit chances are the formulas' percentages.

Core: `phys_low(sim, me, m)` = lowest damage of one plain Fight on monster `m` (both hands), `phys_range` = (lowest, highest) and
`policy._fight_value` (installed by FightCore) = the best expected Fight of this character, which every offense command must beat.

Damage per strike = (Attack - Defense) * M, Attack / M from the weapon's formula (AtkType, see `Strike.range`), then the command's DamageMod
(Aim: auto hit, Focus: M x2, Throw: x2 and auto hit, Jump landing: auto hit), the rows (each halves M), Double Grip, Protect, Defend, Guard
and the element tables.
Timing: after an action ATB restarts at `cycle()` = EqWeight/2 + 120 - Agility ticks (this ROM's ResetATB; Haste halves, Slow doubles);
commands with a delay (BattleCmdDelay: Focus 60, Slash 60, Aim 5, Jump 10 + 80 until the landing) add it to the turn, so their value is
multiplied by cycle / (cycle + delay) before it is compared with a plain Fight.
Jump: untargetable between take-off and landing (TargetNotActive on CmdStatus 0x10), lands with the weapon's formula: spears get M x2 and no
row penalty, other weapons just an auto hit; the menu does not reopen before the landing (the ATB timer holds the queued landing), so a
second command can not be issued meanwhile.  Machine Head's script (Interceptor Rocket) is the only one that aims at a jumper."""
import re

from jobcmds import register, JobCommand
from sim import Command

# ---- battle RAM / CharStruct offsets (battle_ram.inc)
O_ROW, O_LEVEL = 0x00, 0x02
O_RHSH, O_LHSH, O_RHW, O_LHW = 0x11, 0x12, 0x13, 0x14
O_ST1, O_ST2, O_ST3, O_CMDST = 0x1A, 0x1B, 0x1C, 0x1E
O_PASS2, O_EQW = 0x21, 0x23
O_STR, O_AGI, O_MAG = 0x28, 0x29, 0x2B
O_EVADE, O_DEF, O_MEVADE, O_MDEF = 0x2C, 0x2D, 0x2E, 0x2F
O_ABSORB, O_BLOCK, O_IMMUNE, O_HALF, O_WEAK = 0x30, 0x31, 0x32, 0x33, 0x34
O_ARMORPROP, O_CANTEVADE, O_CREATURE, O_STOLEN = 0x39, 0x64, 0x65, 0x6B
O_ALWAYS1, O_ALWAYS2, O_ALWAYS3 = 0x70, 0x71, 0x72
O_BONUS_STR, O_BONUS_AGI, O_BONUS_MAG, O_BONUS_LEVEL = 0x74, 0x75, 0x77, 0x78
GEAR, GEAR_SIZE, GEAR_RH, GEAR_LH, REC = 0x4049, 84, 60, 72, 12     # per character: 7 equipment records of 12 bytes (RH weapon at +60, LH at +72)
BATTLE_MONSTER_ID = 0x4020                                           # u16 per monster slot
INV_QTY = 0x2834
GIL = 0x0947                                                         # 3 bytes, the same WRAM in and out of battle
MONSTER_ITEM = 0x105000      # ROM: 4 bytes per monster: rare steal, common steal, rare drop, common drop
CONSUMABLE_PROP = 0x110A80   # ROM: 8 bytes per consumable (item id - 0xE0)

CMD_GUARD, CMD_KICK, CMD_FOCUS, CMD_CHAKRA, CMD_STEAL, CMD_MUG, CMD_JUMP, CMD_LANCE = 6, 7, 8, 9, 0x0B, 0x0C, 0x0D, 0x0E
CMD_THROW, CMD_SWDSLAP, CMD_GILTOSS, CMD_SLASH, CMD_ANIMALS, CMD_AIM = 0x11, 0x12, 0x13, 0x14, 0x15, 0x16
DELAY = {CMD_FOCUS: 60, CMD_SLASH: 60, CMD_AIM: 5, CMD_JUMP: 10 + 80}      # ROM BattleCmdDelay (ticks before the action lands)
ATB_BASE, ATB_SHIFT = 120, 1       # ResetATB in this ROM: EqWeight >> 1 + 120 - Agility (see atb_constants)

# weapon formulas (AtkType & 0x7F) -> how the strike is built; see the Attack30..73 procedures
FIST, SWORD, KNIFE, SPEAR, AXE, BOW_STATUS, BOW_ELEMENT, KATANA, WHIP, BELL, LONG_AXE, ROD, RUNE, CHICKEN, BRAVE = (
    0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x64, 0x6E)
SPEAR_CREATURE, BOW_CREATURE, THROW = 0x73, 0x72, 0x45
BACK_ROW = {FIST, SWORD, KNIFE, AXE, KATANA, RUNE, CHICKEN, BRAVE}          # BackRowMod (spears through CheckJump)
DOUBLE_GRIP = {SWORD, AXE, KATANA, LONG_AXE, RUNE, BRAVE}
HALF_EVADE = {KNIFE, BOW_STATUS, BOW_ELEMENT, CHICKEN, BOW_CREATURE}
OWN_HIT = {AXE, LONG_AXE, RUNE, BOW_STATUS, BOW_ELEMENT, WHIP, ROD}                # Hit% = Param1 (others 100)
HEAVY, UNDEAD, HUMAN = 0x20, 0x01, 0x80                                       # CreatureType bits


def atb_constants(rom):
    """(base, shift) of ResetATB read from the ROM: `lda EqWeight / jsr ShiftDivide / clc / adc #base / sec / sbc Agility`; the call
    enters the LSR chain at $01C2 - shift.  Falls back to this ROM's values when the code is not found."""
    m = re.search(rb"\xbd\x23\x20\x20(.)\x01\x18\x69(.)\x38\xed\xe2\x7b", rom[0x20000:0x30000], re.S)
    if not m: return ATB_BASE, ATB_SHIFT
    return m.group(2)[0], 0x1C2 - (0x100 | m.group(1)[0])


def stat(w, a, o, bonus=None):
    return min(99, w[a + o] + (w[a + bonus] if bonus else 0))


def status1(w, o): return w[o + O_ST1] | w[o + O_ALWAYS1]
def status2(w, o): return w[o + O_ST2] | w[o + O_ALWAYS2]
def status3(w, o): return w[o + O_ST3] | w[o + O_ALWAYS3]


def cycle(w, a, base=ATB_BASE, shift=ATB_SHIFT):
    """ATB ticks between the end of one action of the character at CharStruct `a` and its next turn (ResetATB)."""
    t = (w[a + O_EQW] >> shift) + base - stat(w, a, O_AGI, O_BONUS_AGI)
    t = t if t > 0 else 1
    s = status3(w, a)
    if s & 0x08: t = max(1, t >> 1)
    if s & 0x04: t = min(255, t << 1)
    return t


def delay_ticks(w, a, cmd):
    d = DELAY.get(cmd, 0); s = status3(w, a)
    if s & 0x08: d = max(1, d >> 1)
    if s & 0x04: d = min(255, d << 1)
    return d


def tempo(w, a, cmd, base=ATB_BASE, shift=ATB_SHIFT):
    """Share of a plain turn's rate that a command with a delay keeps: cycle / (cycle + delay)."""
    c = cycle(w, a, base, shift)
    return c / (c + delay_ticks(w, a, cmd))


def weapon_record(sim, me, hand):
    """12-byte weapon record in the hand (0 right, 1 left) from the gear struct the battle uses; None when the hand holds no weapon
    (a bare-handed character needs the 'Empty' fists, item 1, in the field struct: with 0 in both hands the game does not attack at all)."""
    w = sim.w
    if not w[me.b + (O_RHW, O_LHW)[hand]]: return None
    o = GEAR + GEAR_SIZE * me.i + (GEAR_RH, GEAR_LH)[hand]
    return bytes(w[o:o + REC])


class Strike:
    """One weapon (or command) hit, built the way the Attack procedures of the battle program build it."""
    def __init__(self, kind, power, p1=0, p2=0, p3=0, category=0x80, dm=0, jumping=False):
        self.kind, self.power, self.p1, self.p2, self.p3, self.category, self.dm, self.jumping = kind, power, p1, p2, p3, category, dm, jumping

    @classmethod
    def weapon(cls, rec, dm=0, jumping=False):
        return cls(rec[8] & 0x7F, rec[7], rec[9], rec[10], rec[11], rec[1], dm, jumping)

    def range(self, w, a, t, crit=False):
        """-> (lowest, highest, hit probability, absorbed) of one strike by CharStruct `a` on CharStruct `t`: the damage when it connects
        (lowest / highest roll; `crit`: as a critical hit, which doubles Attack and ignores Defense) and the chance that it does."""
        k, lvl, st = self.kind, stat(w, a, O_LEVEL, O_BONUS_LEVEL), stat(w, a, O_STR, O_BONUS_STR)
        ag, mg = stat(w, a, O_AGI, O_BONUS_AGI), stat(w, a, O_MAG, O_BONUS_MAG)
        P = self.power
        magic = k in (BELL, ROD)
        if k == FIST:                                          # Brawl (Barehand): + 2 * Level, M = Level * Str / 256 + 2; else M = 2
            brawl = w[a + O_PASS2] & 0x40
            a0 = P + (lvl * 2 + (50 if w[a + O_ARMORPROP] & 0x20 else 0) if brawl else 0); a1 = a0 + (lvl >> 2)
            M = ((lvl * st) >> 8) + 2 if brawl else 2
        elif k in (KNIFE, BOW_STATUS, BOW_ELEMENT, WHIP, BOW_CREATURE, CHICKEN):
            a0, a1 = P, P + 3
            M = ((st * lvl) >> 7) + 2 + (((ag * lvl) & 0xFF) >> 7)     # the agility half only uses the low byte of the product (game bug)
        elif k in (AXE, LONG_AXE, RUNE):
            a0, a1 = P >> 1, (P >> 1) + P
            M = ((lvl * st) >> 7) + 2
        elif k == BELL:
            a0, a1 = P >> 1, (P >> 1) + (P >> 1)
            M = ((ag * lvl) >> 7) + 2 + ((mg * lvl) >> 7)
        elif k == ROD:
            a0, a1 = 0, P * 2
            M = ((lvl * mg) >> 8) + 2
        elif k == THROW:
            a0, a1 = P, P + (P >> 3)
            M = ((lvl * st) >> 7) + ((lvl * ag) >> 7) + 2
        else:                                                  # swords, katanas, spears, brave blade: SwordDamage
            a0, a1 = P, P + (P >> 3)
            M = ((lvl * st) >> 7) + 2
        defense = w[t + (O_MDEF if magic else O_DEF)]
        if k in (AXE, LONG_AXE, RUNE): defense >>= 2
        airborne = self.jumping or w[a + O_CMDST] & 0x10
        if k in (SPEAR, SPEAR_CREATURE) and airborne: M <<= 1                     # CheckJump
        elif (k in BACK_ROW or k in (SPEAR, SPEAR_CREATURE)) and not airborne:     # BackRowMod (skipped while jumping)
            if w[a + O_ROW] & 0x80: M >>= 1
            if w[t + O_ROW] & 0x80: M >>= 1
        dm = self.dm                                           # CommandMod
        if dm & 0x40: a0 <<= 1; a1 <<= 1
        if dm & 0x20: a0 >>= 1; a1 >>= 1
        if dm & 0x10: M <<= 1
        if dm & 0x08: M >>= 1
        if dm & 0x04: defense = 0
        if dm & 0x01 and w[t + O_CREATURE] & HUMAN: a0 <<= 1; a1 <<= 1
        if w[t + O_CMDST] & 0x80: M >>= 1                      # target defending
        if w[t + O_CMDST] & 0x40: a0 = a1 = 0                  # target guarding
        if k in DOUBLE_GRIP and w[a + O_PASS2] & 0x20 and not (w[a + O_RHSH] or w[a + O_LHSH]) and not (w[a + O_RHW] and w[a + O_LHW]): M <<= 1
        if magic:
            if status3(w, t) & 0x20: M >>= 1
            if status1(w, t) & 0x20: defense = 0
        else:
            if status1(w, t) & 0x30: defense = 0
            if status3(w, t) & 0x40: M >>= 1
        if status1(w, a) & 0x30: a0 = a1 = 3
        if status2(w, a) & 0x08: a0 = a0 * 3 >> 1; a1 = a1 * 3 >> 1
        el = self.p1 if k in (SWORD, KNIFE, SPEAR) else self.p3 if k in (BOW_ELEMENT, ROD) else 0
        absorbed = False
        if el:                                                 # ElementDamageModPhys
            if w[t + O_ABSORB] & el: absorbed = True
            elif w[t + O_IMMUNE] & el: return 0, 0, 0.0, False
            elif w[t + O_HALF] & el: a0 >>= 1; a1 >>= 1
            elif w[t + O_WEAK] & el: a0 <<= 1; a1 <<= 1; defense = 0
        if crit: a0 <<= 1; a1 <<= 1; defense = 0
        M = max(M, 1)
        lo, hi = min(9999, max(0, a0 - defense) * M), min(9999, max(0, a1 - defense) * M)
        if absorbed: return 0, 0, 1.0, True
        # hit chance (HitPhysical / CheckPHit): the Hit% roll, then the evade roll unless the target cannot evade
        if self.dm & 0x80 or k == BELL: return lo, hi, 1.0, False
        hit = self.p1 / 100 if k in OWN_HIT else 1.0
        evade = w[t + O_MEVADE] if k == ROD else w[t + O_EVADE] >> 1 if k in HALF_EVADE else w[t + O_EVADE]
        if status1(w, t) & 0x20: evade = 0
        if status1(w, t) & 0x10: evade = min(99, evade * 2)
        if status2(w, t) & 0x70 or status3(w, t) & 0x10 or self.category & w[t + O_CANTEVADE]: evade = 0
        return lo, hi, hit * (1 - evade / 100), False


def strikes(sim, me, dm=0, jumping=False, kick=False):
    """The strikes of a Fight by `me` (one per hand holding a weapon).  `kick`: the Monk's Kick = fist formula without weapon power."""
    if kick: return [Strike(FIST, 0, category=0)]
    out = []
    for hand in (0, 1):
        rec = weapon_record(sim, me, hand)
        if rec: out.append(Strike.weapon(rec, dm, jumping))
    return out


def phys_range(sim, me, m, dm=0, jumping=False, kick=False, crit=False):
    """(lowest, highest) damage of a Fight by `me` on `m` when every strike connects (`crit`: every strike a critical hit)."""
    lo = hi = 0
    for s in strikes(sim, me, dm, jumping, kick):
        l, h, _, _ = s.range(sim.w, me.b, m.b, crit)
        lo += l; hi += h
    return lo, hi


def phys_low(sim, me, m, dm=0, jumping=False, kick=False):
    """Lowest damage of a plain Fight by `me` on monster `m` (both hands, every strike connecting, lowest roll, no critical)."""
    return phys_range(sim, me, m, dm, jumping, kick)[0]


def phys_expected(sim, me, m, dm=0, jumping=False, kick=False):
    """Lowest-roll damage weighted by each strike's hit chance: what the veteran counts on from the formulas."""
    return sum(l * p for l, _, p, _ in (s.range(sim.w, me.b, m.b) for s in strikes(sim, me, dm, jumping, kick)))


def absorbs(sim, me, m):
    """True when a hand's weapon element is absorbed by `m` (the hit would heal it)."""
    return any(s.range(sim.w, me.b, m.b)[3] for s in strikes(sim, me))


def best_fight(sim, me, mons, **kw):
    """(value, monster) of the target where such a Fight does the most (damage capped at the target's HP: overkill is wasted)."""
    best = (0.0, None)
    for m in mons:
        v = min(m.hp, phys_expected(sim, me, m, **kw))
        if best[1] is None or v > best[0]: best = (v, m)
    return best


def magic_low(w, a, t, power, element=0, multi=False):
    """Lowest damage of a magic attack of `power` (Attack06 / NormalMagicDamage: M = Level * Magic / 256 + 4, Defense = MDefense) by `a` on
    `t` (`multi`: one of several targets, which halves the power); None when the target absorbs / is immune to the element."""
    lvl, mg = stat(w, a, O_LEVEL, O_BONUS_LEVEL), stat(w, a, O_MAG, O_BONUS_MAG)
    M = ((lvl * mg) >> 8) + 4; defense = w[t + O_MDEF]
    if multi: power >>= 1
    if status3(w, t) & 0x20: M >>= 1
    if status1(w, t) & 0x20: defense = 0
    if element:
        if w[t + O_ABSORB] & element or w[t + O_BLOCK] & element or w[t + O_IMMUNE] & element: return None
        if w[t + O_HALF] & element: power >>= 1
        elif w[t + O_WEAK] & element: power <<= 1; defense = 0
    return min(9999, max(0, power - defense) * max(M, 1))


# ---- monster knowledge ---------------------------------------------------------------------------------------------------------------
def monster_id(sim, m):
    k = m.i - 4
    return sim.w[BATTLE_MONSTER_ID + 2 * k] | (sim.w[BATTLE_MONSTER_ID + 2 * k + 1] << 8)


def counter_unsafe(policy, sim, me, m, cmd, element=0, category=0x80):
    """True when `m`'s AI has a counter (a script entry reacting to this command) whose strongest attack could kill `me` or take over 60% of
    its max HP: the test of VeteranPolicy._counter_risk for any command (the reaction sees the command table index = id - 1)."""
    B = getattr(policy, "B", None)
    if B is None: return False
    from knowledge import damage_range
    mid = monster_id(sim, m); w = bytes(sim.w); idx = cmd - 1
    for e in B.counters(mid):
        lim = e.hp_limit
        if lim and not m.hp < lim[1]: continue
        if not reacts(e, idx, element, category): continue
        worst = 0
        for a in e.attacks:
            r = damage_range(w, policy.d.monster(mid), m.i, me.i, B.attack(a), crit=True)
            if r: worst = max(worst, r[1])
        if worst >= me.hp or worst > 0.6 * me.max_hp: return True
    return False


def reacts(entry, idx, element, category):
    """Would a command with table index `idx` (element / category of its attack) set off this reactive script entry?  Conditions 6 / 7 follow
    AICondition06 / 07; spell, item and target reactions are not set off by these commands."""
    for t, p1, p2, p3 in entry.conds:
        if t in (6, 7):
            cmd_ok = True if p2 == 7 else (p2 == idx) != bool(p1)
            val = element if t == 6 else category
            if p2 == 7 and p1: ok = not (p3 & val)
            elif p3 == 0: ok = True
            else: ok = bool(p3 & val) if not p1 or p2 == 7 else True
            if not (cmd_ok and ok): return False
        elif t in (8, 9):
            if not p1: return False                      # 'when this spell / item' is not set off by a command
        elif t in (0xA, 0x11): return False
    return True


def punishes_jumpers(policy, sim, mons):
    """True when a living monster's script has a target choice 'a character that is jumping' (AI targets 0x28..0x2C: Machine Head's
    Interceptor Rocket)."""
    B = getattr(policy, "B", None)
    if B is None: return False
    return any(s[0] == 0xF3 and 0x28 <= s[1] <= 0x2C for m in mons for e in B.script(monster_id(sim, m)) for s in e.specials)


def snapshot(sim):
    """{item id: quantity} of the battle inventory."""
    out = {}
    for slot, iid in sim.inventory_slots.items(): out[iid] = out.get(iid, 0) + sim.w[INV_QTY + slot]
    return out


def slot_mask(*mons):
    m = 0
    for x in mons: m |= 0x80 >> (x.i - 4)
    return m


def threat_hi(policy, me):
    """Strongest single hit the live monsters' scripts can deal to `me` (VeteranPolicy.threat); 0 when the policy has no scripts."""
    return getattr(policy, "threat", {}).get(me.i, (0, 0))[1]


def phys_threat(policy, sim, me, mons):
    """Strongest physical hit (monster Fight / specialty) the scripts of the living monsters can deal to `me`; 0 when unknown."""
    if not hasattr(policy, "_moves"): return 0
    from knowledge import damage_range
    w = bytes(sim.w); hi = 0
    for m in mons:
        mv = policy._moves(sim, m, mons)
        if not mv: continue
        attacks, mid = mv
        for a in attacks:
            A = policy.B.attack(a)
            if A["formula"] in (1, 2):
                r = damage_range(w, policy.d.monster(mid), m.i, me.i, A, crit=True)
                if r: hi = max(hi, r[1])
    return hi


class PhysCommand(JobCommand):
    """Common helpers of the physical commands."""
    cid = 0
    element, category = 0, 0x80
    delayed = False                         # waits long enough that the actor must be able to take a hit meanwhile

    def fv(self, sim, me, mons): return self.p.fight_value(sim, me, mons)

    def tempo(self, sim, me): return tempo(sim.w, me.b, self.cid, *self.p.atb)

    def safe_wait(self, sim, me, mons):
        """A delayed command leaves the actor waiting longer than a plain turn: refuse when one hit the scripts allow could kill it."""
        return not self.delayed or threat_hi(self.p, me) < me.hp

    def remaining(self, sim, me, m, ticks):
        """HP monster `m` is expected to still have `ticks` from now: the partners' plain Fights (lowest rolls, spells left out) at their own
        pace come first, so a delayed command is not wound up against a monster that is about to fall."""
        gone = 0.0
        for c in sim.party:
            if c.i != me.i and not c.dead and c.max_hp:
                gone += phys_expected(sim, c, m) * ticks / cycle(sim.w, c.b, *self.p.atb)
        return max(0.0, m.hp - gone)

    def ok(self, sim, me, m):
        return not counter_unsafe(self.p, sim, me, m, self.cid, self.element, self.category)

    def target(self, sim, me, mons, ok=None):
        """The monster the policy would focus (policy.priority) among those the command may be used on."""
        pool = [m for m in mons if self.ok(sim, me, m) and (ok is None or ok(m))]
        banned = getattr(self.p, "banned", ())
        pool = [m for m in pool if (m.i, "fight") not in banned] or pool
        return max(pool, key=self.p.priority) if pool else None

    def command(self, sim, me, mons, m=None, **kw):
        """The Command as the real menu would leave it (single target: the monster's bit in MonsterTargets)."""
        return Command(self.cid, 0x80, slot_mask(m or mons[0]))


@register
class FightCore(JobCommand):
    """Not a command: installs `policy._fight_value` (the baseline every offense command is compared with), `policy.phys_low` and the ATB
    constants of this ROM (policy.atb)."""
    name = ""

    def __init__(self, policy):
        super().__init__(policy)
        policy._fight_value = self.fight_value
        policy.phys_low = phys_low
        policy.atb = atb_constants(policy.d.r)

    def fight_value(self, sim, me, mons):
        """Expected damage of a plain Fight by `me` on its best target (lowest-roll damage x hit chance, capped by the target's HP); 0 when
        the hands hold no weapon (the game then does not attack at all)."""
        mons = [m for m in mons if not absorbs(sim, me, m)] or mons
        return best_fight(sim, me, mons)[0] if mons else 0.0


# ---- Thief ------------------------------------------------------------------------------------------------------------------------------
class Stealer(PhysCommand):
    """Steal: 40% (80% with Improved Steal) to take the monster's common item (96%) or rare item (4%); once per monster, never from a monster
    with nothing in its common slot.  It costs a turn without damage, so only in easy fights (no scripted hit anywhere near lethal), against
    monsters that outlive two of the thief's Fights, for an item worth having (gear, or a consumable the bag is short of), and at most
    `tries` times per monster."""
    bonus, tries = 1.0, 4

    def __init__(self, policy):
        super().__init__(policy)
        self.failed = {}

    def stealable(self, sim, m):
        return not sim.w[m.b + O_STOLEN] and self.d.r[MONSTER_ITEM + 4 * monster_id(sim, m) + 1] != 0

    def worth(self, sim, m):
        iid = self.d.r[MONSTER_ITEM + 4 * monster_id(sim, m) + 1]
        return iid < 0xE0 or snapshot(sim).get(iid, 0) < 5

    def safe(self, sim, me, mons):
        party = [c for c in sim.party if not c.dead and c.max_hp]
        return not self.p.hard and all(threat_hi(self.p, c) * 2 < c.hp for c in party)

    def propose(self, sim, char, me, mons):
        if not self.safe(sim, me, mons): return None
        m = self.target(sim, me, mons, ok=lambda m: self.stealable(sim, m) and self.worth(sim, m) and m.hp > 2 * phys_low(sim, me, m)
                        and self.failed.get(m.i, 0) < self.tries)
        if m is None: return None
        cmd = self.command(sim, me, mons, m)
        self.failed[m.i] = self.failed.get(m.i, 0) + 1          # cleared by the success itself (the monster is no longer stealable)
        return self.fv(sim, me, mons) + self.bonus, cmd


@register
class Steal(Stealer):
    name, cid = "Steal", CMD_STEAL


@register
class Mug(Stealer):
    """Mug: a Fight (both hands) followed by a Steal on the same monster: never worse than Fight against a monster with an item, in any
    kind of fight."""
    name, cid, bonus = "Mug", CMD_MUG, 2.0

    def safe(self, sim, me, mons): return True

    def propose(self, sim, char, me, mons):
        fv, best = best_fight(sim, me, [m for m in mons if not absorbs(sim, me, m)] or mons)
        cands = [m for m in mons if self.stealable(sim, m) and self.ok(sim, me, m) and not absorbs(sim, me, m)]
        if not cands: return None
        m = max(cands, key=lambda m: min(m.hp, phys_expected(sim, me, m)))
        if min(m.hp, phys_expected(sim, me, m)) < 0.8 * fv: return None         # the item is on a monster the Fight would waste
        return fv + self.bonus, self.command(sim, me, mons, m)


# ---- Dragoon ----------------------------------------------------------------------------------------------------------------------------
@register
class Jump(PhysCommand):
    """Jump: the dragoon leaves the battle (can not be targeted) until it lands 10 + 80 ticks later with its weapon, auto hit; spears double
    M and ignore the rows, other weapons gain only the auto hit.  Worth it when the landing, spread over the longer turn, beats a Fight, or
    when staying out of reach matters (a hard fight where a hit could take half of the dragoon's HP)."""
    name, cid = "Jump", CMD_JUMP

    def value(self, sim, me, m):
        return min(self.remaining(sim, me, m, delay_ticks(sim.w, me.b, self.cid)), phys_expected(sim, me, m, dm=0x80, jumping=True)) * self.tempo(sim, me)

    def propose(self, sim, char, me, mons):
        if punishes_jumpers(self.p, sim, mons): return None
        pool = [m for m in mons if self.ok(sim, me, m) and not absorbs(sim, me, m)]
        if not pool: return None
        m = max(pool, key=lambda m: (self.value(sim, me, m), self.p.priority(m)))
        v = self.value(sim, me, m)
        if self.p.hard and threat_hi(self.p, me) * 2 >= me.hp: v *= 1.25            # airborne it takes no hit meanwhile
        return v, self.command(sim, me, mons, m)


@register
class Lance(PhysCommand):
    """Lance (Dragon Sword): a drain of Magic power 45 (auto hit; the HP goes to the dragoon, the MP drain of power 4 follows); magic-based,
    so it beats a Fight only for a dragoon with real Magic against a high-Defense target.  Undead take no drain (it would hurt the user)."""
    name, cid, category = "Lance", CMD_LANCE, 0

    def value(self, sim, me, m):
        if sim.w[m.b + O_CREATURE] & UNDEAD or sim.w[m.b + O_ARMORPROP] & 0x02: return 0.0
        d = magic_low(sim.w, me.b, m.b, 45)
        if d is None: return 0.0
        deficit = me.max_hp - me.hp
        return min(m.hp, d) + 0.5 * min(deficit, d)             # the drained HP heals the user: half a credit for what it fills in

    def propose(self, sim, char, me, mons):
        cands = [m for m in mons if self.ok(sim, me, m)]
        if not cands: return None
        m = max(cands, key=self.value_key(sim, me))
        return self.value(sim, me, m), self.command(sim, me, mons, m)

    def value_key(self, sim, me): return lambda m: (self.value(sim, me, m), self.p.priority(m))


# ---- Ninja ------------------------------------------------------------------------------------------------------------------------------
@register
class Throw(PhysCommand):
    """Throw: a weapon from the bag (any weapon without the 'not throwable' bit, 90 Shuriken / Fuma / Soot are throw-only) hits for
    (2 * (power + 0..power/8) - Defense) * (Level * Str / 128 + Level * Agi / 128 + 2), auto hit, no row penalty, and is used up; the
    elemental scrolls (Flame / Water / Thunder, consumables with magic power 120) burn every enemy.  Only spare gear is thrown: a weapon
    nobody in the party could wield better than what it holds is a spare; real weapons must double a Fight to be worth the loss."""
    name, cid = "Throw", CMD_THROW

    def spare(self, sim, iid):
        d = self.d; w = sim.w
        rec = d.item_prop(iid)
        for c in sim.party:
            if c.dead: continue
            wm, am = w[c.b + 0x40] | w[c.b + 0x41] << 8, w[c.b + 0x42] | w[c.b + 0x43] << 8
            if not d.item_equippable(iid, wm, am): continue
            cur = max([(d.item_prop(w[c.b + o]) or bytes(12))[7] for o in (O_RHW, O_LHW) if w[c.b + o] > 1] or [0])
            if rec[7] > cur: return False
        return True

    def options(self, sim, me, mons):
        """[(value, inventory slot, monster or None)] for every throwable thing in the bag worth considering."""
        w = sim.w; d = self.d; out = []
        for slot, iid in sim.inventory_slots.items():
            if iid in (0, 1): continue
            if iid < 0x80:
                rec = d.item_prop(iid)
                if rec is None or rec[2] & 0x40 or not rec[7] or not d.item_valid(iid) or not self.spare(sim, iid): continue
                st = Strike(THROW, rec[7], dm=0xC0)
                best = max(((min(m.hp, st.range(w, me.b, m.b)[0]), m) for m in mons if self.ok(sim, me, m)), default=None, key=lambda x: (x[0], self.p.priority(x[1])))
                if best is None: continue
                need = 1.0 if d.item_kind(iid) is None else 2.0                  # throw-only items cost nothing, a real weapon must be worth it
                if best[0] > 0: out.append((best[0] / need, slot, best[1]))
            elif iid >= 0xE0:
                row = bytes(d.r[CONSUMABLE_PROP + 8 * (iid - 0xE0):CONSUMABLE_PROP + 8 * (iid - 0xE0) + 8])
                if row[4] != 0x06 or row[0] != 0x48: continue                       # magic attack on all enemies (the elemental scrolls)
                dmg = [magic_low(w, me.b, m.b, row[6], row[7]) for m in mons]            # an item's power is not halved by many targets
                if any(x is None for x in dmg): continue                    # one of them absorbs it
                out.append((sum(min(m.hp, x) for m, x in zip(mons, dmg)), slot, None))
        return out

    def command(self, sim, me, mons, m=None, slot=None, **kw):
        return Command(CMD_THROW, 0x80, slot_mask(*mons) if m is None else slot_mask(m), 0, slot)

    def propose(self, sim, char, me, mons):
        opts = self.options(sim, me, mons)
        if not opts: return None
        v, slot, m = max(opts, key=lambda o: o[0])
        return v, self.command(sim, me, mons, m, slot)


# ---- Ranger -----------------------------------------------------------------------------------------------------------------------------
@register
class Aim(PhysCommand):
    """Aim: a Fight that always hits (DamageMod 0x80), 5 ticks later.  Better than Fight when the weapon's Hit% or the target's Evade lose
    more than the 5 ticks of delay: bows with Hit% under 100, evasive monsters."""
    name, cid = "Aim", CMD_AIM

    def propose(self, sim, char, me, mons):
        pool = [m for m in mons if self.ok(sim, me, m) and not absorbs(sim, me, m)]
        if not pool: return None
        t = self.tempo(sim, me)
        m = max(pool, key=lambda m: (min(m.hp, phys_expected(sim, me, m, dm=0x80)), self.p.priority(m)))
        return min(m.hp, phys_expected(sim, me, m, dm=0x80)) * t, self.command(sim, me, mons, m)


def animal(roll):
    """Animals' pick for a roll 0..Level: 0 rabbit, 1 squirrel (<5), 2 bees (<10), 3 nightingale (<20), 4 momonga (<30), 5 falcon (<40),
    6 skunk (<50), 7 boar (<60), 8 unicorn."""
    if roll == 0: return 0
    return next((a for a, lim in enumerate((5, 10, 20, 30, 40, 50, 60), 1) if roll < lim), 8)


@register
class Animals(PhysCommand):
    """Animals: rolls 0..Level and calls an animal: 0 rabbit (nothing), <5 squirrel (one random enemy slot, ground magic 45), <10 bee swarm (all,
    10..100 x (Level / 8 + 2)), <20 nightingale (heals the party, power 60), <30 momonga (sleep), <40 falcon (one random slot: 12/16 of its
    HP), <50 skunk (poison / blind), <60 boar (one random slot, ground magic 180), else unicorn (full restore).  No MP is spent; the value is the
    average over the roll's distribution of each animal's lowest result, which stays under a Fight until the dice favour the strong animals."""
    name, cid = "Animals", CMD_ANIMALS

    def value(self, sim, me, mons):
        w = sim.w; lvl = stat(w, me.b, O_LEVEL, O_BONUS_LEVEL); mg = stat(w, me.b, O_MAG, O_BONUS_MAG)
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
        grounded = [m for m in mons if not status1(w, m.b) & 0x08]
        alive = len(mons) / 8                                   # a single-target animal aims at one of the 8 monster slots at random
        party = [c for c in sim.party if not c.dead and c.max_hp]
        heal = ((lvl * mg) >> 8) + 4
        v = 0.0
        for roll in range(lvl + 1):
            a = animal(roll)
            if a == 1: x = alive * mean([min(m.hp, magic_low(w, me.b, m.b, 45) or 0) for m in grounded])
            elif a == 7: x = alive * mean([min(m.hp, magic_low(w, me.b, m.b, 180) or 0) for m in grounded])
            elif a == 2: x = sum(min(m.hp, max(0, 10 - w[m.b + O_MDEF]) * ((lvl >> 3) + 2)) for m in mons)
            elif a == 3: x = sum(min(c.max_hp - c.hp, max(0, 60 - w[c.b + O_MDEF]) * heal) for c in party)
            elif a == 5: x = alive * mean([0.75 * m.hp for m in mons if not w[m.b + O_CREATURE] & HEAVY])
            elif a == 8: x = sum(c.max_hp - c.hp for c in party)
            else: x = 0.0
            v += x / (lvl + 1)
        return v

    def propose(self, sim, char, me, mons):
        return self.value(sim, me, mons), Command(CMD_ANIMALS, 0x80)


# ---- Samurai ----------------------------------------------------------------------------------------------------------------------------
@register
class Slash(PhysCommand):
    """Slash: death to every enemy that is not Heavy (and not a zombie) with Hit% = 85 + own Level - monster Level (1..99), then the
    monster's Magic Evade; a miss is a miss for that monster only.  The 60-tick delay is paid in the tempo.  Its value is the HP it is
    expected to remove, so it wins against everything that is not a boss flagged Heavy."""
    name, cid, delayed, category = "Slash", CMD_SLASH, True, 0

    def chance(self, sim, me, m):
        w = sim.w
        if w[m.b + O_CREATURE] & HEAVY or status1(w, m.b) & 0x02: return 0.0
        hit = min(99, max(1, 85 + stat(w, me.b, O_LEVEL, O_BONUS_LEVEL) - stat(w, m.b, O_LEVEL, O_BONUS_LEVEL)))
        return hit / 100 * (1 - w[m.b + O_MEVADE] / 100)

    def propose(self, sim, char, me, mons):
        if not self.safe_wait(sim, me, mons): return None
        targets = [m for m in mons if self.ok(sim, me, m)]
        if len(targets) < len(mons): return None            # a counter on one of them: hit them one by one instead
        d = delay_ticks(sim.w, me.b, self.cid)
        v = sum(self.chance(sim, me, m) * self.remaining(sim, me, m, d) for m in mons) * self.tempo(sim, me)
        return v, Command(CMD_SLASH, 0x80, slot_mask(*mons))


@register
class SwdSlap(PhysCommand):
    """SwdSlap: the game runs a plain Fight (the 'sword slap' flag is never read), so it is worth exactly a Fight: never preferred."""
    name, cid = "SwdSlap", CMD_SWDSLAP

    def propose(self, sim, char, me, mons):
        m = self.target(sim, me, mons, ok=lambda m: not absorbs(sim, me, m))
        return (best_fight(sim, me, [m])[0], self.command(sim, me, mons, m)) if m else None


@register
class GilToss(PhysCommand):
    """Gil Toss (forbidden by default): every enemy takes (Level + 10 - Defense) * 150 and costs Level * 50 gil (with too little gil the toss
    does nothing and costs nothing).  Valued as the HP removed minus `gil_value` damage per gil; the gil kept in reserve is `policy.gil_reserve`."""
    name, cid, category = "Gil Toss", CMD_GILTOSS, 0
    gil_value = 0.1

    def propose(self, sim, char, me, mons):
        w = sim.w; lvl = stat(w, me.b, O_LEVEL, O_BONUS_LEVEL)
        gil = w[GIL] | w[GIL + 1] << 8 | w[GIL + 2] << 16
        cost = 50 * lvl
        targets = [m for m in mons if self.ok(sim, me, m)]
        if len(targets) < len(mons): return None
        keep = getattr(self.p, "gil_reserve", 0)
        affordable = (gil - keep) // cost if cost else len(mons)
        if affordable < 1: return None
        dmg = sorted((min(m.hp, min(9999, max(0, lvl + 10 - w[m.b + O_DEF]) * 150)) for m in mons), reverse=True)[:affordable]
        n = len(mons)
        if n > affordable: return None                      # the later targets would take 0: not worth the gil
        return sum(dmg) - self.gil_value * cost * n, Command(CMD_GILTOSS, 0x80, slot_mask(*mons))


# ---- Monk -------------------------------------------------------------------------------------------------------------------------------
@register
class Kick(PhysCommand):
    """Kick: one fist strike (Brawl formula with no weapon power) on every enemy, so it beats a fist Fight (two strikes) from three
    enemies on; without Barehand (Brawl) it is nearly harmless."""
    name, cid, category = "Kick", CMD_KICK, 0

    def propose(self, sim, char, me, mons):
        targets = [m for m in mons if self.ok(sim, me, m)]
        if len(targets) < len(mons): return None
        v = sum(min(m.hp, phys_expected(sim, me, m, kick=True)) for m in mons)
        return v, Command(CMD_KICK, 0x80, slot_mask(*mons))


@register
class Focus(PhysCommand):
    """Focus (BuildUp): a Fight 60 ticks later with M x2.  Twice the damage for 60 more ticks of a ~200-tick turn: better than Fight whenever the
    actor can wait (no scripted hit that could kill it meanwhile)."""
    name, cid, delayed = "Focus", CMD_FOCUS, True

    def propose(self, sim, char, me, mons):
        if not self.safe_wait(sim, me, mons): return None
        pool = [m for m in mons if self.ok(sim, me, m) and not absorbs(sim, me, m)]
        if not pool: return None
        d = delay_ticks(sim.w, me.b, self.cid)
        value = lambda m: min(self.remaining(sim, me, m, d), phys_expected(sim, me, m, dm=0x10))
        m = max(pool, key=lambda m: (value(m), self.p.priority(m)))
        return value(m) * self.tempo(sim, me), self.command(sim, me, mons, m)


@register
class Chakra(PhysCommand):
    """Chakra: heals the user by (30 + 0..3 - MDefense) * (Level * Magic / 256 + 4) and cures Stone / Toad / Mini / Zombie.  Used to cure
    Toad / Mini / Zombie (they cripple a fighter) and as a last self-heal when the user is under 40% HP and the heal is a real share of it."""
    name, cid, kind = "Chakra", CMD_CHAKRA, "support"

    def heal(self, sim, me):
        w = sim.w; lvl, mg = stat(w, me.b, O_LEVEL, O_BONUS_LEVEL), stat(w, me.b, O_MAG, O_BONUS_MAG)
        return max(0, 30 - w[me.b + O_MDEF]) * (((lvl * mg) >> 8) + 4)

    def propose(self, sim, char, me, mons):
        cmd = Command(CMD_CHAKRA, 0x80, 0, Command.party_mask(me.i))
        if status1(sim.w, me.b) & 0x32: return 500.0, cmd
        h = self.heal(sim, me)
        if me.hp < 0.4 * me.max_hp and h >= 0.15 * me.max_hp: return min(h, me.max_hp - me.hp), cmd
        return None


@register
class Guard(PhysCommand):
    """Guard: until the next turn every physical hit on the user does 0 (the Guarding bit of CmdStatus makes the hit's Attack 0) and the user
    does nothing.  Taken when a physical hit the scripts allow could kill the user and nothing healed it first, but never twice in a row (a
    guard that is never lifted only stalls the fight)."""
    name, cid, kind = "Guard", CMD_GUARD, "support"

    def __init__(self, policy):
        super().__init__(policy)
        self.last = set()

    def propose(self, sim, char, me, mons):
        if me.i in self.last:
            self.last.discard(me.i)
            return None
        hi = phys_threat(self.p, sim, me, mons)
        if hi and hi >= me.hp:
            self.last.add(me.i)
            return 100.0, Command.guard()
        return None
