import sys, time
sys.path.insert(0, '.')
from sim import *
from explore import search
S = Sim(); s0 = S.initial()
# explore everything reachable with plain Dijkstra until a state in map 29 is found
f, meta = search(S, s0, lambda s: s.map == 29, max_exp=4000)
print("found", f is not None)
if f:
    path = []; s = f
    while meta[id(s)][0] is not None:
        p, a, g = meta[id(s)]; path.append((a.desc, g)); s = p
    for d in path[::-1]: print(d)
    print("at", f.map, f.x, f.y, f.z)
    for a in S.actions(f): print(a)
    import pickle
    pickle.dump(f, open("/tmp/claude-0/scratch/s29.pkl", "wb"))
