#!/usr/bin/env python3
"""Battle simulator command line.

  run.py battle 0x1d                    describe a battle (monsters, boss flag...)
  run.py map 166                        random battles of a local map
  run.py world 3                        random battles of a world-map region
  run.py fight 0x1d [--state S.state4]  simulate it: new 4-member party, or the party/items of a RetroArch save state
  run.py sweep [--fix]                  run every battle 0-511 and report crashes (--fix applies fix_rom.py first)
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim import BattleSim, DEFAULT_ROM
from data import GameData
from field import Field
from scenario import run_battle

def num(s): return int(s, 0)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=DEFAULT_ROM)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("battle", "map", "world"): sub.add_parser(n).add_argument("id", type=num)
    f = sub.add_parser("fight"); f.add_argument("id", type=num); f.add_argument("--state"); f.add_argument("--party", type=int, default=4)
    f.add_argument("--background", type=num, default=0); f.add_argument("--frames", type=int, default=60000)
    s = sub.add_parser("sweep"); s.add_argument("--fix", action="store_true"); s.add_argument("--first", type=num, default=0); s.add_argument("--last", type=num, default=511)
    a = ap.parse_args(argv)
    rom = open(a.rom, "rb").read()
    if a.cmd == "sweep" and a.fix:
        import fix_rom; rom = fix_rom.fixed(rom)
    d = GameData(rom)
    if a.cmd == "battle": print(d.battle_summary(a.id), d.battle(a.id)["raw"])
    elif a.cmd in ("map", "world"):
        e = d.map_encounters(a.id) if a.cmd == "map" else d.world_encounters(a.id)
        print({k: v for k, v in e.items() if k != "battles"})
        for b, p in e["battles"]: print(f"  {p:5.1%}  {d.battle_summary(b)}")
    elif a.cmd == "fight":
        field = Field.from_state(a.state, d) if a.state else Field.new(d, party=a.party)
        t = time.time(); r = run_battle(field, a.id, a.background, max_frames=a.frames, rom=rom)
        print(d.battle_summary(a.id)); print(r.summary()); print(f"({time.time() - t:.2f}s wall)")
        if r.crash: print("CRASH:", r.crash)
    elif a.cmd == "sweep":
        sim = BattleSim(rom); bad = []
        for i in range(a.first, a.last + 1):
            if all(m is None for m in d.battle(i)["monster_ids"]): continue
            r = run_battle(Field.new(d, party=4), i, 0, max_frames=20000, sim=sim)
            if r.outcome in ("crash", "timeout"): bad.append(i); print(f"{i:#05x} {r.outcome}: {d.battle_summary(i)} {r.crash}", flush=True)
        print(f"{len(bad)} problem battles: {[hex(b) for b in bad]}")

if __name__ == "__main__": main()
