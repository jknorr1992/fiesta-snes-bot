"""Veteran's knowledge of monsters: AI scripts and damage ranges.

The bot is assumed to act like a seasoned player who knows what each monster can do (its AI
script: which attacks, under which conditions) and the *range* its damage formulas can produce
(minimum and maximum), but not the exact value of the next roll.  Nothing here reads the RNG
state or peeks at the monster's pending command, so the same knowledge works in a live game.

AI script format (see everything8215/ff5 include/battle/ai_script.inc): AIScriptPtr at
0x109C00 holds one 16-bit offset per monster id (relative to 0x100000).  A script is a list of
entries; an entry is up to 4 four-byte conditions, 0xFE, then actions until 0xFE/0xFF; a lone
0xFF ends the script.  Actions are attack ids (0x80 = the monster's own Fight), 0xFD aa bb cc =
random pick of three attacks, 0xFD 0xF2..0xFA = special commands.
"""
from dataclasses import dataclass, field

AI_PTR, AI_BASE = 0x109C00, 0x100000
ATTACK_PROP = 0x110B80          # 256 x 8 bytes (shared by spells and monster attacks)

COND_NAMES = {0: "always", 1: "status", 2: "hp_less", 3: "var", 4: "alone", 5: "monsters",
              6: "react_cmd_elem", 7: "react_cmd_cat", 8: "react_magic", 9: "react_item",
              0xA: "react_target", 0xB: "char_param", 0xC: "a2", 0xD: "event_flag",
              0xE: "react_damage", 0xF: "dying", 0x10: "one_party_alive", 0x11: "react_summon",
              0x12: "no_female"}
REACTIVE = {6, 7, 8, 9, 0xA, 0xE, 0x11}


@dataclass
class Entry:
    conds: list                       # [(type, p1, p2, p3)]
    attacks: list                     # attack ids this entry can queue (all random choices)
    specials: list = field(default_factory=list)   # (cmd, a, b) for 0xFD 0xF2.. commands
    queued: int = 0                   # actions per turn when this entry fires (random_choice = 1)

    @property
    def reactive(self): return any(c[0] in REACTIVE for c in self.conds)
    @property
    def hp_limit(self):
        """(target routine, hp) of the smallest hp_less condition, else None."""
        lim = [(c[1], c[2] | c[3] << 8) for c in self.conds if c[0] == 2]
        return min(lim, key=lambda t: t[1]) if lim else None


def decode_script(rom, monster_id):
    p = AI_BASE + (rom[AI_PTR + 2 * monster_id] | rom[AI_PTR + 2 * monster_id + 1] << 8)
    entries = []
    for _ in range(64):
        if rom[p] == 0xFF: break
        conds = []
        while rom[p] != 0xFE:
            conds.append(tuple(rom[p:p + 4])); p += 4
            if len(conds) > 8: return entries            # malformed
        p += 1
        attacks, specials, queued = [], [], 0
        while rom[p] not in (0xFE, 0xFF):
            b = rom[p]
            if b == 0xFD:
                c = rom[p + 1]
                if c < 0xF0:
                    attacks += [x for x in rom[p + 1:p + 4] if x < 0xFD]; queued += 1; p += 4
                elif c == 0xF7:
                    n = rom[p + 2]; specials.append((c, 0, 0)); p += 4 + n - 1 if n else 4
                else:
                    specials.append((c, rom[p + 2], rom[p + 3])); p += 4
                    if c == 0xF1 and False: pass
            else:
                attacks.append(b); queued += 1; p += 1
        p += 1
        entries.append(Entry(conds, attacks, specials, queued))
    return entries


class Bestiary:
    """Per-ROM knowledge base used by the tactical policy."""

    def __init__(self, data):
        self.d, self.r = data, data.r
        self._scripts = {}

    def script(self, monster_id):
        if monster_id not in self._scripts: self._scripts[monster_id] = decode_script(self.r, monster_id)
        return self._scripts[monster_id]

    def counters(self, monster_id):
        """Script entries that fire in reaction to something done to the monster and queue attacks."""
        return [e for e in self.script(monster_id) if e.reactive and e.attacks]

    def reaction_matches(self, entry, kind, element=0, spell=None, category=0, item=None, damaging=True):
        """Would our action (kind: 'fight' | 'magic' | 'item') trigger this reactive entry?  Non-reactive
        conditions are assumed to hold (the veteran does not know the AI's variables)."""
        cmd = {"fight": REACT_FIGHT, "magic": REACT_MAGIC, "item": REACT_ITEM}[kind]
        for t, p1, p2, p3 in entry.conds:
            if t == 6:
                if p2 == 7: cmd_ok = True
                else: cmd_ok = (p2 == cmd) != bool(p1)
                if p2 == 7 and p1: elem_ok = not (p3 & element)
                elif p3 == 0: elem_ok = True
                else: elem_ok = bool(p3 & element) if not p1 or p2 == 7 else True
                if not (cmd_ok and elem_ok): return False
            elif t == 7:
                if p2 == 7: cmd_ok = True
                else: cmd_ok = (p2 == cmd) != bool(p1)
                if p2 == 7 and p1: cat_ok = not (p3 & category)
                elif p3 == 0: cat_ok = True
                else: cat_ok = bool(p3 & category) if not p1 or p2 == 7 else True
                if not (cmd_ok and cat_ok): return False
            elif t == 8:
                hit = kind == "magic" and spell == p2
                if hit == bool(p1): return False
            elif t == 9:
                hit = kind == "item" and item == p2
                if hit == bool(p1): return False
            elif t == 0xE:
                if not damaging: return False
            elif t in (0xA, 0x11): return False              # single/multi-target and summon reactions: not modelled
        return True

    def attack(self, aid):
        b = ATTACK_PROP + 8 * aid
        return dict(id=aid, formula=self.r[b + 4], power=self.r[b + 6], element=self.r[b + 7],
                    target=self.r[b], category=self.r[b + 1], cost=self.r[b + 3], raw=bytes(self.r[b:b + 8]))

# ---- damage ranges (min/max of the game's own formulas, never the next roll) ----------------

CHAR = 0x2000
O_ROW, O_LEVEL, O_ST1, O_ST3, O_CMDST, O_DEF, O_MDEF = 0x00, 0x02, 0x1A, 0x1C, 0x1E, 0x2D, 0x2F
O_ABSORB, O_BLOCK, O_IMMUNE, O_HALF, O_WEAK = 0x30, 0x31, 0x32, 0x33, 0x34
O_MATK, O_MM, O_SPEC = 0x44, 0x62, 0x6E
PHYSICAL_TYPES = (1, 2)      # monster Fight / monster specialty
MAGIC_TYPES = (6,)           # plain spell damage
# command ids a monster's reaction conditions see for our actions
REACT_FIGHT, REACT_MAGIC, REACT_ITEM = 4, 0x2B, 1


def _spread(p):
    """Attack = P + (0..P/8)"""
    return p, p + p // 8


def damage_range(w, d_mon, mon_slot, tgt_slot, atk, multi=False, crit=False):
    """(lo, hi) damage of attack record `atk` (Bestiary.attack) from monster slot `mon_slot`
    on combatant `tgt_slot`, using live stat bytes from the WRAM image `w` (so equipment, row,
    Protect/Shell, Defend, elemental resistances and Level are all accounted for).
    lo is the damage when the hit lands with the lowest roll, hi with the highest.
    Returns None for attacks that do not deal plain damage (status, healing, special formulas)."""
    f = atk["formula"]
    mb, tb = CHAR + 0x80 * mon_slot, CHAR + 0x80 * tgt_slot
    if f in PHYSICAL_TYPES:
        a0, a1 = _spread(w[mb + O_MATK]); m = w[mb + O_MM] or 1
        spec = w[mb + O_SPEC] if f == 2 else 0
        if spec & 1: a0 += a0 >> 1; a1 += a1 >> 1                # specialty: 1.5x
        if w[mb + O_ROW] & 0x80: m >>= 1
        if w[tb + O_ROW] & 0x80: m >>= 1
        if w[tb + O_CMDST] & 0x80: m >>= 1                       # defending
        if w[tb + O_ST3] & 0x40: m >>= 1                         # Protect
        defense = 0 if (w[tb + O_ST1] & 0x30 or spec & 0x80) else w[tb + O_DEF]   # toad / mini / ignores defense
        m = max(m, 1)
        lo, hi = max(0, a0 - defense) * m, min(9999, max(0, a1 - defense) * m)
        return (lo, min(9999, hi * 2) if crit else hi)
    if f in MAGIC_TYPES:
        a0, a1 = _spread(atk["power"])
        mag = d_mon["magic"]; lvl = w[mb + O_LEVEL] or d_mon.get("level", 1)
        m = ((lvl * mag) >> 8) + 4
        if w[tb + O_ST3] & 0x20: m >>= 1                         # Shell
        defense = 0 if w[tb + O_ST1] & 0x20 else w[tb + O_MDEF]  # toad
        el = atk["element"]
        if el:
            if w[tb + O_ABSORB] & el or w[tb + O_BLOCK] & el or w[tb + O_IMMUNE] & el: return (0, 0)
            if w[tb + O_HALF] & el: a0 >>= 1; a1 >>= 1
            elif w[tb + O_WEAK] & el: a0 <<= 1; a1 <<= 1; defense = 0
        if multi: a0 >>= 1
        return (min(9999, max(0, a0 - defense) * m), min(9999, max(0, a1 - defense) * m))
    return None
