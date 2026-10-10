#!/usr/bin/env python3
"""Explain why a flag or map is unreachable in the monotone model (event_reach.py): follows setter events, their trigger conditions and
the entrances/events that would lead to their map, recursively. Usage: event_blockers.py <rom> flag:N|map:N ..."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from event_reach import Reach, NPC

def callers(r):
    if not hasattr(r, "_callers"):
        c = {}
        for e in range(1, 1939):
            for op, d in r.rom.event_ops(e):
                if op == 0xCD: c.setdefault(d["event"], set()).add(e)
                if op == 0xF0:
                    y = bytes.fromhex(d["yes"])
                    if y[:1] == b"\xcd": c.setdefault(int.from_bytes(y[1:3], "little"), set()).add(e)
        r._callers = c
    return r._callers

def up(r, evs):
    out, st = set(), list(evs)
    c = callers(r)
    while st:
        e = st.pop()
        if e in out: continue
        out.add(e); st.extend(c.get(e, ()))
    return out

def explain(r, what, depth=0, seen=None, names=None, maxd=6):
    seen = seen if seen is not None else set()
    pad = "  " * depth
    kind, n = what
    if (kind, n) in seen or depth > maxd:
        return
    seen.add((kind, n))
    maps = {k[0] for k in r.ctx}
    nm = lambda m: "%d %s" % (m, (names or {}).get(m, ""))
    if kind == "flag":
        evs = [e for e, x in r.eff.items() if (n in x["set"]) or (n >= NPC and n - NPC in x["npc_on"])]
        print(pad + "flag %d %s; set by events %s" % (n, "SET" if r.S >> n & 1 else "never set", evs[:8]))
        if r.S >> n & 1: return
        for g in r.groups:
            if g[6] in up(r, evs[:8]):
                un = [c[1] for c in g[5] if c[0] == "set" and not r.S >> c[1] & 1]
                print(pad + " - %s on map %s (%d,%d) runs event %d; unmet flags %s; map %s" % (g[0], nm(g[1]), g[2], g[3], g[6], un, "reachable" if g[1] in maps else "NOT reachable"))
                for f in un: explain(r, ("flag", f), depth + 2, seen, names, maxd)
                if g[1] not in maps: explain(r, ("map", g[1]), depth + 2, seen, names, maxd)
    else:
        print(pad + "map %s %s" % (nm(n), "reachable" if n in maps else "NOT reachable"))
        if n in maps: return
        for s in range(511):
            for en in r.rom.entrances(s):
                if en["map"] == n:
                    print(pad + " - entrance from %s %s" % (nm(s), "reachable" if s in maps else "NOT reachable"))
                    if s not in maps: explain(r, ("map", s), depth + 2, seen, names, maxd)
        for e in range(1, 1939):
            if any(op in (0xE0, 0xE1, 0xE3) and d["map"] == n for op, d in r.rom.event_ops(e)):
                for g in r.groups:
                    if g[6] in up(r, [e]):
                        un = [c[1] for c in g[5] if c[0] == "set" and not r.S >> c[1] & 1]
                        print(pad + " - event %d via %s map %s (%d,%d); unmet flags %s; map %s" % (e, g[0], nm(g[1]), g[2], g[3], un, "reachable" if g[1] in maps else "NOT reachable"))
                        for f in un: explain(r, ("flag", f), depth + 2, seen, names, maxd)
                        if g[1] not in maps: explain(r, ("map", g[1]), depth + 2, seen, names, maxd)

if __name__ == "__main__":
    r = Reach(sys.argv[1]).run()
    names = {int(k): v for k, v in json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "map_names.json"))).items()}
    for a in sys.argv[2:]:
        k, n = a.split(":"); explain(r, (k, int(n)), names=names)
