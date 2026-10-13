"""Pockets: connected regions of walkable tiles inside a local map, the nodes of the position-aware relaxed model.

Two tiles are in the same pocket when the party can walk between them on the map's base layer (no tile changes, no object
blockers; one-way moves and z-levels are ignored in the sense that a pocket is an undirected component of the standing-state
graph over all z-levels). Entrance and exit tiles are terminal: walking onto them leaves the map, so they never join pockets.
A tile change applied on top of the base layer can merge pockets: `merges` computes which."""
import sys
sys.path.insert(0, '.')
from localmap import LocalMap


class Pockets:
    def __init__(self, sim):
        self.S = sim; self.R = sim.R
        self._c = {}

    def _terminals(self, m):
        """tiles that are not part of a pocket: entrances, and tiles with an event trigger (walking onto one may fire an event; whether the
        party can pass through is decided by the relaxed model's gate nodes)"""
        t = {(e["x"], e["y"]) for e in self.R.entrances(m)}
        t |= {(x, y) for x, y, ts in self.R.event_triggers(m)}
        return t

    def label(self, m, mods=None):
        """-> (tile -> pocket id for every walkable non-terminal tile, {terminal tile: [pocket ids of walkable neighbours]})"""
        lm = LocalMap(self.R, m, mods)
        term = self._terminals(m)
        par = {}
        def find(a):
            while par[a] != a:
                par[a] = par[par[a]]; a = par[a]
            return a
        def union(a, b):
            a, b = find(a), find(b)
            if a != b: par[a] = b
        states = []
        for y in range(64):
            for x in range(64):
                b1, b2 = lm.props(x, y)
                if b2 == 0 or not b1 & 7: continue            # exit tile / nothing passable
                if (x, y) in term: continue
                if b1 & 4 or b1 & 3 == 3: zs = (1, 2)
                else: zs = (b1 & 3,)
                for z in zs:
                    st = (x, y, z); par[st] = st; states.append(st)
        neigh = {}
        for st in states:
            for d, nxt in lm.moves(*st):
                if nxt in par: union(st, nxt)
                else: neigh.setdefault((nxt[0], nxt[1]), set()).add(st)
        ids = {}; tile = {}
        for st in states:
            r = find(st)
            if r not in ids: ids[r] = len(ids)
            k = ids[r]
            if (st[0], st[1]) not in tile: tile[(st[0], st[1])] = k
        # a tile that is in two components (different z-levels) keeps its first; give the other levels as alternates
        alt = {}
        for st in states:
            k = ids[find(st)]
            if tile[(st[0], st[1])] != k: alt.setdefault((st[0], st[1]), set()).add(k)
        tn = {t: sorted({ids[find(s)] for s in ss}) for t, ss in neigh.items()}
        return tile, tn, alt, len(ids)

    def lm(self, m):
        return LocalMap(self.R, m)

    def get(self, m):
        r = self._c.get(m)
        if r is None: r = self._c[m] = self.label(m)
        return r


if __name__ == "__main__":
    import time
    from sim import Sim
    S = Sim(); P = Pockets(S)
    t = time.time()
    for m in (385, 386, 156, 364):
        tile, tn, alt, n = P.get(m); print(m, "pockets", n, "tiles", len(tile), "terminal", len(tn))
    print("sec", time.time() - t)
    t = time.time(); tot = 0
    for m in range(5, 511):
        try: tot += P.get(m)[3]
        except Exception as e: print("fail", m, e)
    print("all maps", tot, "pockets in", time.time() - t)
