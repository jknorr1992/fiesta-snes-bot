import sys,pickle,os,json
"""Writes data/reached_regions.json: the maps and world-map tiles on a simulated route (a pickled list of (state, action, state) from sim.explore.chain),
extended by a search to map 184 (Crescent). usage: make_reached.py CHAIN.pkl OUT.json   (env FFV_ROM = the ROM)"""
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from sim import *
from explore import search, chain
from relaxed import Relaxed
from bot import goal_fn, heuristic
S=Sim(); Rl=Relaxed(S)
ch=pickle.load(open(sys.argv[1],'rb'))
steps=[(p,a,n) for p,a,n in ch]
s=ch[-1][2]
goal,_=goal_fn('map:184'); h=heuristic(Rl,'map:184')
f,k=search(S,s,goal,max_exp=60000,wh=1.0,h=h,log=0)
steps+=chain(f)
DX={1:(0,-1),2:(1,0),3:(0,1),4:(-1,0)}
maps={}; world=set(); 
def addmap(m,i): maps.setdefault(m,[]).append(i)
for i,(p,a,n) in enumerate(steps):
    addmap(p.map,i); addmap(n.map,i)
    for st,(x0,y0) in ((p,(p.x,p.y)),):
        if p.map<5:
            x,y=x0,y0; mk=S.W.mod_key(p.map,p.flags); veh=p.veh
            tiles=[(x,y)]
            for d in (a.path or ()):
                x=(x+DX[d][0])&255; y=(y+DX[d][1])&255; tiles.append((x,y))
            for (x,y) in tiles: world.add((p.map,x,y,veh,i))
    if n.map<5 and p.map<5: world.add((n.map,n.x,n.y,n.veh,i))
out={"steps":len(steps),"route_events":sorted({a.event for p,a,n in steps if a.event is not None}),"local_maps":{str(m):sorted(set(v)) for m,v in maps.items() if m>=5},
     "world_maps":sorted({m for m in maps if m<5}),
     "world_tiles":sorted([list(w) for w in world])}
json.dump(out,open(sys.argv[2],'w'))
print(len(steps),len(out['local_maps']),len(world))
print(sorted(out['local_maps'],key=int))
