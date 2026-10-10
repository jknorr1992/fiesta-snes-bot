import sys, time
sys.path.insert(0, '.')
from run_plan import *
S, ch = start(17, 11, 45)
p, a, n = ch[2]
ex = Exec(S); ex.load_from("%s1.state" % CK, 1)
n_, pos = ex.walk_dirs(a.path)
print(n_, pos)
x, y = pos[1], pos[2]
def rd(off,k):
    out=b''
    for i in range(0,k,128): out+=ex.read(off+i,min(128,k-i))
    return out
lay=rd(0x10000,4096); obj=rd(0x13000,4096); props=rd(0x1186,512)
lm = S.localmap(p)
for yy in range(y-3,y+5):
    print(yy,' '.join('%02x:%02x%02x%s'%(lay[yy*64+xx],props[2*lay[yy*64+xx]],props[2*lay[yy*64+xx]+1],'*%02x'%obj[yy*64+xx] if obj[yy*64+xx] else '   ') for xx in range(x-3,x+4)))
print("sim")
for yy in range(y-3,y+5):
    print(yy,' '.join('%02x:%02x%02x%s'%(lm.tile(xx,yy),*lm.props(xx,yy),'*' if (xx,yy) in lm.blocked else ' ') for xx in range(x-3,x+4)))
print(ex.shot())
print(a.path)
print([ (k,v) for k,v in vars(ex).items() if k in ('last',)])
