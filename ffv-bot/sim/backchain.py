"""Backward chaining from the credits: cheapest supporting hyperedge for each prerequisite (relaxed model, initial state)."""
import sys; sys.path.insert(0, '.')
from sim import Sim
from relaxed import Relaxed
S = Sim(); Rl = Relaxed(S); s0 = S.initial()
d = Rl.levels(s0.flags, s0.map, s0.npcf)
INF = 10 ** 9
by_t = {}
for i, (t, pre, c) in enumerate(Rl.he): by_t.setdefault(t, []).append(i)
def name(n):
    if n < Rl.oN: return "flag %d" % n
    if n < Rl.oM: return "npc %d" % (n - Rl.oN)
    if n < Rl.oE: return "map %d" % (n - Rl.oM)
    return "event %d" % (n - Rl.oE)
seen = {}
def back(n):
    if n in seen or d[n] == 0: return
    best = None
    for i in by_t.get(n, []):
        t, pre, c = Rl.he[i]
        v = max([d[p] for p in pre] + [0]) + c
        if v == d[n] and (best is None or len(pre) < len(Rl.he[best][1])): best = i
    seen[n] = best
    if best is None: return
    for p in Rl.he[best][1]: back(p)
back(Rl.oE + 1924)
for n, i in sorted(seen.items(), key=lambda kv: d[kv[0]]):
    pre = Rl.he[i][1] if i is not None else []
    print("L%-3d %-12s <- %s" % (d[n], name(n), ", ".join(name(p) for p in pre if d[p])))
