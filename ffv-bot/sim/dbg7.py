import sys, time
sys.path.insert(0, '.')
from run_plan import *
S, ch = start(17, 11, 45)
p, a, n = ch[2]
print(a.desc, a.path)
ex = Exec(S); ex.load_from("%s1.state" % CK, 1)
print(ex.fpos())
x, y = p.x, p.y
DX = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}
for d in a.path:
    r = ex.step(BTN[d]); dx, dy = DX[d]
    print(BTN[d], (x + dx, y + dy), r[1], flush=True)
    if (r[1][1], r[1][2]) != (x + dx, y + dy): break
    x, y = x + dx, y + dy
print(ex.shot())
