#!/usr/bin/env python3
"""Backward relevance from the ending: which trigger/NPC groups (and so events and flags) can matter for reaching event $bf0f.
Usage: event_relevance.py <rom.sfc>   (prints counts; import compute() for the sets)"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_vm import Rom
from event_effects import effects

NPC = 512

def all_groups(rom):
    """Every (kind, map, x, y, script, conds, event) trigger and NPC group in the ROM."""
    out = []
    for m in range(511):
        for x, y, t in rom.triggers(m):
            for conds, ev in rom.trigger_script(t):
                out.append(("trigger", m, x, y, t, conds, ev))
        for n in rom.npcs(m):
            for conds, ev in rom.npc_script(n["script"]):
                out.append(("npc", m, n["x"], n["y"], n["script"], conds, ev))
    return out

def compute(rom, extra_roots=()):
    memo = {}
    eff = {e: effects(rom, e, memo) for e in range(1, 1939) if e not in (244,)}
    groups = [g for g in all_groups(rom) if 0 < g[6] < 1939 and g[6] != 244]
    # events that run the ending
    roots = {e for e, x in eff.items() if any(op == 0xBF and d.get("sub") == 0x0F for op, d in rom.event_ops(e))}
    # chained: events that call a root
    changed = True
    while changed:
        changed = False
        for e, x in eff.items():
            if e not in roots and any(c in roots for c in x["calls"]):
                roots.add(e); changed = True
    rel_events = set(roots) | set(extra_roots)
    need, need_maps = set(), set()
    parents = {}  # local map -> local maps with an entrance into it (a map that is needed makes its ancestors needed)
    for m in range(5, 511):
        for e in rom.entrances(m):
            if e["map"] >= 5 and e["map"] != m:
                parents.setdefault(e["map"], set()).add(m)
    def close_maps():
        stack = list(need_maps)
        while stack:
            for p in parents.get(stack.pop(), ()):
                if p not in need_maps:
                    need_maps.add(p); stack.append(p)
    groups_all = all_groups(rom)
    prior = {}  # group index -> flags that the groups before it in the same script test with `clear` (they must be set to fall through)
    for i, g in enumerate(groups_all):
        j, fl = i - 1, set()
        while j >= 0 and groups_all[j][:5] == g[:5]:
            fl |= {c[1] for c in groups_all[j][5] if c[0] == "clear"}
            j -= 1
        prior[i] = fl
    by_event = {}
    for i, g in enumerate(groups_all): by_event.setdefault(g[6], []).append(i)
    def add_event(e):
        for i in by_event.get(e, ()):
            g = groups_all[i]
            if True:
                need.update(prior[i])
                need_maps.add(g[1])
                for c in g[5]:
                    if c[0] == "set": need.add(c[1])
                if g[0] == "npc":
                    need.add(NPC + g[4])
        close_maps()
    for e in list(rel_events): add_event(e)
    changed = True
    while changed:
        changed = False
        for e, x in eff.items():
            if e in rel_events: continue
            gives = (x["set"] | {NPC + n for n in x["npc_on"]}) & need
            reaches = {m for m, _, _ in x["maps"]} & need_maps
            if gives or reaches:
                rel_events.add(e); add_event(e); changed = True
    return eff, groups, rel_events, need, roots

if __name__ == "__main__":
    rom = Rom(sys.argv[1])
    eff, groups, rel, need, roots = compute(rom)
    print("ending events:", sorted(roots))
    print("relevant events:", len(rel), "needed flags:", len(need))
    print("need flags:", sorted(need)[:80])
    print("ending groups:", [g for g in groups if g[6] in roots])
