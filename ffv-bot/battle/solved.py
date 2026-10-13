"""Solved random fights (user idea, 2026-10-13): once a party has beaten an encounter flawlessly in a full batch, later instances of the same
encounter are distilled to the adjustments of the party's resources instead of simulating another batch.

A record holds, for the flawless runs of the batch:
  * who mattered, by role: ACTORS (members that issued a command: their job, exact gear, passives, level, stats, the commands and spells they
    used), TANKS (members that never acted but lost HP: level, max HP, gear), IDLE members (neither: anybody will do);
  * what the fight did to each member and the party: mean change of HP, MP, exp and ABP, the worst HP / MP loss (and the lowest HP seen at any
    turn), mean gil and mean item changes.

A later instance REUSES the record when its party can fill the recorded roles (members are matched to the roles in any order): an actor needs
the same job, passives and exact gear, no lower level, stats, max HP / MP, the used commands in its command slots and the used spells known; a tank
no lower level / max HP and the same gear; an idle member only has to be alive and free of ailments.  Nobody may run short (HP keeps a margin over
the worst recorded loss, MP, items), and the rewards must not cross a threshold (a level-up, or an ability / job-level ABP cost): a level-up
changes the party, so that fight is simulated afresh.  Gear is compared item by item, never as a score: a piece can matter for an element, a
status or a monster family (Bone Mail's resistances) that a power number does not show.  A different piece stands in only when its ROM property
record matches the recorded one byte for byte except the plain numbers (a weapon's attack power, armor's defense / evade / magic defense),
which may not be lower: same weapon class and damage formula, no special properties.

A record belongs to the battle code it was made with: after any change to the bot the fight is simulated once more before it is trusted again
(the new batch replaces the record).  A fight that does not pass deletes its record.
"""
import itertools, os, pickle
from field import MAGIC_BITS

HP_MARGIN = 0.0                # extra fraction of max HP over the worst recorded loss (user 2026-10-13: only a negative adjustment rules a record out)
SPELL_BYTES = 12
EXP_SLACK = 1                  # rewards must stay this far below a threshold
BASE = ("base_str", "base_agi", "base_vit", "base_mag")


def signature(f):
    """Per party slot: what decides how a fight goes (None for an empty slot)."""
    sig = []
    for i in range(4):
        ch = f.character(i)
        if not ch.in_party: sig.append(None); continue
        sig.append(dict(job=ch.job, commands=tuple(ch.commands), passives=(ch.passives1, ch.passives2, ch.element_up), level=ch.level,
                        max_hp=ch.max_hp, max_mp=ch.max_mp, stats=tuple(getattr(ch, k) for k in BASE),
                        equip=tuple(sorted(ch.equipment().items())), back=ch.back_row, status=(ch.status1, ch.status2, ch.status3, ch.status4),
                        hp=ch.hp, mp=ch.mp, exp=ch.exp, ap=ch.ap, job_level=ch.job_level))
    return sig


class Solved:
    def __init__(self, d, abilities, path=None, code_key=""):
        self.d = d; self.ab = abilities; self.path = path; self.code_key = code_key
        self.rec = {}                  # (enc, bg) -> record
        self.hits = self.invalid = 0; self.reasons = {}
        if path and os.path.exists(path):
            try: self.rec = pickle.load(open(path, "rb"))
            except Exception: self.rec = {}

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp%d" % os.getpid(); pickle.dump(self.rec, open(tmp, "wb")); os.replace(tmp, self.path)

    # ------------------------------------------------------------------ record
    def record(self, enc, bg, start, flawless, rate):
        """`start`: the Field before the fight; `flawless`: the batch results that count (each has the end WRAM, who acted, the lowest HP seen)."""
        from field import Field
        key = (enc, bg); s = signature(start); n = len(flawless)
        if n == 0: self.rec.pop(key, None); return
        ends = [Field.from_wram(r["wram"], self.d) for r in flawless]
        acts = {i: set() for i in range(4)}
        for r in flawless:
            for (c, cmd, sel) in r.get("acts", []): acts.setdefault(c, set()).add((cmd, sel))
        mem = {}
        for i, si in enumerate(s):
            if si is None: continue
            chs = [e.character(i) for e in ends]
            if any(c.hp <= 0 or (c.status1 | c.status2 | c.status3 | c.status4) for c in chs): self.rec.pop(key, None); return       # a dead member or an ailment: not a clean fight
            if any(c.level != si["level"] or c.job_level != si["job_level"] or tuple(c.commands) != si["commands"] for c in chs): self.rec.pop(key, None); return   # growth events: simulate them
            lows = [r["minhp"][i] for r in flawless if r.get("minhp") and r["minhp"][i] < (1 << 30)]
            low = min([min(c.hp for c in chs)] + lows)
            loss_hp = max(0, si["hp"] - low)
            role = "actor" if acts[i] else ("tank" if loss_hp > 0 else "idle")
            mem[i] = dict(role=role, dhp=sum(c.hp - si["hp"] for c in chs) / n, dmp=sum(c.mp - si["mp"] for c in chs) / n, dexp=sum(c.exp - si["exp"] for c in chs) / n,
                          dap=sum(c.ap - si["ap"] for c in chs) / n, loss_hp=loss_hp, loss_mp=max(0, max(si["mp"] - c.mp for c in chs)),
                          used=sorted(acts[i]), sig=si)
        bag0 = start.items(); items = {}
        for it in set(bag0) | {k for e in ends for k in e.items()}:
            dl = sum(e.items().get(it, 0) - bag0.get(it, 0) for e in ends) / n
            if abs(dl) > 1e-9: items[it] = dl
        spells = bytes(start.wram[MAGIC_BITS:MAGIC_BITS + SPELL_BYTES])
        self.rec[key] = dict(code=self.code_key, mem=mem, items=items, spells=spells, rate=rate, n=n,
                             gil=sum(e.gil - start.gil for e in ends) / n, frames=0)
        self.save()

    # ------------------------------------------------------------------ reuse
    # ------------------------------------------------------------------ gear
    def plain_upgrade(self, a, b):
        """Can item `b` stand in for the recorded item `a` in the same slot?  Only when it is the same item, or differs from it in its plain
        numbers alone, never lower: the ROM property records must match byte for byte (equip class and with it the weapon's damage formula, element,
        status, spell, stat bonus ...) except the attack power of a weapon, and the defense / evade / magic defense of armor (its graphic byte is ignored).
        A piece with any special property (Bone Mail ...) therefore only stands in for itself."""
        if a == b: return True
        pa, pb = self.d.item_prop(a), self.d.item_prop(b)
        if pa is None or pb is None: return False
        ka, kb = self.d.item_kind(a), self.d.item_kind(b)
        if ka is None or ka != kb: return False
        free = {7} if ka == "weapon" else {6, 7, 8, 9}
        for k in range(12):
            if k in free: continue
            if pa[k] != pb[k]: return False
        return all(pb[k] >= pa[k] for k in free if not (ka != "weapon" and k == 8))

    def gear_ok(self, rec, cur):
        da, db = dict(rec), dict(cur)
        return all(self.plain_upgrade(da[k], db.get(k, 0)) for k in da)

    def _fits(self, role_rec, c, spells):
        """Can current member signature `c` fill the recorded role? -> reason or None."""
        o = role_rec["sig"]; role = role_rec["role"]
        if any(c["status"]): return "ailment"
        if c["hp"] <= 0: return "dead"
        if role == "idle": return None
        if c["level"] < o["level"] or c["max_hp"] < o["max_hp"]: return "weaker"
        if c["back"] != o["back"]: return "row differs"
        if c["passives"] != o["passives"]: return "passives differ"
        if not self.gear_ok(o["equip"], c["equip"]): return "gear differs"
        if c["hp"] - role_rec["loss_hp"] < max(1, HP_MARGIN * c["max_hp"]): return "HP would run short"
        if role == "actor":
            if c["job"] != o["job"]: return "job differs"
            if c["max_mp"] < o["max_mp"] or any(a < b for a, b in zip(c["stats"], o["stats"])): return "weaker"
            if c["mp"] - role_rec["loss_mp"] < 0: return "MP would run short"
            if not {x for x in o["commands"] if x} <= {x for x in c["commands"] if x}: return "command missing"
            for cmd, sel in role_rec["used"]:
                if cmd == MAGIC_CAST and not (spells[sel >> 3] & (0x80 >> (sel & 7))): return "spell unknown"
        return None

    def lookup(self, enc, bg, f):
        """-> (record, assignment {current slot: recorded slot}, why)."""
        res = self._lookup(enc, bg, f)
        if res[0] is None: self.reasons[res[2]] = self.reasons.get(res[2], 0) + 1
        return res

    def _lookup(self, enc, bg, f):
        key = (enc, bg); r = self.rec.get(key)
        if r is None: return None, None, "no record"
        if r["code"] != self.code_key: return None, None, "battle code changed"
        cur = signature(f); cs = [i for i, c in enumerate(cur) if c is not None]; rs = sorted(r["mem"])
        if len(cs) != len(rs): return None, None, "party size differs"
        spells = bytes(f.wram[MAGIC_BITS:MAGIC_BITS + SPELL_BYTES])
        why = None                     # the reason the first (same order) assignment fails: the others only fail by comparing different jobs
        for perm in itertools.permutations(rs):
            assign = dict(zip(cs, perm)); bad = None
            for ci, ri in assign.items():
                bad = self._fits(r["mem"][ri], cur[ci], spells)
                if bad: break
            if bad: why = why or bad; continue
            bad = self._thresholds(r, cur, assign)
            if bad: why = why or bad; continue
            bag = f.items()
            for it, dl in r["items"].items():
                if dl < 0 and bag.get(it, 0) + dl < 1: return None, None, "items would run short"
            return r, assign, "ok"
        return None, None, why or "no member fits"

    def _thresholds(self, r, cur, assign):
        """The rewards must not cross a level-up or an ability / job-level ABP cost (those change the party)."""
        for ci, ri in assign.items():
            c = cur[ci]; m = r["mem"][ri]
            if c["mp"] + m["dmp"] < 0: return "MP would run short"
            if c["level"] < 99 and c["exp"] + m["dexp"] + EXP_SLACK >= self.d.level_exp(c["level"] + 1): return "level-up"
            if m["dap"] > 0:
                costs = [cost for cost, _ in self.ab.job_list(c["job"])]
                if any(c["ap"] < cost <= c["ap"] + m["dap"] + EXP_SLACK for cost in costs) or c["ap"] + m["dap"] + EXP_SLACK >= 0xFFF: return "ability / job level"
        return None

    def apply(self, enc, bg, f, r, assign):
        """The party's state after the fight, from the recorded adjustments."""
        for ci, ri in assign.items():
            m = r["mem"][ri]; ch = f.character(ci)
            ch.hp = max(1, min(ch.max_hp, round(ch.hp + m["dhp"]))); ch.mp = max(0, min(ch.max_mp, round(ch.mp + m["dmp"])))
            ch.exp = ch.exp + round(m["dexp"]); ch.ap = ch.ap + round(m["dap"]); f.set_character(ci, ch)
        f.gil = f.gil + round(r["gil"])
        bag = f.items()
        for it, dl in r["items"].items():
            v = bag.get(it, 0) + round(dl)
            if v > 0: bag[it] = min(99, v)
            else: bag.pop(it, None)
        f.set_items(bag)
        self.hits += 1
        return round(sum(m["dexp"] for m in r["mem"].values()))


MAGIC_CAST = 0x2C              # the command id a cast leaves in the menu data; `selected` is then the spell id
