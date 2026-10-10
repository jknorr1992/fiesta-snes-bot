#!/usr/bin/env python3
"""Build data/event_graph.json: for every map trigger its position, flag conditions, the event it runs and the flags
that event sets or clears. Reads the ROM only. Usage: build_event_graph.py <rom.sfc> <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_conditions import trigger_script, triggers
from event_scripts import scan

def main(rom_path, out):
    rom = open(rom_path, "rb").read()
    graph = []
    for m in range(512):
        for x, y, t in triggers(rom, m):
            for conds, ev in trigger_script(rom, t):
                if ev >= 1940:
                    continue
                sets, clears, ok = scan(rom, ev)
                graph.append({"map": m, "x": x, "y": y, "trigger": t, "needs_set": [c[1] for c in conds if c[0] == "flag_set"],
                              "needs_clear": [c[1] for c in conds if c[0] == "flag_clear"], "other_conds": [c for c in conds if c[0] not in ("flag_set", "flag_clear")],
                              "event": ev, "sets": sets, "clears": clears, "script_ok": ok})
    json.dump(graph, open(out, "w"), indent=0)
    print(len(graph), "trigger events written to", out)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
