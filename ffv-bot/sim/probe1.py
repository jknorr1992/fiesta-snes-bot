import sys, time, heapq
sys.path.insert(0, '.')
from sim import *
from explore import explore
S = Sim(); s0 = S.initial()
t = time.time()
f, par = explore(S, s0, max_states=int(sys.argv[1]))
print("states", len(par), "time", time.time() - t)
# summarise: maps seen and max flags
maps = {}
for k in par: maps.setdefault(k[2], 0); maps[k[2]] += 1
print(sorted(maps.items())[:80])
best = max(par, key=lambda k: bin(k[0]).count("1"))
print("max flags", bin(best[0]).count("1"), "map", best[2], best[3], best[4])
