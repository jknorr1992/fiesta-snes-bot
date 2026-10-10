#!/usr/bin/env python3
"""Per-event effects with chained events ($cd jumps) followed: flags set/cleared, NPC flags, map loads, battles, party changes.
Usage: event_effects.py <rom.sfc> [event ...] -> prints effects; with no events prints which events set/clear key flags."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_vm import Rom

def effects(rom, e, memo, stack=()):
    if e in memo: return memo[e]
    out = {"set": set(), "clear": set(), "npc_on": set(), "npc_off": set(), "maps": [], "battles": [], "party": [], "calls": []}
    if e in stack or not 0 < e < 1940:
        return out
    for op, d in rom.event_ops(e):
        if op in (0xA2, 0xA4): out["set"].add(d["flag"])
        elif op in (0xA3, 0xA5): out["clear"].add(d["flag"])
        elif op == 0xCA: out["npc_on"].add(d["npc_flag"])
        elif op == 0xCB: out["npc_off"].add(d["npc_flag"])
        elif op in (0xE0, 0xE1, 0xE3): out["maps"].append((d["map"], d["world_x"] if d["map"] < 5 else d["x"], d["y"]))
        elif op in (0xBD, 0xE2): out["battles"].append(d["battle"])
        elif op == 0xB7: out["party"].append((d["char"], "remove" if d["remove"] else "add"))
        elif op == 0xCD or (op == 0xF0 and bytes.fromhex(d["yes"])[:1] == b"\xcd"):
            t = d["event"] if op == 0xCD else int.from_bytes(bytes.fromhex(d["yes"])[1:3], "little")
            out["calls"].append(t)
            sub = effects(rom, t, memo, stack + (e,))
            for k in ("set", "clear", "npc_on", "npc_off"): out[k] = out[k] | sub[k]
            for k in ("maps", "battles", "party"): out[k] = out[k] + sub[k]
    memo[e] = out
    return out

if __name__ == "__main__":
    rom = Rom(sys.argv[1]); memo = {}
    if len(sys.argv) > 2:
        for e in map(int, sys.argv[2:]): print(e, effects(rom, e, memo))
    else:
        eff = {e: effects(rom, e, memo) for e in range(1, 1940)}
        for f in (16, 17, 70, 71, 306, 486, 487):
            print("flag", f, "set by", [e for e, x in eff.items() if f in x["set"]], "cleared by", [e for e, x in eff.items() if f in x["clear"]])
