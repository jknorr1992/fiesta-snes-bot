import sys; sys.path.insert(0,'.')
from run_plan import *
S, ch = start(17, 11, 45)
s = ch[7][2]
a=[a for a in S.actions(s) if a.event==1050][0]
ex = Exec(S); ex.load_from("%s7.state" % CK, 7); ex.cheat_party()
n,p = ex.walk_dirs(a.path[:3]); print(n,p)
ex.step("UP")
seq=[]
for i in range(400):
    ex.wait(1); q=ex.fpos()
    if not seq or seq[-1]!=q: seq.append(q)
print(seq)
