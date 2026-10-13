"""Solved random fights (user idea, 2026-10-13): once a party composition has beaten an encounter flawlessly in a full batch, later instances
of the same encounter are distilled to the adjustments of the party's resources instead of simulating another batch.

  * A record holds the party signature (per slot: job, commands, passives, level, max HP / MP, the exact gear worn, spells known) and what the flawless runs
    did to the party: mean change of HP, MP, exp, ABP, gil and items, and the worst HP / MP loss of any run.
  * A later instance REUSES the record when the current party is at least as strong in every slot (same job, commands and exact gear, level and
    max HP / MP no lower, spells a superset, no ailment), applying the recorded losses would not take HP, MP or an item below zero (HP keeps
    a margin over the worst recorded loss), and the rewards do not cross a threshold (a level-up, or an ability / job-level ABP cost): a
    level-up changes the party, so that fight is simulated afresh.
  * A fight that is not reusable runs the full batch, which then replaces the record.  Every REVALIDATE-th use runs the full batch too, and
    a record only counts for the battle code it was made with (a changed bot may play differently), so each record is re-run once after any
    change to the code.
"""
import hashlib, os, pickle
from field import MAGIC_BITS

REVALIDATE = 6                 # every 6th use of a record runs the full batch again
HP_MARGIN = 0.10               # the party keeps this fraction of max HP over the worst recorded loss
SPELL_BYTES = 12
EXP_SLACK = 1                  # rewards must stay this far below a threshold


def signature(f, d):
    """Per party slot: what decides how a fight goes.  Gear is compared item by item, never as a score: a piece can matter for an element, a status
    or a monster family (Bone Mail's resistances) that a power number does not show, so any change of gear means a fresh batch."""
    sig = []
    for i in range(4):
        ch = f.character(i)
        if not ch.in_party: sig.append(None); continue
        sig.append(dict(job=ch.job, commands=tuple(ch.commands), passives=(ch.passives1, ch.passives2), level=ch.level, max_hp=ch.max_hp, max_mp=ch.max_mp,
                        equip=tuple(sorted(ch.equipment().items())), back=ch.back_row, status=(ch.status1, ch.status2, ch.status3, ch.status4),
                        hp=ch.hp, mp=ch.mp, exp=ch.exp, ap=ch.ap, job_level=ch.job_level))
    return sig


class Solved:
    def __init__(self, d, abilities, path=None, code_key=""):
        self.d = d; self.ab = abilities; self.path = path; self.code_key = code_key
        self.rec = {}                  # (enc, bg) -> record
        self.uses = {}                 # (enc, bg) -> reuses since the last full batch
        self.hits = self.misses = self.invalid = 0
        if path and os.path.exists(path):
            try: self.rec = pickle.load(open(path, "rb"))
            except Exception: self.rec = {}

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp%d" % os.getpid(); pickle.dump(self.rec, open(tmp, "wb")); os.replace(tmp, self.path)

    # ------------------------------------------------------------------ record
    def record(self, enc, bg, start, flawless, rate):
        """`start`: the Field before the fight; `flawless`: the batch results that count (each has the end WRAM)."""
        from field import Field
        key = (enc, bg); s = signature(start, self.d); n = len(flawless)
        if n == 0: self.rec.pop(key, None); return
        ends = [Field.from_wram(r["wram"], self.d) for r in flawless]
        mem = {}
        for i, si in enumerate(s):
            if si is None: continue
            chs = [e.character(i) for e in ends]
            if any(c.hp <= 0 or (c.status1 | c.status2 | c.status3 | c.status4) for c in chs): self.rec.pop(key, None); return       # an ailment or a dead member: not a clean, quantifiable fight
            if any(c.level != si["level"] or c.job_level != si["job_level"] or tuple(c.commands) != si["commands"] for c in chs): self.rec.pop(key, None); return   # growth events: simulate them
            mem[i] = dict(dhp=sum(c.hp - si["hp"] for c in chs) / n, dmp=sum(c.mp - si["mp"] for c in chs) / n, dexp=sum(c.exp - si["exp"] for c in chs) / n,
                          dap=sum(c.ap - si["ap"] for c in chs) / n,
                          loss_hp=max(0, max(si["hp"] - c.hp for c in chs)), loss_mp=max(0, max(si["mp"] - c.mp for c in chs)))
        bag0 = start.items(); items = {}
        for it in set(bag0) | {k for e in ends for k in e.items()}:
            dl = sum(e.items().get(it, 0) - bag0.get(it, 0) for e in ends) / n
            if abs(dl) > 1e-9: items[it] = dl
        spells = bytes(start.wram[MAGIC_BITS:MAGIC_BITS + SPELL_BYTES])
        self.rec[key] = dict(code=self.code_key, sig=s, mem=mem, items=items, spells=spells, rate=rate, n=n,
                             gil=sum(e.gil - start.gil for e in ends) / n, frames=0)
        self.uses[key] = 0; self.save()

    # ------------------------------------------------------------------ reuse
    def lookup(self, enc, bg, f):
        """-> (record, why) ; record None when this party cannot skip the batch (why = reason, for the log)."""
        key = (enc, bg); r = self.rec.get(key)
        if r is None: return None, "no record"
        if r["code"] != self.code_key: return None, "battle code changed"
        if self.uses.get(key, 0) >= REVALIDATE - 1: return None, "revalidation"
        cur = signature(f, self.d); old = r["sig"]
        if [c is None for c in cur] != [o is None for o in old]: return None, "party changed"
        if bytes(f.wram[MAGIC_BITS:MAGIC_BITS + SPELL_BYTES]) != r["spells"] and any(a & ~b for a, b in zip(r["spells"], f.wram[MAGIC_BITS:MAGIC_BITS + SPELL_BYTES])): return None, "fewer spells"
        for i, (c, o) in enumerate(zip(cur, old)):
            if c is None: continue
            if c["job"] != o["job"] or c["commands"] != o["commands"] or c["back"] != o["back"]: return None, "job / commands differ"
            if c["passives"] != o["passives"]: return None, "passives differ"
            if c["level"] < o["level"] or c["max_hp"] < o["max_hp"] or c["max_mp"] < o["max_mp"]: return None, "weaker than the record"
            if c["equip"] != o["equip"]: return None, "gear differs"
            if any(c["status"]): return None, "ailment"
            m = r["mem"][i]
            if c["hp"] - m["loss_hp"] < max(1, HP_MARGIN * c["max_hp"]): return None, "HP would run short"
            if c["mp"] + m["dmp"] < 0 or c["mp"] - m["loss_mp"] < 0: return None, "MP would run short"
            # rewards must not cross a level-up or an ability / job-level ABP cost
            if c["level"] < 99 and c["exp"] + m["dexp"] + EXP_SLACK >= self.d.level_exp(c["level"] + 1): return None, "level-up"
            dap = m["dap"]
            if dap > 0:
                costs = [cost for cost, _ in self.ab.job_list(c["job"])]
                if any(c["ap"] < cost <= c["ap"] + dap + EXP_SLACK for cost in costs) or c["ap"] + dap + EXP_SLACK >= 0xFFF: return None, "ability / job level"
        bag = f.items()
        for it, dl in r["items"].items():
            if dl < 0 and bag.get(it, 0) + dl < 1: return None, "items would run short"
        return r, "ok"

    def apply(self, enc, bg, f, r):
        """The party's state after the fight, from the recorded adjustments."""
        key = (enc, bg)
        for i, m in r["mem"].items():
            ch = f.character(i)
            ch.hp = max(1, min(ch.max_hp, round(ch.hp + m["dhp"]))); ch.mp = max(0, min(ch.max_mp, round(ch.mp + m["dmp"])))
            ch.exp = ch.exp + round(m["dexp"]); ch.ap = ch.ap + round(m["dap"]); f.set_character(i, ch)
        f.gil = f.gil + round(r["gil"])
        bag = f.items()
        for it, dl in r["items"].items():
            v = bag.get(it, 0) + round(dl)
            if v > 0: bag[it] = min(99, v)
            else: bag.pop(it, None)
        f.set_items(bag)
        self.uses[key] = self.uses.get(key, 0) + 1; self.hits += 1
        return round(sum(m["dexp"] for m in r["mem"].values()))
