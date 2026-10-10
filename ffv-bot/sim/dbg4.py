import sys, time
sys.path.insert(0, '.')
from execute import *
S = Sim()
ex = Exec(S)
print(ex.fpos())
for d in ("RIGHT","RIGHT","UP"):
    print(d, ex.step(d), ex.fpos(), ex.u8(0xB57), ex.u8(0x18))
