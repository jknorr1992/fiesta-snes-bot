import sys, time
sys.path.insert(0, '.')
from execute import *
S = Sim(); s0 = S.initial()
acts = S.actions(s0); a = acts[0]
outs = S.execute(s0, a); print(len(outs), outs[0].map, outs[0].x, outs[0].y, outs[0].face)
s1 = outs[0]
print("flags", [i for i in range(512) if s1.flag(i)])
for a in S.actions(s1): print(a, a.path[:30])
print("npcs", S.objects(s1))
ts = S.trig_at(122)
print({k: v for k, v in ts.items()})
