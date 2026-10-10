import sys, time
sys.path.insert(0, '.')
from sim import *
from relaxed import Relaxed
from explore import search

S = Sim(); Rl = Relaxed(S); s0 = S.initial()
target_flag = int(sys.argv[1]); wh = float(sys.argv[2]); N = int(sys.argv[3])
h = lambda s: Rl.h_flag(target_flag, s.flags, s.map, s.npcf)
f, meta = search(S, s0, lambda s: s.flag(target_flag), max_exp=N, wh=wh, h=h, log=100)
print("found", f is not None)
if f:
    path = []; s = f
    while meta[id(s)][0] is not None:
        p, a, g = meta[id(s)]; path.append((a.desc, g)); s = p
    for d in path[::-1]: print(d)
