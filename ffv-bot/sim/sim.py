"""FFV game simulator for route planning: exact tile movement (localmap/world), triggers, entrances, NPC talks and the event VM.

A *macro action* is something the player can do from a settled position: walk to a tile that fires something (event trigger,
entrance, map exit, vehicle), or stand somewhere and press A (NPC, button trigger, board/land a vehicle).  Every action yields
the resulting States (several when the event branches).  Walking paths are recomputed on demand (`path_to`).
"""
import collections, heapq, functools, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from rom import Rom, DEFAULT_ROM
from vm import VM, State, FACE_DX
from localmap import LocalMap, DIRS as LDIRS
from world import World, DIRS as WDIRS, LAND_BIT
from analysis import Analysis

WORLD_HOOKS = [   # (map, vehicle test, x range, y range, trigger script) from the field main loop
    (1, lambda v: True, (0x9F, 0xA1), (0xA1, 0xA1), 9),
    (0, lambda v: v == 6, (0x3D, 0x42), (0x9E, 0xA4), 16),
    (2, lambda v: v >= 5, (0xB6, 0xB9), (0x87, 0x8A), 12),
]
FACE_OF_DIR = {1: 0, 2: 1, 3: 2, 4: 3}      # walking direction (1 up, 2 right, 3 down, 4 left) -> $0adb facing
DIR_OF_FACE = {v: k for k, v in FACE_OF_DIR.items()}

class Action:
    __slots__ = ("kind", "desc", "cost", "target", "face", "event", "path", "stand", "npc")
    def __init__(self, kind, desc, cost, target, face=None, event=None):
        self.kind, self.desc, self.cost, self.target, self.face, self.event = kind, desc, cost, target, face, event
        self.path = None     # walking directions (1 up 2 right 3 down 4 left) from the current position to the standing tile
        self.stand = None    # direction to press at the end to turn without moving (None = none)
        self.npc = None      # talk to a wandering NPC: its object index (the walk to it is repeated live until it stands where the talk needs)
    def __repr__(self): return "<%s %s cost=%s>" % (self.kind, self.desc, self.cost)

DMG_COST = 6                                            # extra path cost of stepping on a damage floor tile (tile property byte 1 bit $20)

class Sim:
    OLD_MERGE = bool(os.environ.get("FFV_OLD_MERGE"))
    def __init__(self, rom_path=None):
        self.R = Rom(rom_path)
        self.W = World(self.R.r)
        self.V = VM(self.R)
        self.init_npcf = sum(1 << i for i in self.R.initial_npc_flags())
        self._local_cache = {}
        self.A = Analysis()
        # flags that any trigger / NPC script / world hook / world-map tile change reads
        self.read_mask = 0
        for f in self.A.read_flags(): self.read_mask |= 1 << f
        for w in range(5):
            for f, _, _, _ in self.W.mods[w]: self.read_mask |= 1 << f
        for ts in (9, 12, 14, 16, 254, 288):
            for conds, ev in self.R.trigger_script(ts):
                for c in conds:
                    if c[0] == "flag": self.read_mask |= 1 << c[1]
        self.npc_mask = 0
        for m in range(511):
            for n in self.R.npcs(m): self.npc_mask |= 1 << n["script"]
        self._rel = {}

    def relevant(self, ev):
        """does running event `ev` change anything that can matter (read flags, NPCs, maps, vehicles, tiles, party position)?"""
        r = self._rel.get(ev)
        if r is None:
            f = self.A.eff[ev]
            r = bool(f.loads or f.veh or f.mods or f.steps or f.ends or f.gameover or
                     any(self.read_mask >> n & 1 for n in f.sets | f.clears) or
                     any(self.npc_mask >> n & 1 for n in f.npc_on | f.npc_off))
            self._rel[ev] = r
        return r

    def key(self, s):
        return (s.flags & self.read_mask, s.npcf & self.npc_mask, s.map, s.veh, tuple(map(tuple, s.vtab)),
                tuple(sorted(s.mods.items())), tuple(sorted(s.npcpos.items())), tuple(sorted(s.npcvis.items())), s.parent, s.ended)

    # ------------------------------------------------------------------ conditions
    def ram(self, s, addr):
        a = 0x500 + addr
        if a == 0xADC: return s.veh
        if 0xADD <= a < 0xADD + 24: return s.vtab[(a - 0xADD) >> 2][(a - 0xADD) & 3]
        return None                                       # gil, jobs, ... not modelled

    def cond_ok(self, s, conds, face=None, button=False):
        for c in conds:
            k = c[0]
            if k == "flag":
                if s.flag(c[1]) != c[2]: return False
            elif k == "facing":
                if face != c[1]: return False
            elif k == "button":
                if not button: return False
            elif k in ("ram1", "ram2"):
                v = self.ram(s, c[2])
                if v is None: return False
                op, mode, val = c[1], c[3], c[4]
                if op in (0xF8, 0xF5):
                    if not v & val: return False
                elif mode == 0:
                    if v != val: return False
                elif mode == 1:
                    if not v >= val: return False
                elif not v < val: return False
            else:
                return False
        return True

    def pick(self, s, groups, face=None, button=False):
        """first group whose conditions hold -> event id (0 = nothing), or None if none matches"""
        for conds, ev in groups:
            if self.cond_ok(s, conds, face, button): return ev if (ev == 0 or self.relevant(ev)) else 0
        return None

    # ------------------------------------------------------------------ per-map tables
    @functools.lru_cache(maxsize=None)
    def trig_at(self, m):
        out = {}
        for x, y, ts in self.R.event_triggers(m):
            out.setdefault((x, y), ts)                    # CheckTriggers only looks at the first entry of a tile
        return out

    @functools.lru_cache(maxsize=None)
    def ent_at(self, m):
        out = {}
        for e in self.R.entrances(m):
            out.setdefault((e["x"], e["y"]), e)
        return out

    def objects(self, s):
        """visible NPC objects of the current local map: list of (index, script, x, y, stationary)"""
        out = []
        for i, n in enumerate(self.R.npcs(s.map)):
            vis = s.npcvis.get(i)
            if vis is None: vis = s.npc_flag(n["script"])
            if not vis: continue
            x, y = s.npcpos.get(i, (n["x"], n["y"]))
            out.append((i, n["script"], x, y, bool(n["flags"] & 4)))
        return out

    def localmap(self, s):
        blocked = {(x, y) for _, _, x, y, st in self.objects(s) if st}
        key = (s.map, tuple(sorted(s.mods.items())), frozenset(blocked))
        lm = self._local_cache.get(key)
        if lm is None:
            if len(self._local_cache) > 400: self._local_cache.clear()
            lm = LocalMap(self.R, s.map, s.mods, blocked)
            self._local_cache[key] = lm
        return lm

    # ------------------------------------------------------------------ states
    def initial(self):
        """state when the player first gets control (new game: event 16 = intro)"""
        s = State(); s.npcf = self.init_npcf
        outs = self.V.run(s, 16)
        assert len(outs) == 1
        return outs[0]

    def with_z(self, s):
        if s.map >= 5:
            lm = self.localmap(s)
            s.z = lm.z_after(lm.props(s.x, s.y)[0], s.z)
        return s

    # ------------------------------------------------------------------ arrival processing (triggers that fire when standing on a tile)
    def arrive(self, s, stepped, depth=0):
        """Process everything the game does once the party stands still at s: event trigger, entrance, map exit, vehicle boarding,
        world hooks. Returns a list of resulting settled states."""
        if s.ended or s.gameover or depth > 8: return [s]
        if s.map >= 5: return self._arrive_local(s, stepped, depth)
        return self._arrive_world(s, stepped, depth)

    def _ride(self, o):
        """an event can leave the party on an auto-move (conveyor) tile: the engine keeps pushing it until it leaves them.
        -> (state, entrance_taken_at_the_end)"""
        if o.map < 5 or o.ended: return o, False
        lm = self.localmap(o)
        rode = False
        for _ in range(300):
            if not lm.forced(o.x, o.y): break
            r = lm.moves(o.x, o.y, o.z)
            if not r: break
            d, (nx, ny, nz) = r[0]
            if self.trig_at(o.map).get((nx, ny)) is not None or lm.is_exit(nx, ny): break
            o = State.clone(o); o.x, o.y, o.z = nx, ny, nz; o.face = FACE_OF_DIR[d]; rode = True
            if self.ent_at(o.map).get((nx, ny)) is not None: return o, True
        return o, False

    def _run_after(self, s, ev, depth):
        res = []
        for o, ent in map(self._ride, self.V.run(s, ev)):
            if ent: res += self._take_entrance(o, self.ent_at(o.map)[(o.x, o.y)], depth)
            elif o.key() == s.key(): res.append(o)
            else: res += self.arrive(self.with_z(o), False, depth + 1)
        return res

    def _arrive_local(self, s, stepped, depth):
        ts = self.trig_at(s.map).get((s.x, s.y))
        if ts is not None:
            ev = self.pick(s, self.R.trigger_script(ts), s.face, False)
            if ev:
                outs = self._run_after(s, ev, depth)
                e = self.ent_at(s.map).get((s.x, s.y)) if stepped else None
                if e is None: return outs
                res = []                                   # CheckTriggers runs the entrance check after the event trigger unless $59 is set
                for o in outs:
                    if o.map == s.map and (o.x, o.y) == (s.x, s.y) and not o.ended: res += self._take_entrance(o, e, depth)
                    else: res.append(o)
                return res
        if stepped:
            e = self.ent_at(s.map).get((s.x, s.y))
            if e is not None: return self._take_entrance(s, e, depth)
        lm = self.localmap(s)
        if lm.is_exit(s.x, s.y) and stepped:
            return self._load_parent(s, depth)
        return [s]

    def _take_entrance(self, s, e, depth):
        n = s.clone()
        if s.map < 5: n.parent = (s.map, s.x, s.y)
        m = e["map"]
        if m < 5: self.V.load_map(n, m, e["dx"], e["dy"], n.face)
        else: self.V.load_map(n, m, e["dx"] & 0x3F, e["dy"] & 0x3F, e["dx"] >> 6)
        return self.arrive(self.with_z(n), False, depth + 1)

    def _load_parent(self, s, depth):
        n = s.clone()
        m, x, y = n.parent
        self.V.load_map(n, m, x, y, n.face)
        ev = self.pick(n, self.R.trigger_script(14))
        if ev: return self._run_after(n, ev, depth)
        return self.arrive(n, False, depth + 1)

    def _arrive_world(self, s, stepped, depth):
        allowed = s.veh in (0, 1, 5) or s.map >= 3        # CheckTriggers: only maps 0-2 skip triggers for other vehicles
        if allowed:
            ts = self.trig_at(s.map).get((s.x, s.y))
            if ts is not None:
                ev = self.pick(s, self.R.trigger_script(ts), s.face, False)
                if ev: return self._run_after(s, ev, depth)
            if stepped:
                e = self.ent_at(s.map).get((s.x, s.y))
                if e is not None: return self._take_entrance(s, e, depth)
        if s.veh == 0:                                    # boarding: stand on a visible vehicle (ship, submarine board by themselves)
            for i in range(6):
                v = s.vtab[i]
                if v[1] & 0x80 or (v[0] >> 5) != s.map or (v[2], v[3]) != (s.x, s.y): continue
                typ = (v[0] & 0x1C) >> 2
                if typ in (4, 5):
                    n = s.clone(); n.veh = i + 1; n.vtab[i][1] |= 0x80
                    ev = self.pick(n, self.R.trigger_script(0xFE))      # BoardShip -> trigger script 254
                    if typ == 5 and ev: return self._run_after(n, ev, depth)
                    return self.arrive(n, False, depth + 1)
        for m, vt, (x0, x1), (y0, y1), ts in WORLD_HOOKS:
            if s.map == m and vt(s.veh) and x0 <= s.x <= x1 and y0 <= s.y <= y1:
                ev = self.pick(s, self.R.trigger_script(ts))
                if ev: return self._run_after(s, ev, depth)
        return [s]

    @staticmethod
    def _backtrack(par, node):
        p = []
        while par.get(node):
            node, d = par[node][0], par[node][1]
            p.append(d)
        return p[::-1]

    # ------------------------------------------------------------------ actions: local maps
    def actions(self, s):
        return self._local_actions(s) if s.map >= 5 else self._world_actions(s)

    def _arrival_kind(self, s, m, x, y, d):
        """what happens when walking onto tile (x, y) in direction d (facing derived): ('event', ev) | ('entrance', e) | ('exit',) | None"""
        face = FACE_OF_DIR[d]
        ts = self.trig_at(m).get((x, y))
        if ts is not None:
            ev = self.pick(s, self.R.trigger_script(ts), face, False)
            if ev: return ("event", ev)
        return None

    def _local_actions(self, s):
        lm = self.localmap(s)
        acts = []
        start = (s.x, s.y, s.z)
        ents = self.ent_at(s.map)
        f = self._local_flood(lm, start, s.map)
        dist = f[0]; par = f[1]; own = False                    # the cached flood is shared: copied only when a plain-floor trigger tile extends it
        terminal = {}; tcost = {}
        base = f[2]; bi = 0; nb = len(base)                      # flood arrivals come sorted by cost; extras (past plain-floor triggers) go in a heap
        extra = []; wn = 0
        while bi < nb or extra:
            if extra and (bi >= nb or extra[0][0] < dist[base[bi][0]] + base[bi][3]):
                _, _, (cur, d, nxt, step) = heapq.heappop(extra)
            else:
                cur, d, nxt, step = base[bi]; bi += 1
            nx, ny, nz = nxt
            key = (nx, ny, d)
            if key in terminal: continue
            kind = self._arrival_kind(s, s.map, nx, ny, d)
            e = ents.get((nx, ny))
            c = dist[cur] + step
            if kind or e is not None or lm.is_exit(nx, ny):
                terminal[key] = (cur, kind, e); tcost[key] = c
                continue
            if nxt in dist and dist[nxt] <= c: continue
            if not own: dist = dict(dist); par = dict(par); own = True
            dist[nxt] = c; par[nxt] = (cur, d)          # a trigger tile that does not fire now is plain floor: go on through it
            f2 = self._local_flood(lm, nxt, s.map)
            for node, dd in f2[0].items():
                if node != nxt and c + dd < dist.get(node, 1 << 30): dist[node] = c + dd; par[node] = f2[1][node]
            for it in f2[2]: wn += 1; heapq.heappush(extra, (dist[it[0]] + it[3], wn, it))
        # terminal tiles: walking onto them fires something
        for (nx, ny, d), (cur, kind, e) in terminal.items():
            tgt = State.clone(s); tgt.x, tgt.y, tgt.face = nx, ny, FACE_OF_DIR[d]
            tgt.z = lm.z_after(lm.props(nx, ny)[0], cur[2])
            what = kind[0] if kind else ("entrance" if e is not None else "exit")
            desc = "walk to (%d,%d) on map %d: %s %s" % (nx, ny, s.map, what, kind[1] if kind else (("-> map %d" % e["map"]) if e is not None else ""))
            acts.append(Action("walk" + what, desc, tcost[(nx, ny, d)], (tgt, True), face=d, event=kind[1] if kind else None))
            acts[-1].path = self._backtrack(par, cur) + [d]
        # interactions from plain tiles
        objs = self.objects(s)
        by_pos = {}
        for i, script, ox, oy, st in objs: by_pos.setdefault((ox, oy), []).append((i, script))
        seen_npc = set()
        for node, dd in list(dist.items()):
            if node[0] == "T": continue
            x, y, z = node
            arrive_face = None
            # button / facing triggers on this tile
            ts = self.trig_at(s.map).get((x, y))
            if ts is not None:
                groups = self.R.trigger_script(ts)
                for face in range(4):
                    if not self._can_face(lm, node, par, face, s): continue
                    for button in (False, True):
                        ev = self.pick(s, groups, face, button)
                        if ev and (button or True):
                            if not button and self._arrival_kind(s, s.map, x, y, DIR_OF_FACE[face]): continue
                            tgt = State.clone(s); tgt.x, tgt.y, tgt.z, tgt.face = x, y, z, face
                            acts.append(Action("press" if button else "turn", "stand (%d,%d) map %d facing %d%s: event %d" % (x, y, s.map, face, " +A" if button else "", ev), dd + 1, (tgt, False), face, ev))
                            acts[-1].path = self._backtrack(par, node)
                            acts[-1].stand = DIR_OF_FACE[face] if lm.step(x, y, z, DIR_OF_FACE[face]) is None else None
            # NPC talk
            for d in (1, 2, 3, 4):
                dx, dy = LDIRS[d]
                tx, ty = (x + dx) & 63, (y + dy) & 63
                cands = by_pos.get((tx, ty), [])
                if not cands and lm.props(tx, ty)[1] == 0xFF:        # through-tile (counter): talk to the object behind it
                    cands = by_pos.get(((x + 2 * dx) & 63, (y + 2 * dy) & 63), [])
                for i, script in cands:
                    ev = self.pick(s, self.R.npc_script(script), FACE_OF_DIR[d], True)
                    if ev:
                        tgt = State.clone(s); tgt.x, tgt.y, tgt.z, tgt.face = x, y, z, FACE_OF_DIR[d]
                        acts.append(Action("talk", "talk to npc %d (script %d) from (%d,%d) map %d: event %d" % (i, script, x, y, s.map, ev), dd + 1, (tgt, False), FACE_OF_DIR[d], ev))
                        acts[-1].path = self._backtrack(par, node); acts[-1].stand = d
                        if not (self.R.npcs(s.map)[i]["flags"] & 4): acts[-1].npc = i
        # Karnak cell (map 138): the prisoner NPC (script 342) is out of reach until the cells open by themselves after a while;
        # then talking to him plays event 107 and releases the party. Modelled as a wait + talk at the start of the pocket.
        if s.map == 138:
            for i, script, ox, oy, st in objs:
                if script == 342:
                    ev = self.pick(s, self.R.npc_script(script), 0, True)
                    if ev and not any(a.kind == "wait" for a in acts):
                        tgt = State.clone(s)
                        acts.append(Action("wait", "wait in the Karnak cell until the prisoner (npc %d) can be reached, talk: event %d" % (i, ev), 600, (tgt, False), None, ev))
                        acts[-1].path = []
        return acts

    def _barriers(self, m):
        """tiles of local map m where walking onto them can fire something (event triggers, entrances)"""
        cache = self.__dict__.setdefault("_barrier_cache", {})
        b = cache.get(m)
        if b is None: b = cache[m] = frozenset(self.trig_at(m)) | frozenset(self.ent_at(m))
        return b

    def _local_flood(self, lm, start, m):
        """cached on the LocalMap: cheapest costs to every standing state (x, y, z) reachable from `start` without stepping on a tile
        that can fire something (those arrivals are returned for the per-state flag check): (dist, par, arrivals)"""
        fl = lm.__dict__.setdefault("_fl", {})
        r = fl.get(start)
        if r is not None: return r
        bar = self._barriers(m)
        dist = {start: 0}; par = {start: None}; done = set(); arr = []; seen = set()
        heap = [(0, 0, start)]; n = 0
        while heap:
            dc, _, cur = heapq.heappop(heap)
            if cur in done: continue
            done.add(cur)
            for d, nxt in lm.moves(*cur):
                nx, ny, nz = nxt
                step = 1 + (DMG_COST if lm.props(nx, ny)[0] & 0x20 else 0)      # damage floor (poison/tile damage): walk around it when a detour is cheaper
                if (nx, ny) in bar or lm.is_exit(nx, ny):
                    if (nx, ny, d) not in seen: seen.add((nx, ny, d)); arr.append((cur, d, nxt, step))
                    continue
                if nxt in done or dist.get(nxt, 1 << 30) <= dc + step: continue
                dist[nxt] = dc + step; par[nxt] = (cur, d); n += 1; heapq.heappush(heap, (dc + step, n, nxt))
        if len(fl) > 300: fl.clear()
        r = fl[start] = (dist, par, arr)
        return r

    def _can_face(self, lm, node, par, face, s):
        """can the party stand on `node` facing `face`? (turn by bumping into a blocked neighbour, or arrive moving that way)"""
        x, y, z = node
        d = DIR_OF_FACE[face]
        if lm.step(x, y, z, d) is None: return True
        p = par.get(node)
        return bool(p and p[1] == d)

    # ------------------------------------------------------------------ actions: world map
    def vtype(self, s, slot):
        return (s.vtab[slot][0] & 0x1C) >> 2

    def _hook_tiles(self, s):
        out = set()
        for m, vt, (x0, x1), (y0, y1), ts in WORLD_HOOKS:
            if s.map == m and vt(s.veh):
                out |= {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
        return out

    def _world_arrival_kind(self, s, vt, x, y, d, vehicle_tiles):
        """('event'|'entrance'|'board'|'hook', info) if arriving at world tile (x, y) walking in direction d fires something"""
        face = FACE_OF_DIR[d]
        if s.veh in (0, 1, 5) or s.map >= 3:
            ts = self.trig_at(s.map).get((x, y))
            if ts is not None:
                ev = self.pick(s, self.R.trigger_script(ts), face, False)
                if ev: return ("event", ev)
            e = self.ent_at(s.map).get((x, y))
            if e is not None: return ("entrance", e)
        if s.veh == 0 and (x, y) in vehicle_tiles: return ("board", vehicle_tiles[(x, y)])
        for m, vtest, (x0, x1), (y0, y1), ts in WORLD_HOOKS:
            if s.map == m and vtest(s.veh) and x0 <= x <= x1 and y0 <= y <= y1:
                ev = self.pick(s, self.R.trigger_script(ts))
                if ev: return ("hook", ev)
        return None

    def _world_actions(self, s):
        w = s.map
        mk = self.W.mod_key(w, s.flags)
        vt = 0 if s.veh == 0 else self.vtype(s, s.veh - 1)
        vehicle_tiles = {}
        for i in range(6):
            v = s.vtab[i]
            if not v[1] & 0x80 and (v[0] >> 5) == w and (v[0] & 0x1C) >> 2 in (4, 5): vehicle_tiles[(v[2], v[3])] = i
        acts = []
        start = (s.x, s.y)
        f = self._world_flood(w, vt, mk, frozenset(vehicle_tiles) if vt == 0 else frozenset(), start)
        dist = f[0]; par = f[1]; own = False
        terminal = {}
        work = []; wn = 0
        for it in f[2]: wn += 1; heapq.heappush(work, (dist[it[0]], wn, it))
        while work:
            _, _, (cur, d, npos, dis) = heapq.heappop(work)
            if (npos, d) in terminal or npos in dist: continue
            if dis:       # disembark from ship/submarine onto land
                terminal[(npos, d)] = (cur, ("disembark", None)); continue
            kind = self._world_arrival_kind(s, vt, npos[0], npos[1], d, vehicle_tiles)
            if kind:
                terminal[(npos, d)] = (cur, kind); continue
            c = dist[cur] + 1                                   # a trigger tile that does not fire now is plain ground: go on through it
            if not own: dist = dict(dist); par = dict(par); own = True
            dist[npos] = c; par[npos] = (cur, d)
            if self.OLD_MERGE:
                f2 = self._world_flood(w, vt, mk, frozenset(vehicle_tiles) if vt == 0 else frozenset(), npos)
                for node, dd in f2[0].items():
                    if node != npos and c + dd < dist.get(node, 1 << 30): dist[node] = c + dd; par[node] = f2[1][node]
                for it in f2[2]: wn += 1; heapq.heappush(work, (dist[it[0]], wn, it))
                continue
            # the same merge without flooding the whole piece again: spread from npos only over tiles whose distance improves
            # (a tile that is not improved keeps a distance from which everything behind it is already at least as close)
            sc = self.__dict__.setdefault("_wstep_cache", {}).setdefault((w, vt, mk), {})
            bar = self._world_barriers(w); vts = frozenset(vehicle_tiles) if vt == 0 else frozenset()
            q = collections.deque([npos]); seen2 = set()
            while q:
                cu = q.popleft(); dc = dist[cu] + 1
                for d2 in (1, 2, 3, 4):
                    dx, dy = WDIRS[d2]
                    np2 = ((cu[0] + dx) & 255, (cu[1] + dy) & 255)
                    bd = np2 in vts
                    k2 = (cu[0], cu[1], d2, bd)
                    r2 = sc.get(k2, 0)
                    if r2 == 0: r2 = sc[k2] = self.W.step(w, vt, cu[0], cu[1], d2, mk, bd)
                    if r2 is None: continue
                    if r2[2]:
                        if (np2, d2) not in seen2: seen2.add((np2, d2)); wn += 1; heapq.heappush(work, (dist[cu], wn, (cu, d2, np2, True)))
                        continue
                    if np2 in bar or np2 in vts:
                        if (np2, d2) not in seen2: seen2.add((np2, d2)); wn += 1; heapq.heappush(work, (dist[cu], wn, (cu, d2, np2, False)))
                        continue
                    if dc < dist.get(np2, 1 << 30): dist[np2] = dc; par[np2] = (cu, d2); q.append(np2)
        if vt in (4, 5) or s.veh:     # keep the cheapest disembark per connected piece of land (ship stays at that dock)
            comp = self._foot_comp(w, mk); best = {}
            hot = self._hot_comps(s, w, comp)
            for key, (cur, kind) in terminal.items():
                if kind[0] != "disembark": continue
                c = comp[key[0][1] * 256 + key[0][0]]
                if c not in hot: continue
                if c not in best or dist[cur] < dist[terminal[best[c]][0]]: best[c] = key
            keep = set(best.values())
            terminal = {k: v for k, v in terminal.items() if v[1][0] != "disembark" or k in keep}
        for (npos, d), (cur, kind) in terminal.items():
            tgt = State.clone(s); tgt.x, tgt.y, tgt.face = npos[0], npos[1], FACE_OF_DIR[d]
            if kind[0] == "disembark":
                slot = s.veh - 1
                tgt.vtab[slot] = [w << 5 | (tgt.vtab[slot][0] & 0x1F), 0x00, cur[0], cur[1]]
                tgt.veh = 0
            acts.append(Action("walk" + kind[0], "world %d walk to (%d,%d): %s %s" % (w, npos[0], npos[1], kind[0], kind[1] if kind[0] in ("event", "hook") else (("-> map %d" % kind[1]["map"]) if kind[0] == "entrance" else "")),
                               dist[cur] + 1, (tgt, True), d, kind[1] if kind[0] in ("event", "hook") else None))
            acts[-1].path = self._backtrack(par, cur) + [d]
        # land the current vehicle here (A)
        if vt in (1, 2, 3, 6):
            comp = self._foot_comp(w, mk)
            hot = self._hot_comps(s, w, comp)
            bestl = {}
            for node, dd in dist.items():
                if self.W.can_land(w, vt, node[0], node[1], mk) and self._plain(s, node, vehicle_tiles):
                    c = comp[node[1] * 256 + node[0]]
                    if c in hot and (c not in bestl or dd < dist[bestl[c]]): bestl[c] = node     # one landing per useful piece of land
            for node in bestl.values():
                dd = dist[node]
                if True:
                    tgt = State.clone(s); slot = s.veh - 1
                    tgt.x, tgt.y = node
                    tgt.vtab[slot] = [w << 5 | (tgt.vtab[slot][0] & 0x1F), 0x00, node[0], node[1]]
                    tgt.veh = 0
                    acts.append(Action("land", "land vehicle %d at (%d,%d) world %d" % (s.veh, node[0], node[1], w), dd + 1, (tgt, False)))
                    acts[-1].path = self._backtrack(par, node)
        # board a chocobo / hiryuu / airship by standing on it and pressing A
        if vt == 0:
            for i in range(6):
                v = s.vtab[i]
                typ = (v[0] & 0x1C) >> 2
                if v[1] & 0x80 or (v[0] >> 5) != w or typ in (4, 5, 0): continue
                if (v[2], v[3]) in dist:
                    tgt = State.clone(s); tgt.x, tgt.y = v[2], v[3]; tgt.veh = i + 1; tgt.vtab[i][1] |= 0x80
                    acts.append(Action("boardA", "board vehicle %d at (%d,%d)" % (i + 1, v[2], v[3]), dist[(v[2], v[3])] + 1, (tgt, False)))
                    acts[-1].path = self._backtrack(par, (v[2], v[3]))
        return acts

    def _world_barriers(self, w):
        cache = self.__dict__.setdefault("_wbarrier_cache", {})
        b = cache.get(w)
        if b is None:
            b = set(self.trig_at(w)) | set(self.ent_at(w))
            for m, vtest, (x0, x1), (y0, y1), ts in WORLD_HOOKS:
                if m == w: b |= {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}
            b = cache[w] = frozenset(b)
        return b

    def _world_flood(self, w, vt, mk, vtiles, start):
        """cached: reachable world tiles from `start` for vehicle type vt without stepping on a tile that can fire something or leaving
        a ship onto land: (dist, par, arrivals (cur, d, npos, disembark))"""
        cache = self.__dict__.setdefault("_wflood_cache", {})
        key = (w, vt, mk, vtiles, start)
        r = cache.get(key)
        if r is not None: return r
        bar = self._world_barriers(w)
        sc = self.__dict__.setdefault("_wstep_cache", {}).setdefault((w, vt, mk), {})       # step results are the same for every flood of this map state
        dist = {start: 0}; par = {start: None}; arr = []; seen = set()
        q = collections.deque([start])
        while q:
            cur = q.popleft()
            for d in (1, 2, 3, 4):
                dx, dy = WDIRS[d]
                npos = ((cur[0] + dx) & 255, (cur[1] + dy) & 255)
                bd = npos in vtiles and vt == 0
                k2 = (cur[0], cur[1], d, bd)
                r2 = sc.get(k2, 0)
                if r2 == 0: r2 = sc[k2] = self.W.step(w, vt, cur[0], cur[1], d, mk, bd)
                if r2 is None: continue
                if r2[2]:
                    if (npos, d) not in seen: seen.add((npos, d)); arr.append((cur, d, npos, True))
                    continue
                if npos in dist: continue
                if npos in bar or npos in vtiles:
                    if (npos, d) not in seen: seen.add((npos, d)); arr.append((cur, d, npos, False))
                    continue
                dist[npos] = dist[cur] + 1; par[npos] = (cur, d); q.append(npos)
        if len(cache) > 60: cache.clear()
        r = cache[key] = (dist, par, arr)
        return r

    def _hot_comps(self, s, w, comp):
        hot = set(comp[y * 256 + x] for (x, y) in list(self.ent_at(w)) + list(self.trig_at(w)) + [(v[2], v[3]) for v in s.vtab if (v[0] >> 5) == w])
        hot |= set(comp[y * 256 + x] for m_, vt_, (x0, x1), (y0, y1), ts_ in WORLD_HOOKS if m_ == w for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
        return hot

    def _foot_comp(self, w, mk):
        key = (w, mk)
        cache = self.__dict__.setdefault("_comp_cache", {})
        if key in cache: return cache[key]
        comp = [-1] * 65536; n = 0
        for st in range(65536):
            if comp[st] >= 0: continue
            comp[st] = n; q = [st]
            while q:
                c = q.pop(); x, y = c & 255, c >> 8
                for d in (1, 2, 3, 4):
                    dx, dy = WDIRS[d]; nx, ny = (x + dx) & 255, (y + dy) & 255
                    nc = ny * 256 + nx
                    if comp[nc] >= 0: continue
                    r = self.W.step(w, 0, x, y, d, mk, False)
                    if r is None or r[2]: continue
                    rb = self.W.step(w, 0, nx, ny, {1: 3, 2: 4, 3: 1, 4: 2}[d], mk, False)
                    if rb is None or rb[2]: continue
                    comp[nc] = n; q.append(nc)
            n += 1
        cache[key] = comp
        return comp

    def _plain(self, s, node, vehicle_tiles):
        return node not in vehicle_tiles

    def execute(self, s, act):
        """apply an action's event (if any) and arrival processing -> list of states"""
        tgt, stepped = act.target
        if act.path and tgt.flags >> 508 & 3:                # every party step clears the temporary event flags 508/509 ($c0c796 at StartMove/step)
            tgt = State.clone(tgt); tgt.flags &= ~(3 << 508); act = Action(act.kind, act.desc, act.cost, (tgt, stepped), act.face, act.event)
        if act.kind == "wait":              # the scripted release leaves the party at (20,61) on the live game (the VM's party-step trace drifts by 2)
            out = self._run_after(tgt, act.event, 0)
            for o in out: o.x, o.y = 20, 61
            return out
        if act.kind in ("press", "talk", "turn"):
            return self._run_after(tgt, act.event, 0) if act.event else [tgt]
        if act.kind == "land":
            ev = self.pick(tgt, self.R.trigger_script(288))
            return self._run_after(tgt, ev, 0) if ev else self.arrive(tgt, False)
        if act.kind == "boardA":
            return [tgt]
        if act.kind == "walkdisembark":
            return self.arrive(tgt, True)
        return self.arrive(tgt, True)
