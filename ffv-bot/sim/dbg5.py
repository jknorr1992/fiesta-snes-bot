import sys, time
sys.path.insert(0, '.')
from execute import *
S = Sim(); s0 = S.initial()
ex = Exec(S)
ex.load_from("/mnt/project-files/retroarch-testbed/states/newgame.state", 1)
a = S.actions(s0)[0]
ex.do(s0, a)
print(ex.fpos())
s1 = S.execute(s0, a)[0]
a1 = [x for x in S.actions(s1) if x.kind == "walkevent"][0]
print(a1, a1.path)
# manual with prints
DX = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}
m0, cx, cy = ex.fpos()
held = None
for d in a1.path:
    name = BTN[d]; dx, dy = DX[d]; exp = (cx + dx, cy + dy)
    if held != name:
        if held: ex._send(IDS[held], False)
        ex._send(IDS[name], True); held = name
    trace = []
    for k in range(60):
        p = ex.fpos(); trace.append(p[1:])
        if p[1:] == exp: break
    print(name, exp, "reached" if p[1:] == exp else "FAIL", len(trace), trace[-3:])
    if p[1:] != exp: break
    cx, cy = exp
ex._send(IDS[held], False)
