"""Staged search from the start of the game toward the ending. Each stage: (name, goal predicate, heuristic target). Chains are pickled."""
import sys, time, pickle, os
sys.path.insert(0, '.')
from sim import *
from relaxed import Relaxed
from explore import search, chain

OUT = os.environ.get("STAGES_OUT", "/tmp/claude-0/scratch/stages.pkl")

def stage(S, Rl, s0, name, goal, hfn, n, wh):
    t = time.time()
    f, k = search(S, s0, goal, max_exp=n, wh=wh, h=hfn, log=500)
    print("%s: %s after %d exp %.0fs" % (name, "found" if f else "FAILED", k, time.time() - t), flush=True)
    return chain(f) if f else None

if __name__ == "__main__":
    S = Sim(); Rl = Relaxed(S); s = S.initial()
    allch = []
    if os.environ.get('START_PKL'):
        allch = pickle.load(open(os.environ['START_PKL'], 'rb')); s = allch[-1][2]
        print('continuing from', len(allch), 'steps: map', s.map, s.x, s.y, flush=True)
    spec = sys.argv[1:]
    # stage spec: kind:value[:n[:wh]]   kind in map|flag|event
    for sp in spec:
        p = sp.split(":"); kind, val = p[0], int(p[1]); n = int(p[2]) if len(p) > 2 else 4000; wh = float(p[3]) if len(p) > 3 else 1.0
        if kind == "map": goal = lambda s, v=val: s.map == v; hf = lambda s, v=val: Rl.levels(s.flags, s.map, s.npcf)[Rl.oM + v]
        elif kind == "flag": goal = lambda s, v=val: s.flag(v); hf = lambda s, v=val: Rl.h_flag(v, s.flags, s.map, s.npcf)
        ch = stage(S, Rl, s, sp, goal, hf, n, wh)
        if ch is None: break
        allch += ch; s = ch[-1][2]
        print("  now map %d (%d,%d) flags %d" % (s.map, s.x, s.y, bin(s.flags & S.read_mask).count("1")), flush=True)
        pickle.dump([(p, a, n_) for p, a, n_ in allch], open(OUT, "wb"))
    print("total", len(allch), "steps")
