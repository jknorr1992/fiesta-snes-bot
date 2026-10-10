import sys, time
sys.path.insert(0, '.')
from execute import *
S = Sim(); s0 = S.initial()
ex = Exec(S)
ex.load_from("/mnt/project-files/retroarch-testbed/states/newgame.state", 1)
a = S.actions(s0)[0]
ex.do(s0, a)
s1 = S.execute(s0, a)[0]
a1 = [x for x in S.actions(s1) if x.kind == "walkevent"][0]
t=time.time()
for d in a1.path:
    r = ex.step(BTN[d])
    print(BTN[d], r, "%.2f"%(time.time()-t), flush=True)
