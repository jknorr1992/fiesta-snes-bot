"""Relaxed (monotone, position-free) distance-to-goal estimate: counts the events/maps that still have to be unlocked.
Nodes: event flags F(f), NPC flags N(s), maps M(m), events E(e). Hyperedges: AND of prerequisites -> target (+cost).
Knuth's generalisation of Dijkstra computes the minimum level of every node for a given set of flags and the current map.
Inside local maps the model is position-aware: pocket nodes P(m, k) are the connected walkable regions of a map (see pockets.py); events
need the pocket their trigger tile / NPC is in, entrances and map loads lead into the pocket of their arrival tile, and an event that
changes tiles merges the pockets the new tiles connect. The pocket tables depend only on the ROM and are cached per ROM hash
(directory FFV_CACHE, default ~/.cache/ffv-bot)."""

def _cache_dir():
    d = os.environ.get("FFV_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "ffv-bot")
    try: os.makedirs(d, exist_ok=True)
    except OSError: return None
    return d

import heapq, sys, os, hashlib, pickle
sys.path.insert(0, '.')
from pockets import Pockets

class Relaxed:
    CACHE_VERSION = 4

    def __init__(self, sim):
        self.S = sim; A = sim.A; R = sim.R; I = A.I
        nE = A.E.N
        self.PK = Pockets(sim)
        self._load_pockets()
        self.nF, self.nN, self.nM = 512, 1024, 511
        self.oF, self.oN, self.oM, self.oE = 0, 512, 512 + 1024, 512 + 1024 + 511
        self.oP0 = self.oE + nE
        self.oP = {}; o = self.oP0                         # first pocket node of every local map
        for m in range(5, 511):
            self.oP[m] = o; o += self.PK.get(m)[3]
        self.oG = o                                          # trigger-tile nodes, two per tile: G = walking over it fires nothing, S = the party can stand on it
        gate_defs = []                                       # (map, tile, neighbour nodes (pockets / other trigger tiles), [(flag conditions, event)])
        tidx = {}
        for m in range(5, 511):
            tile_, tn_, alt_, n_ = self.PK.get(m)
            ents = {(e["x"], e["y"]) for e in R.entrances(m)}
            trs = {(x, y): ts for x, y, ts in R.event_triggers(m) if (x, y) not in ents and ((x, y) in tn_)}
            for t, ts in trs.items():
                tidx[(m, t)] = len(gate_defs)
                gate_defs.append([m, t, None, [([(c[1], c[2]) for c in conds if c[0] == "flag"], ev) for conds, ev in R.trigger_script(ts)]])
        for gi, g in enumerate(gate_defs):                   # neighbours: the pockets next to the tile and the adjacent trigger tiles
            m, t = g[0], g[1]
            nb = [("P", self.oP[m] + k) for k in self.PK.get(m)[1][t]]
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                u = ((t[0] + dx) & 63, (t[1] + dy) & 63)
                if (m, u) in tidx: nb.append(("S", tidx[(m, u)]))
            g[2] = nb
        gate_defs = [tuple(g) for g in gate_defs]
        self.gate_defs = gate_defs
        o += 2 * len(gate_defs)
        self.n = o
        he = []                               # (target, prereqs, addcost)
        self.hdesc = []                       # what each hyperedge stands for, for explain()
        lab = [""]
        class _H(list):
            def append(h, t, d=self.hdesc): list.append(h, t); d.append(lab[0])
        he = _H()
        def pockets_at(m, x, y):
            """pocket nodes that contain / touch tile (x, y) of local map m"""
            if m not in self.oP: return []
            tile, tn, alt, n = self.PK.get(m)
            if (x, y) in tile: return [self.oP[m] + tile[(x, y)]] + [self.oP[m] + k for k in alt.get((x, y), ())]
            if (x, y) in tn: return [self.oP[m] + k for k in tn[(x, y)]]
            for r in (1, 2):                                # a tile that only becomes walkable through a tile change: the nearest pocket
                out = []
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        t = ((x + dx) & 63, (y + dy) & 63)
                        if t in tile and self.oP[m] + tile[t] not in out: out.append(self.oP[m] + tile[t])
                if out: return out
            return []
        def all_pockets(m): return [] if m not in self.oP else [self.oP[m] + k for k in range(self.PK.get(m)[3])]
        self.pockets_at = pockets_at
        for e in range(nE):
            f = A.eff[e]; lab[0] = "effect of event %d" % e
            for fl in f.sets: he.append((self.oF + fl, [self.oE + e], 0))
            for s_ in f.npc_on: he.append((self.oN + s_, [self.oE + e], 0))
            for (m, x, y) in self.final_places(e):
                if m < 0 or m >= 511: continue
                if m < 5 or x is None: he.append((self.oM + m, [self.oE + e], 0)); continue
                for t in (pockets_at(m, x, y) or all_pockets(m)): he.append((t, [self.oE + e], 0))
        for e, lst in I.src.items():
            for kind, m, x, y, conds, extra in lst:
                lab[0] = "event %d run by %s map %s (%s,%s) script %s" % (e, kind, m, x, y, extra)
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
                where = [self.oM + m] if m >= 0 else []
                if m >= 5:
                    if kind == "trigger": where = pockets_at(m, x, y) or where
                    else:                                                       # an NPC is talked to from a neighbouring tile
                        w = []
                        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                            for t in pockets_at(m, (x + dx) & 63, (y + dy) & 63):
                                if t not in w: w.append(t)
                        where = w or where
                for w in (where or [None]):
                    he.append((self.oE + e, pre + ([w] if w is not None else []), 1))
        for m in range(511):
            for en in R.entrances(m):
                lab[0] = "entrance map %d (%d,%d) -> map %d (%d,%d)" % (m, en["x"], en["y"], en["map"], en["dx"], en["dy"])
                dest = en["map"]
                src = [self.oM + m] if m < 5 else (pockets_at(m, en["x"], en["y"]) or all_pockets(m))
                if dest < 5: tgt = [self.oM + dest]
                else: tgt = pockets_at(dest, en["dx"] & 63, en["dy"] & 63) or all_pockets(dest)
                for t in tgt:
                    for s_ in src: he.append((t, [s_], 1))
                if dest >= 5:                                                  # leaving by an exit tile of the destination: back to the entrance
                    back = [self.oM + m] if m < 5 else (pockets_at(m, en["x"], en["y"]) or all_pockets(m))
                    for xp in self.exit_pockets(dest):
                        for t in back: he.append((t, [xp], 1))
        lab[0] = "pocket in map"
        for m in range(5, 511):
            for k in range(self.PK.get(m)[3]): he.append((self.oM + m, [self.oP[m] + k], 0))     # a map is reached when any of its pockets is
        for gi, (m, t, nb, groups) in enumerate(gate_defs):
            g = self.oG + 2 * gi; st = g + 1
            lab[0] = "trigger tile (%d,%d) of map %d" % (t[0], t[1], m)
            for conds, ev in groups: he.append((g, [self.oE + ev], 0))             # once its event has run the tile is walked over
            for kind, x in nb:
                src = x if kind == "P" else self.oG + 2 * x + 1
                he.append((st, [src, g], 0))                                       # stand on the tile after reaching a neighbour
                if kind == "P": he.append((x, [st], 0))                            # and step off it into the pocket
        self._gate_cond = [(self.oG + 2 * gi, [g[0] for g in groups]) for gi, (m, t, nb, groups) in enumerate(gate_defs)]
        for (e, m, groups) in self.merges:                                      # a tile change connects pockets
            lab[0] = "tile change of event %d joins pockets of map %d" % (e, m)
            for g in groups:
                for a in g:
                    for b in g:
                        if a != b: he.append((self.oP[m] + b, [self.oE + e, self.oP[m] + a], 0))
        self.he = list(he)
        self.users = [[] for _ in range(self.n)]
        for i, (t, pre, c) in enumerate(he):
            for p in pre: self.users[p].append(i)
        self.init_npc = sim.init_npcf
        self._cache = {}

    def final_places(self, e):
        """where the party stands after event e (all branches): (map, x, y) of the end states that moved to another map, from the event VM
        (an event can load several maps for scenes; only the last one counts). Falls back to the loads found by the static scan."""
        c = self.__dict__.setdefault("_fin", {})
        r = c.get(e)
        if r is None:
            try:
                from vm import State
                s0 = State(); s0.map = 511; s0.x = 20; s0.y = 20; s0.npcf = self.S.init_npcf
                r = sorted({(o.map, o.x, o.y) for o in self.S.V.run(s0, e) if o.map != 511})
            except Exception:
                r = [p for p in self.S.A.eff[e].loadpos if p is not None]
            c[e] = r
        return r

    # ------------------------------------------------------------------ pocket tables (ROM-only data, cached per ROM hash)
    def _load_pockets(self):
        rom = self.S.R.r
        d = _cache_dir()
        path = None
        if d:
            h = hashlib.sha1(rom).hexdigest()
            path = os.path.join(d, "pockets-%s-v%d.pkl" % (h[:16], self.CACHE_VERSION))
            try:
                with open(path, "rb") as fh: pk, self.merges = pickle.load(fh)
                self.PK._c = pk; return
            except Exception: pass
        for m in range(5, 511): self.PK.get(m)
        self.merges = self._compute_merges()
        if path:
            try:
                with open(path + ".tmp", "wb") as fh: pickle.dump((self.PK._c, self.merges), fh)
                os.replace(path + ".tmp", path)
            except OSError: pass

    def exit_pockets(self, m):
        """pockets of local map m that touch an exit tile"""
        c = self.__dict__.setdefault("_exitp", {})
        r = c.get(m)
        if r is None:
            tile, tn, alt, n = self.PK.get(m)
            lm = self.PK.lm(m)
            ks = set()
            for t, ps in tn.items():
                if lm.is_exit(*t): ks |= set(ps)
            r = c[m] = [self.oP[m] + k for k in sorted(ks)]
        return r

    def _compute_merges(self):
        """[(event, map, [groups of pocket ids that a tile change of the event joins])]; the map is the one of the triggers / NPCs that run
        the event (the party is on the trigger tile for relative tile changes) or that the event loads before changing tiles"""
        A = self.S.A; I = A.I
        tops = {}
        def sources(e, seen):
            if e in tops: return tops[e]
            out = set()
            if e in seen: return out
            seen = seen | {e}
            for kind, m, x, y, conds, extra in I.src.get(e, ()):
                if kind == "call": out |= sources(m, seen)
                elif kind in ("trigger", "npc") and m >= 5: out.add((m, x, y))
            tops[e] = out
            return out
        res = []; done = {}
        for e in range(A.E.N):
            d = A._direct[e]
            if not d.modcmds: continue
            places = set(sources(e, frozenset()))
            for (m, x, y) in d.loadpos:
                if m >= 5 and x is not None: places.add((m, x, y))
            for (m, x, y) in places:
                mods = {}
                for op, b in d.modcmds:
                    w, h = (b[3] & 15) + 1, (b[3] >> 4) + 1
                    if op == 0xF3: x0, y0 = b[1] & 0x3F, b[2]
                    else: x0, y0 = (b[1] & 0x3F) + x, (b[2] + y) & 255
                    for j in range(h):
                        for i in range(w): mods[((x0 + i) & 63, (y0 + j) & 63)] = b[4 + j * w + i]
                key = (m, tuple(sorted(mods.items())))
                if key not in done:
                    tile, tn, alt, n = self.PK.get(m)
                    t2, tn2, alt2, n2 = self.PK.label(m, mods)
                    grp = {}
                    for t, k in tile.items():
                        k2 = t2.get(t)
                        if k2 is not None: grp.setdefault(k2, set()).add(k)
                    for t, k in t2.items():                               # new floor next to a base pocket
                        if t not in tile:
                            for ddx, ddy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                                nb = ((t[0] + ddx) & 63, (t[1] + ddy) & 63)
                                if nb in tile: grp.setdefault(k, set()).add(tile[nb])
                    done[key] = [sorted(g) for g in grp.values() if len(g) > 1]
                if done[key]: res.append((e, m, done[key]))
        return res

    def levels(self, flags, curmap, npcf=None, pos=None, via=None):
        pk = None
        if pos is not None and curmap >= 5:
            pk = tuple(self.pockets_at(curmap, pos[0], pos[1]))
        key = (flags & self.S.read_mask, curmap, (npcf & self.S.npc_mask) if npcf is not None else None, pk)
        r = self._cache.get(key) if via is None else None
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
        for g, glist in self._gate_cond:                    # a trigger tile is open when no group of its script fires under the flags now
            for conds in glist:
                for f, v in conds:
                    if bool(flags >> f & 1) != v: break
                else: break
            else: dist[g] = 0; pq.append((0, g))
        if curmap >= 5:
            for t in (pk if pk is not None else [self.oP[curmap] + k for k in range(self.PK.get(curmap)[3])]):
                dist[t] = 0; pq.append((0, t))
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
                    if v < dist[t]:
                        dist[t] = v; heapq.heappush(pq, (v, t))
                        if via is not None: via[t] = i
        if via is None: self._cache[key] = dist
        return dist

    def name(self, u):
        if u < self.oN: return "flag %d" % u
        if u < self.oM: return "npcflag %d" % (u - self.oN)
        if u < self.oE: return "map %d" % (u - self.oM)
        if u < self.oP0: return "event %d" % (u - self.oE)
        if u >= self.oG:
            m, t = self.gate_defs[(u - self.oG) // 2][:2]; return "%s tile %s of map %d" % ("gate" if (u - self.oG) % 2 == 0 else "standing on", t, m)
        for m, o in self.oP.items():
            if u < self.oG and o <= u < o + self.PK.get(m)[3]: return "pocket %d of map %d (e.g. tile %s)" % (u - o, m, next((t for t, k in self.PK.get(m)[0].items() if k == u - o), None))
        return "node %d" % u

    def explain(self, flags, curmap, npcf, pos, target, depth=0, via=None, seen=None):
        """print the relaxed derivation of `target` (a node id)"""
        if via is None:
            via = {}; self.levels(flags, curmap, npcf, pos, via=via); seen = set()
        d = self.levels(flags, curmap, npcf, pos)
        pad = "  " * depth
        if target in seen: print(pad + self.name(target) + " (above)"); return
        seen.add(target)
        i = via.get(target)
        print("%s%s  level %s%s" % (pad, self.name(target), d[target], "" if i is not None else "  [given]"))
        if i is None: return
        print(pad + "  by " + self.hdesc[i])
        for p in self.he[i][1]: self.explain(flags, curmap, npcf, pos, p, depth + 1, via, seen)

    def h_event(self, ev, flags, curmap, npcf=None, pos=None):
        return self.levels(flags, curmap, npcf, pos)[self.oE + ev]
    def h_flag(self, f, flags, curmap, npcf=None, pos=None):
        return self.levels(flags, curmap, npcf, pos)[self.oF + f]
    def h_pos(self, m, x, y, flags, curmap, npcf=None, pos=None):
        d = self.levels(flags, curmap, npcf, pos)
        return min([d[t] for t in self.pockets_at(m, x, y)] or [d[self.oM + m]])

if __name__ == "__main__":
    import time
    from sim import Sim
    S = Sim(); t = time.time(); Rl = Relaxed(S); print("build", time.time() - t, len(Rl.he))
    s = S.initial()
    t = time.time(); d = Rl.levels(s.flags, s.map, s.npcf); print("levels", time.time() - t)
    print("goal 1924 level", d[Rl.oE + 1924], "flag 23", d[Rl.oF + 23], "map 509", d[Rl.oM + 509])
