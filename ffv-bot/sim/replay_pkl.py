"""Replay a pickled stage chain on the emulator: replay_pkl.py chain.pkl [resume_index]  (checkpoint ck<i> holds the state after step i)"""
import sys, os, pickle
sys.path.insert(0, '.')
from run_plan import *
ch = pickle.load(open(sys.argv[1], 'rb'))
S = Sim()
ex = Exec(S)
resume = int(sys.argv[2]) if len(sys.argv) > 2 else -1
if resume >= 0: ex.load_from("%s%d.state" % (CK, resume), 1); print("resumed after", resume, flush=True)
else: ex.load_from("/mnt/project-files/retroarch-testbed/states/newgame.state", 1)
ex.cheat_party()
print(len(ch), "steps", flush=True)
i = run(S, ch, ex, resume + 1)
print("stopped at", i, flush=True)
