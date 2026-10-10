#!/usr/bin/env python3
"""Per event: the maps it loads directly and the events it jumps to (cached in data/event_loads.json; decoding is slow)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from event_vm import Rom
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "event_loads.json")

def build(rom_path):
    rom = Rom(rom_path); out = {}
    for ev in range(rom.n_events):
        try: ops = rom.event_ops(ev)
        except Exception: continue
        loads = [[a["map"] & 0x3FF, a["x"] & 0x3F, a["y"]] for op, a in ops if op in (224, 225, 227) and not a.get("parent") and a.get("map") is not None]
        jumps = sorted({a["event"] for op, a in ops if op == 205})
        if loads or jumps: out[ev] = {"loads": loads, "jumps": jumps}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    return out

def load():
    return {int(k): v for k, v in json.load(open(OUT)).items()}

if __name__ == "__main__":
    print(len(build(sys.argv[1])), "events with loads/jumps")
