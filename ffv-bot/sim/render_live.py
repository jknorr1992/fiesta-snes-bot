"""Render the map the emulator is on (live position and live event flags) with the sim's reachable tiles, plus optionally another map.
usage: render_live.py chain.pkl out_prefix [target_map]   (tile modifications come from the last chain state; flags/position are live)"""
import sys, pickle, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_plan import *
import viewmap
S = Sim(); ex = Exec(S)
ch = pickle.load(open(sys.argv[1], 'rb')); s = State.clone(ch[-1][2])
lv = ex.live(); s.flags = lv["flags"]; s.map, s.x, s.y = lv["map"], lv["x"], lv["y"]
if s.map != ch[-1][2].map: s.mods = {}
viewmap.render(S, s, sys.argv[2] + "_here_map%d.png" % s.map)
print(sys.argv[2] + "_here_map%d.png" % s.map)
if len(sys.argv) > 3 and int(sys.argv[3]) != s.map:
    t = State.clone(s); t.map = int(sys.argv[3]); t.mods = {}
    viewmap.render(S, t, sys.argv[2] + "_target_map%d.png" % t.map); print(sys.argv[2] + "_target_map%d.png" % t.map)
