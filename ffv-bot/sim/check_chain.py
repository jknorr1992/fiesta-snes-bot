"""Simulator-only check that the planner connects a new game to the credits: plan goal after goal from the new-game state, each goal
from the state the previous plan ended in, and report the cost of every goal. No emulator needed.

usage: check_chain.py [--save PKL] [--from PKL] goal [goal ...]      goal = map:N | flag:N | pos:M:X:Y | @FILE
env: FFV_ROM, N_EXP (search budget per goal, default 20000)"""
import sys, os, time, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import *


def main(argv):
    save = argv[argv.index("--save") + 1] if "--save" in argv else None
    start = argv[argv.index("--from") + 1] if "--from" in argv else None
    goals = []
    skip = False
    for i, a in enumerate(argv):
        if skip: skip = False; continue
        if a in ("--save", "--from"): skip = True
        elif a.startswith("@"): goals += [g for l in open(a[1:]) for g in l.split("#")[0].split()]
        elif not a.startswith("--"): goals.append(a)
    S = Sim(); Rl = Relaxed(S)
    chain_all = pickle.load(open(start, "rb")) if start else []
    s = chain_all[-1][2] if chain_all else S.initial()
    n_exp = int(os.environ.get("N_EXP", "20000")); t0 = time.time()
    for spec in goals:
        if spec.startswith(("at:", "open:")): continue                  # live-only steps (walk next to a chest, open it)
        goal, _ = goal_fn(spec)
        if goal(s): print("%-14s already true" % spec, flush=True); continue
        t = time.time()
        f, k = search(S, s, goal, max_exp=n_exp, wh=1.0, h=heuristic(Rl, spec), log=0)
        if not f:
            print("%-14s NO PLAN after %d expansions (%.0fs) from map %d (%d,%d)" % (spec, k, time.time() - t, s.map, s.x, s.y), flush=True)
            if save: pickle.dump(chain_all, open(save, "wb"))
            return 1
        ch = chain(f); chain_all += ch; s = ch[-1][2]
        print("%-14s %3d actions, %5d exp, %5.1fs  -> map %d (%d,%d)" % (spec, len(ch), k, time.time() - t, s.map, s.x, s.y), flush=True)
    if save: pickle.dump(chain_all, open(save, "wb"))
    print("all %d goals planned, %d actions, %.0fs" % (len(goals), len(chain_all), time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
