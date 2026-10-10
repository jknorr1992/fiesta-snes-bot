import sys; sys.path.insert(0,'.')
from run_plan import *
S, ch = start(17, 11, 45)
s = ch[7][2]
print(s.map,s.x,s.y)
acts = S.actions(s) if hasattr(S,'actions') else None
print([m for m in dir(S) if 'action' in m])
for a in S.actions(s):
    if a.kind not in ('walk',) or a.event is not None:
        print(a.kind, a.desc, a.cost, a.event)
