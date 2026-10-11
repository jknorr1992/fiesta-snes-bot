"""Relaxed (monotone, position-free) distance-to-goal estimate: counts the events/maps that still have to be unlocked.
Nodes: event flags F(f), NPC flags N(s), maps M(m), events E(e). Hyperedges: AND of prerequisites -> target (+cost).
Knuth's generalisation of Dijkstra computes the minimum level of every node for a given set of flags and the current map."""
import heapq, sys
sys.path.insert(0, '.')

class Relaxed:
    def __init__(self, sim):
        self.S = sim; A = sim.A; R = sim.R; I = A.I
        nE = A.E.N
        self.nF, self.nN, self.nM = 512, 1024, 511
        self.oF, self.oN, self.oM, self.oE = 0, 512, 512 + 1024, 512 + 1024 + 511
        self.n = self.oE + nE
        he = []                               # (target, prereqs, addcost)
        for e in range(nE):
            f = A.eff[e]
            for fl in f.sets: he.append((self.oF + fl, [self.oE + e], 0))
            for s in f.npc_on: he.append((self.oN + s, [self.oE + e], 0))
            for m in f.loads:
                if m >= 0: he.append((self.oM + m, [self.oE + e], 0))
        for e, lst in I.src.items():
            for kind, m, x, y, conds, extra in lst:
                if kind == "call":
                    he.append((self.oE + e, [self.oE + m], 0)); continue
                if kind == "script":
                    mp = {9: 1, 12: 2, 16: 0}.get(extra)
                    if extra not in (9, 12, 14, 16, 254, 288, 17, 18): continue
                    pre = [self.oF + c[1] for c in conds if c[0] == "flag" and c[2]]
                    if mp is not None: pre.append(self.oM + mp)
                    he.append((self.oE + e, pre, 1)); continue
                pre = [self.oF + c[1] for c in conds if c[0] == "flag" and c[2]]
                if kind == "npc":
                    pre.append(self.oN + extra)
                if kind in ("npc", "trigger") and m >= 0: pre.append(self.oM + m)
                he.append((self.oE + e, pre, 1))
        for m in range(511):
            for en in R.entrances(m):
                he.append((self.oM + en["map"], [self.oM + m], 1))
                he.append((self.oM + m, [self.oM + en["map"]], 1))      # leaving a map by walking out (parent) / returning
        self.he = he
        self.users = [[] for _ in range(self.n)]
        for i, (t, pre, c) in enumerate(he):
            for p in pre: self.users[p].append(i)
        self.init_npc = sim.init_npcf
        self._cache = {}

    def levels(self, flags, curmap, npcf=None):
        key = (flags & self.S.read_mask, curmap, (npcf & self.S.npc_mask) if npcf is not None else None)
        r = self._cache.get(key)
        if r is not None: return r
        n = self.n
        INF = 10 ** 9
        dist = [INF] * n
        pq = []
        fl = flags
        while fl:                                           # set bits only
            low = fl & -fl; f = low.bit_length() - 1; fl ^= low
            if f < 512: dist[self.oF + f] = 0; pq.append((0, self.oF + f))
        nf = self.init_npc if npcf is None else npcf
        while nf:
            low = nf & -nf; s_ = low.bit_length() - 1; nf ^= low
            if s_ < 1024: dist[self.oN + s_] = 0; pq.append((0, self.oN + s_))
        dist[self.oM + curmap] = 0; pq.append((0, self.oM + curmap))
        # hyperedges with no prerequisites fire immediately
        if self.__dict__.get("_cnt0") is None:
            self._cnt0 = [len(h[1]) for h in self.he]
            self._free = [(t, c) for (t, pre, c) in self.he if not pre]
        cnt = list(self._cnt0); mx = [0] * len(self.he)
        for t, c in self._free:
            if c < dist[t]: dist[t] = c; pq.append((c, t))
        heapq.heapify(pq)
        done = [False] * n
        while pq:
            d, u = heapq.heappop(pq)
            if done[u] or d > dist[u]: continue
            done[u] = True
            for i in self.users[u]:
                cnt[i] -= 1
                if d > mx[i]: mx[i] = d
                if cnt[i] == 0:
                    t = self.he[i][0]; v = mx[i] + self.he[i][2]
                    if v < dist[t]: dist[t] = v; heapq.heappush(pq, (v, t))
        self._cache[key] = dist
        return dist

    def h_event(self, ev, flags, curmap, npcf=None):
        return self.levels(flags, curmap, npcf)[self.oE + ev]
    def h_flag(self, f, flags, curmap, npcf=None):
        return self.levels(flags, curmap, npcf)[self.oF + f]

if __name__ == "__main__":
    import time
    from sim import Sim
    S = Sim(); t = time.time(); Rl = Relaxed(S); print("build", time.time() - t, len(Rl.he))
    s = S.initial()
    t = time.time(); d = Rl.levels(s.flags, s.map, s.npcf); print("levels", time.time() - t)
    print("goal 1924 level", d[Rl.oE + 1924], "flag 23", d[Rl.oF + 23], "map 509", d[Rl.oM + 509])
