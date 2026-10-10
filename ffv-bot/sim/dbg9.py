import sys
sys.path.insert(0, '.')
from run_plan import *
import play_nav; play_nav.HOLDS = (1, 3, 14)
S, ch = start(17, 11, 45)
p, a, n = ch[2]
ex = Exec(S); ex.load_from("%s1.state" % CK, 1)
a_path=a.path
for k,d in enumerate(a_path[:19]):
    m,pp = ex.step(BTN[d])
    print(k,d,pp)
for i in range(3):
    print(ex.step(BTN[2]))
print(ex.read(0x0AD4,8).hex(), ex.read(0x0ADC,1).hex())
print(ex.shot())
