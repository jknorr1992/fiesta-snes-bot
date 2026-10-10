import sys, time, heapq
sys.path.insert(0, '.')
from sim import *

class Node:
    __slots__ = ("s", "parent", "act", "g")
    def __init__(self, s, parent, act, g): self.s, self.parent, self.act, self.g = s, parent, act, g

def sig(S, s, acts):
    return (S.key(s), frozenset((a.kind, (a.target[0].x, a.target[0].y, a.target[0].face if a.kind in ("press", "talk", "turn") else 0), a.event) for a in acts))

def search(S, s0, goal, max_exp=5000, wh=0.0, h=None, log=None, event_cost=10):
    """best-first over macro actions; dedupe on (key, action signature) at expansion time. Returns (goal Node or None, expansions)"""
    root = Node(s0, None, None, 0)
    pq = [(0, 0, root)]
    seen = set(); n = 0; cnt = 0
    t0 = time.time()
    while pq and n < max_exp:
        f, _, nd = heapq.heappop(pq)
        s = nd.s
        if goal(s): return nd, n
        acts = S.actions(s)
        sg = sig(S, s, acts)
        if sg in seen: continue
        seen.add(sg); n += 1
        if log and n % log == 0: print("exp", n, "pq", len(pq), "g", nd.g, "map", s.map, "flags", bin(s.flags & S.read_mask).count("1"), "t %.1f" % (time.time() - t0), flush=True)
        for a in acts:
            for o in S.execute(s, a):
                if o.key() == s.key() and (o.x, o.y) == (s.x, s.y): continue
                cnt += 1
                gg = nd.g + a.cost + event_cost
                hh = h(o) if h else 0
                heapq.heappush(pq, (gg + wh * hh, cnt, Node(o, nd, a, gg)))
    return None, n

def chain(nd):
    out = []
    while nd.parent is not None:
        out.append((nd.parent.s, nd.act, nd.s)); nd = nd.parent
    return out[::-1]
