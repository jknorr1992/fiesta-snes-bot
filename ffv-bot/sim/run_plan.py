import sys, time, os, pickle
sys.path.insert(0, '.')
from execute import *
from explore import search, chain

CK = "/tmp/claude-0/scratch/ck"

def run(S, ch, ex, start_idx=0, stop_on_diff=True, save=True):
    for i, (p, a, n) in enumerate(ch[start_idx:], start_idx):
        t = time.time()
        print("[%d] %s (cost %d) path %d" % (i, a.desc, a.cost, len(a.path or [])), flush=True)
        ex.do(p, a)
        lv = ex.live()
        outs = S.execute(p, a)
        match = [o for o in outs if (o.map, o.x, o.y) == (lv["map"], lv["x"], lv["y"])]
        diffs = compare(S, lv, match[0] if match else n)
        print("    live map %d (%d,%d) %.0fs %s" % (lv["map"], lv["x"], lv["y"], time.time() - t, "OK" if not diffs else "DIFF " + "; ".join(diffs)), flush=True)
        if diffs and stop_on_diff: return i
        if save: ex.save_as("%s%d.state" % (CK, i))
    return None

def start(goal_map, gx=None, gy=None, n=4000):
    S = Sim(); s0 = S.initial()
    f, k = search(S, s0, lambda s: s.map == goal_map and (gx is None or (s.x, s.y) == (gx, gy)), max_exp=n)
    return S, chain(f)

if __name__ == "__main__":
    goal_map = int(sys.argv[1])
    gx = int(sys.argv[2]) if len(sys.argv) > 3 else None
    gy = int(sys.argv[3]) if len(sys.argv) > 3 else None
    S, ch = start(goal_map, gx, gy)
    print(len(ch), "steps")
    ex = Exec(S)
    resume = int(os.environ.get("RESUME", "-1"))
    if resume >= 0:
        ex.load_from("%s%d.state" % (CK, resume), 1); print("resumed after", resume)
    else:
        ex.load_from("/mnt/project-files/retroarch-testbed/states/newgame.state", 1)
    ex.cheat_party()
    i = run(S, ch, ex, resume + 1)
    print("stopped at", i)
