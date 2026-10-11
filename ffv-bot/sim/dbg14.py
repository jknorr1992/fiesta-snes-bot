import sys,pickle; sys.path.insert(0,'.')
from run_plan import *
ch=pickle.load(open(sys.argv[1],'rb')); i=int(sys.argv[2])
S=Sim(); p,a,n=ch[i]
print(a.desc,a.path)
ex=Exec(S); ex.load_from("%s%d.state"%(CK,i-1),1); ex.cheat_party()
print(ex.fpos())
k,pos=ex.walk_dirs(a.path); print(k,pos)
ex.wait(30); print(ex.pos(), ex.u8(0xB57), ex.u8(0x18))
print(ex.shot())
