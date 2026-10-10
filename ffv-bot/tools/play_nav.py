#!/usr/bin/env python3
"""Route play-test driver: walks the ROM-derived route on the test bed with dev cheats (immortal party, 1-HP monsters).
Movement uses ROM data for planning (world rows + tile properties; local map grid and tile properties read from WRAM) and
RAM position feedback; blocked steps are remembered and replanned. Cheats are for route testing only (user-approved)."""
import sys, os, time
from collections import deque
sys.path.insert(0, os.path.dirname(__file__))
from play import Game
from world_model import World

DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

class Nav(Game):
    _void = {}
    def __init__(self, rom_path, fast=1.0):
        super().__init__(fast=fast)
        self.world = World(open(rom_path, "rb").read())
        self.blocked = {}
        self.free = {}       # map -> {tile: set of directions}: forced-move trigger tiles (ladders/doorways) that the property rule rejects
        self.avoid = {}      # map -> set of tiles to path around (map entrances other than the goal)
        self.log = []
        from event_vm import Rom
        self._rom = Rom(rom_path)
        self.rom_entrances = self._rom.entrances

    # ---- cheats ----
    def cheat_party(self):
        for slot in range(4):
            b = 0x500 + 0x50 * slot
            if self.u8(b) & 0x40 or True:
                self.write(b + 6, bytes([0x0F, 0x27, 0x0F, 0x27]))      # hp, max hp 9999
                self.write(b + 10, bytes([0x0F, 0x27, 0x0F, 0x27]))     # mp, max mp 9999
    def cheat_battle(self):
        for slot in range(4):
            b = 0x2000 + 0x80 * slot
            self.write(b + 6, bytes([0x0F, 0x27]))
        for slot in range(4, 12):
            b = 0x2000 + 0x80 * slot
            if self.u16(b + 6) > 1: self.write(b + 6, bytes([1, 0]))

    # ---- state helpers ----
    def flags256(self):
        raw = self.read(0xA14, 96)
        m = 0
        for i, b in enumerate(raw): m |= b << (8 * i)
        return m
    def fpos(self):
        b = self.read(0xAD4, 6)
        return ((b[0] | b[1] << 8) & 0xFFF, b[4], b[5])
    def pos(self):
        m, x, y = self.fpos(); return m, x, y, self.u8(0xB53)
    def cur_veh(self):
        """Vehicle the party rides. $0ADC flickers to 0 while a vehicle moves, so take the largest of several samples; also match
        the party tile against the parked-vehicle table ($0ADD, 4 bytes each: flags, height, x, y; vehicle id = slot + 1)."""
        v = 0
        for _ in range(5):
            v = max(v, self.u8(0xADC)); time.sleep(0.03)
        if v: return v
        m, x, y = self.fpos()
        raw = self.read(0xADD, 24)
        for i in range(6):
            if raw[4 * i + 2] == x and raw[4 * i + 3] == y and (x, y) != (0, 0): return i + 1
        return 0
    def stable(self, tries=6):
        last, n = None, 0
        for _ in range(40):
            p = self.pos()
            if p == last: n += 1
            else: last, n = p, 1
            if n >= tries: return p
            time.sleep(0.05)
        return last

    def fight(self, limit=1500):
        """Win the current battle with cheats; returns True when the battle flag cleared."""
        for i in range(limit):
            if self.u8(0x18) != 1: return True
            self.cheat_battle()
            self.press("A", 3, 3)
        return False

    def busy(self):
        """A message box is open ($B57), a battle is on ($18) or the party is being moved by a script (position changing)."""
        b = self.read(0xB57, 1)[0], self.read(0x18, 1)[0]
        if any(b): return True
        p = self.fpos(); time.sleep(0.4)
        return self.fpos() != p

    def dialog(self, n=40):
        """Mash A while a message box is open or a script is running; returns presses used."""
        k = quiet = 0
        while k < n:
            if self.u8(0x18) == 1: self.fight()
            if self.u16(0xAD4) & 0xFFF == 13: self.name_screen(); continue
            if not self.busy():
                quiet += 1
                if quiet >= 4: break          # idle for a few polls: the scene is over
                time.sleep(0.15); continue
            quiet = 0
            self.press("A", 3, 3); k += 1
        return k

    def settle(self, limit=400):
        """Run scenes to the end: keep pressing A until nothing is running for ~2 s."""
        for _ in range(limit):
            self.dialog(60)
            time.sleep(0.5)
            if not self.busy():
                time.sleep(1.0)
                if not self.busy(): return True
        return False

    def name_screen(self):
        """Name entry (map 13): the default name is kept; UP to the header row, RIGHT x4 to End, A."""
        self.press("UP", 4, 8)
        for _ in range(4): self.press("RIGHT", 4, 8)
        self.press("A", 4, 30); self.wait(60)

    # ---- movement ----
    def step(self, d, frames=14):
        """One tile in direction d. The engine keeps walking while the key is down at the tile boundary, so tap the key for a few
        frames (a held key overshoots by a tile), fall back to a longer hold when the tap did not start a move."""
        from play import IDS
        before = self.fpos()
        for hold in (3, frames):
            self._send(IDS[d], True)
            moved = False
            for i in range(hold):
                self.wait(1)
            self._send(IDS[d], False)
            for _ in range(12):
                self.wait(1)
                if self.fpos() != before: moved = True; break
            if moved: break
        if moved:
            last = None
            for _ in range(10):                 # let the walk animation finish before the next command
                self.wait(2); cur = self.fpos()
                if cur == last: break
                last = cur
        else:
            self.wait(2)
        return moved, self.pos()

    def local_grid(self):
        ids = self.read(0x10000, 4096)
        props = self.read(0x1186, 512)   # 2 bytes per tile (byte 1, byte 2), matches the engine mirror at $10f2
        return ids, props

    SIDE = {"UP": 0x08, "RIGHT": 0x01, "DOWN": 0x04, "LEFT": 0x02}
    def can_move(self, ids, props, c, n, d):
        """Engine rule (CheckPartyCanMove): the tile you leave must open its side in direction d (properties byte 2),
        and the destination's z-level bits (byte 1) must match the current tile's z-level or be a bridge."""
        if max(c[1] * 64 + c[0], n[1] * 64 + n[0]) >= len(ids): return False
        tc, tn = ids[c[1] * 64 + c[0]], ids[n[1] * 64 + n[0]]
        if 2 * max(tc, tn) + 1 >= len(props): return False
        b1c, b2c = props[2 * tc], props[2 * tc + 1]
        b1n = props[2 * tn]
        if not (b2c & self.SIDE[d]) and not (b2c & 0x40): return False
        return bool(b1n & ((b1c & 3) | 4))

    def void_id(self, map_id, ids, props):
        """The filler tile that covers most of a cave/mountain map (black rock). Its props say open, but it is outside the playable area."""
        v = self._void.get(map_id)
        if v is None:
            import collections
            t, n = collections.Counter(bytes(ids[:4096])).most_common(1)[0]
            v = t if n > 1500 and 2 * t < len(props) and props[2 * t] & 0x08 else -1   # grass fills big maps too, but its props lack bit 0x08
            self._void[map_id] = v
        return v

    def can_move_m(self, map_id, ids, props, c, n, d):
        v = self.void_id(map_id, ids, props)
        if v >= 0 and max(c[1] * 64 + c[0], n[1] * 64 + n[0]) < len(ids) and ids[n[1] * 64 + n[0]] == v and (ids[c[1] * 64 + c[0]] == v or n not in self.avoid.get(map_id, ())): return False   # doors sit on the filler tile
        return self.can_move(ids, props, c, n, d)

    def hop_edges(self, map_id, c):
        """Forced-move facing triggers (events 840-843) hop the party two tiles over a deep-water gap: [(direction, target tile)]."""
        fm = self.free.get(map_id)
        if not fm or c not in fm: return []
        return [(d, (c[0] + 2 * DIRS[d][0], c[1] + 2 * DIRS[d][1])) for d in fm[c]]

    def plan_local(self, start, goal, ids, props, map_id):
        import heapq
        goals = goal if isinstance(goal, (set, frozenset)) else {goal}
        found = None
        bl = self.blocked.setdefault(map_id, set())
        av = self.avoid.get(map_id, ())
        dist, prev, pq = {start: 0}, {start: None}, [(0, start)]
        while pq:
            dcur, c = heapq.heappop(pq)
            if c in goals: found = c; break
            if dcur > dist[c]: continue
            for d, (dx, dy) in DIRS.items():
                n = (c[0] + dx, c[1] + dy)
                if not (0 <= n[0] < 64 and 0 <= n[1] < 64) or (c, d) in bl: continue
                if not self.can_move_m(map_id, ids, props, c, n, d): continue
                w = 1 + (60 if n in av and n not in goals else 0)   # stepping on another map's entrance leaves the map: only if unavoidable
                if n not in dist or dcur + w < dist[n]:
                    dist[n] = dcur + w; prev[n] = (c, d); heapq.heappush(pq, (dist[n], n))
            for d, n in self.hop_edges(map_id, c):
                if not (0 <= n[0] < 64 and 0 <= n[1] < 64) or (c, d) in bl: continue
                if n not in dist or dcur + 1 < dist[n]:
                    dist[n] = dcur + 1; prev[n] = (c, d); heapq.heappush(pq, (dist[n], n))
        if found is None: return None
        path, c = [], found
        while prev[c]: c, d = prev[c][0], prev[c][1]; path.append(d)
        return path[::-1]

    def plan_world(self, w, veh, start, goal, mask):
        """BFS over (tile, vehicle) states. The ship disembarks onto any walkable shore tile; the other vehicles use their landing rules."""
        mk = self.world.mod_key(w, mask)
        bl = self.blocked.setdefault((w, "w"), set())
        s0 = (start, veh or 0)
        q, prev = deque([s0]), {s0: None}
        end = None
        while q:
            st = q.popleft(); c, v = st
            if c == goal: end = st; break
            for d, (dx, dy) in DIRS.items():
                n = ((c[0] + dx) % 256, (c[1] + dy) % 256)
                if (c, d) in bl: continue
                if v and self.world.passable(w, v, n[0], n[1], mk): nv = v
                elif self.world.passable(w, 0, n[0], n[1], mk) and (v == 0 or v == 5 or self.world.can_land(w, v, n[0], n[1], mk)): nv = 0
                elif n == goal and v: nv = v
                else: continue
                ns = (n, nv)
                if ns in prev: continue
                prev[ns] = (st, d); q.append(ns)
        if end is None: return None
        path, c = [], end
        while prev[c]: c, d = prev[c][0], prev[c][1]; path.append(d)
        return path[::-1]

    def goto(self, x, y, veh=None, max_steps=2500, want_map=None):
        """Walk to tile (x, y) of the current map. Returns 'arrived', 'mapchange', 'stuck' or 'nopath'."""
        m0, cx, cy, local = self.stable()
        self.blocked.pop(m0 if local else (m0, "w"), None)   # bump memory is per attempt (NPCs and scenes move)
        stuck = nopath = 0
        for _ in range(max_steps):
            if self.u8(0x18) == 1: self.fight()
            if self.u8(0xB57): self.dialog();
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            if (cx, cy) == (x, y): return "arrived"
            if local:
                ids, props = self.local_grid()
                path = self.plan_local((cx, cy), (x, y), ids, props, m)
            else:
                v = veh if veh is not None else self.cur_veh()    # $0ADC = current vehicle (0 foot, 1 chocobo, ... as in world_model)
                path = self.plan_world(m, v, (cx, cy), (x, y), self.flags256() << 0)
            if not path:
                nopath += 1
                if nopath > 6: return self.probe(x, y, m0) or "nopath"
                for _ in range(3): self.press("A", 4, 14)    # result screens ($18 is already 0) need A presses
                self.settle(30); continue   # battle result screens and scripts leave the RAM grid unusable for a while
            for d in path[:12]:
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved:
                    if self.u8(0x18) == 1: self.fight()
                    elif self.u8(0xB57): self.dialog()
                    else:
                        # maybe an NPC is in the way (Boco, guards): talk to it, then retry once before treating the edge as blocked
                        for _ in range(8):                      # also clears battle result screens ($18 is already 0 there)
                            self.press("A", 4, 20)
                            moved, p = self.step(d)
                            if moved or p[0] != m0: break
                            if self.u8(0x18) == 1: self.fight()
                        if p[0] != m0: return "mapchange"
                        if not moved:
                            self.blocked.setdefault(m if local else (m, "w"), set()).add(((cx, cy), d)); stuck += 1
                    break
                dx, dy = DIRS[d]
                if not local and self.cur_veh() != v:
                    cx, cy = p[1], p[2]; break         # boarded or disembarked: replan with the new vehicle
                if (p[1], p[2]) != (cx + dx, cy + dy) and local:
                    cx, cy = p[1], p[2]; break         # overshoot or a scripted move: replan from where we are
                cx, cy = p[1], p[2]
            if stuck > 60: return "stuck"
        return "stuck"

    def probe(self, x, y, m0, limit=40):
        """Fallback when the grid says there is no path (tile properties can lie, e.g. before a scene rewrites the room):
        feel toward the goal with real steps, preferring the longer axis. Returns 'arrived'/'mapchange' or None."""
        for _ in range(limit):
            if self.u8(0x18) == 1: self.fight()
            if self.u8(0xB57): self.dialog()
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            if (cx, cy) == (x, y): return "arrived"
            opts = []
            if cx != x: opts.append(("RIGHT" if x > cx else "LEFT", abs(x - cx)))
            if cy != y: opts.append(("DOWN" if y > cy else "UP", abs(y - cy)))
            moved = False
            for d, _n in sorted(opts, key=lambda o: -o[1]):
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if moved: break
            if not moved: return None
        return None

    def leave_by_door(self, m0):
        """Maps whose way out has no exit-tile property (e.g. Wind Shrine front room): walk to the tile the world entrance
        drops you on, then keep walking outward (down, then the other directions) until the map changes."""
        door = next((e for wm in (0, 1, 2) for e in self.rom_entrances(wm) if e["map"] == m0), None)
        if not door: return None
        if self.goto(door["dx"] & 0x3F, door["dy"] & 0x3F) == "mapchange": return "mapchange"
        for d in ("DOWN", "LEFT", "RIGHT", "UP"):
            for _ in range(3):
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved: break
        return None

    def exit_tiles(self, ids, props):
        """Map exit tiles (engine: tile properties byte 2 == 0 on a passable tile leaves to the parent map)."""
        return {(x, y) for y in range(64) for x in range(64)
                if props[2 * ids[y * 64 + x] + 1] == 0 and props[2 * ids[y * 64 + x]] & 3}

    def leave_map(self, max_steps=800):
        """Walk to the nearest map exit tile; returns goto-style result."""
        m0, cx, cy, local = self.stable()
        ids, props = self.local_grid()
        goals = self.exit_tiles(ids, props) - self.avoid.get(m0, set())
        for _ in range(12):
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            ids, props = self.local_grid()
            path = self.plan_local((cx, cy), goals, ids, props, m0)
            if not path: return self.leave_by_door(m0) or "nopath"
            for d in path[:10]:
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved:
                    if self.u8(0x18) == 1: self.fight()
                    elif self.u8(0xB57): self.dialog()
                    else: self.press("A", 4, 20); self.dialog(30)
                    break
        return "stuck"
