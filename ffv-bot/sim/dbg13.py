import sys; sys.path.insert(0,'.')
from run_plan import *
S, ch = start(28)
i=int(sys.argv[1])
p,a,n = ch[i]
print(a.desc,a.path,a.stand)
ex = Exec(S); ex.load_from("%s%d.state" % (CK,i-1), 1); ex.cheat_party()
x,y=ex.fpos()[1:]
print(ex.fpos())
def rd(off,k):
    out=b''
    for q in range(0,k,128): out+=ex.read(off+q,min(128,k-q))
    return out
lay=rd(0x10000,4096); obj=rd(0x13000,4096); props=rd(0x1186,512)
for yy in range(y-4,y+3):
    print(yy,' '.join('%02x:%02x%02x%s'%(lay[yy*64+xx],props[2*lay[yy*64+xx]],props[2*lay[yy*64+xx]+1],'*%02x'%obj[yy*64+xx] if obj[yy*64+xx] else '   ') for xx in range(x-3,x+5)))
lm=S.localmap(p)
print(lm.blocked)
for k,d in enumerate(a.path[:8]):
    ex.wait(24) if k and d!=a.path[k-1] else 0
    r=ex.step(BTN[d]); print(k,d,r, ex.u8(0x18), ex.u8(0xB57))
    if ex.u8(0x18)==1: ex.fight(); ex.settle(); print('after fight',ex.pos())
ex.shot()
ex.wait(120)
print(ex.step("RIGHT"), ex.step("UP"))
ex.shot()
