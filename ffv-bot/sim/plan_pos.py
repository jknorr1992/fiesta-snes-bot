"""plan_pos.py start.pkl out.pkl map x y [max_exp]: search to an exact position (no heuristic), append to the chain"""
import sys, pickle
sys.path.insert(0, '.')
from sim import *
from explore import search, chain
S = Sim(); ch = pickle.load(open(sys.argv[1], 'rb')); s = ch[-1][2]
m, x, y = map(int, sys.argv[3:6]); n = int(sys.argv[6]) if len(sys.argv) > 6 else 20000
f, k = search(S, s, lambda t: t.map == m and (t.x, t.y) == (x, y), max_exp=n, wh=0, h=lambda t: 0, log=2000)
print("found" if f else "FAILED", k)
if f: pickle.dump(ch + [(p, a, n_) for p, a, n_ in chain(f)], open(sys.argv[2], 'wb')); print(len(ch), '->', len(ch) + len(chain(f)))
