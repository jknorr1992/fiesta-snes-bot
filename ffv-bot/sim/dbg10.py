import sys
sys.path.insert(0, '.')
from run_plan import *
S, ch = start(17, 11, 45)
p, a, n = ch[3]
print(a.kind,a.desc,a.stand,a.face,a.event,a.path)
ex = Exec(S); ex.load_from("%s2.state" % CK, 2)
print(ex.fpos())
x0=ex.fpos()
for k,d in enumerate(a.path):
    ex.wait(24) if k and d!=a.path[k-1] else 0; m,pp = ex.step(BTN[d]); print(k,d,pp)
ex.shot()
