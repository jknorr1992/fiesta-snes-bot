import sys, time
sys.path.insert(0, '.')
from execute import *
S = Sim()
ex = Exec(S)
print(ex.live())
def rd(off,n):
    out=b''
    for i in range(0,n,128): out+=ex.read(off+i,min(128,n-i))
    return out
lay=rd(0x10000,4096); obj=rd(0x13000,4096); props=rd(0x1186,512)
for y in range(18,26):
    print(y,' '.join('%02x:%02x%02x%s'%(lay[y*64+x],props[2*lay[y*64+x]],props[2*lay[y*64+x]+1],'*%02x'%obj[y*64+x] if obj[y*64+x] else '   ') for x in range(11,19)))
