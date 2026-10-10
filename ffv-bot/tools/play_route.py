#!/usr/bin/env python3
"""Play-test the generated route (data/event_path.json) on the test bed. Usage: play_route.py <rom.sfc> [first_step [last_step]]
Saves a state to slot N after each step (slot = step index mod 9 + 1 is NOT used; states are written to /tmp/route_states by copy)."""
import sys, os, json, time, shutil
sys.path.insert(0, os.path.dirname(__file__))
from collections import deque
import event_loads
from play_nav import Nav, DIRS
from event_vm import Rom
from map_render import Maps, u16
from ffv_decomp import decompress

ROM = sys.argv[1] if len(sys.argv) > 1 else "/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc"
HERE = os.path.dirname(__file__)
STATE_DIR = "/mnt/project-files/retroarch-testbed/states/Snes9x"
STATE_NAME = "Final Fantasy V (Japan) (patched)(1).state%d"

class Runner:
    def __init__(self):
        self.n = Nav(ROM); self.rom = Rom(ROM)
        self.steps = json.load(open(os.path.join(HERE, "..", "data", "event_path.json")))
        self.veh = None; self.bad = set(); self.tried_veh = set(); self._grids = {}; self._tedges = {}; self._el = event_loads.load(); self._tl = {}; self._ops = {}; self._room = {}; self._rooms = {}; self.trig_info = {}
        self.world_doors = {e['map'] & 0x1FF for wm in (0, 1, 2) for e in self.rom.entrances(wm)}
        self.maps = Maps(open(ROM, 'rb').read()); self._exits = {}
        for m in range(512):
            try: self.n.avoid[m] = {(e['x'], e['y']) for e in self.rom.entrances(m)}
            except Exception: pass

    FORCED = {840: "RIGHT", 841: "LEFT", 842: "DOWN", 843: "UP"}
    def load_free(self, m):
        """Forced-move facing triggers (events 840-843) of local map m: allow walking onto and off those tiles."""
        if m in self.n.free or m < 3: return
        fm = {}
        for x, y, t in self.rom.triggers(m):
            for conds, ev in self.rom.trigger_script(t):
                if ev in self.FORCED and any(c[0] == "facing" for c in conds): fm.setdefault((x, y), set()).add(self.FORCED[ev])
        self.n.free[m] = fm

    def trigger_loads(self, ev, depth=0, seen=None):
        """Maps loaded by event ev (following jump commands): list of (map, x, y)."""
        seen = seen if seen is not None else set()
        if ev in seen or depth > 4: return []
        seen.add(ev); d = self._el.get(ev); out = []
        if not d: return out
        out += [tuple(l) for l in d["loads"]]
        for j in d["jumps"]: out += self.trigger_loads(j, depth + 1, seen)
        return out

    def trigger_edges(self, m):
        """Triggers on local map m whose script moves the party to another map: {(x, y): [(dest, dx, dy, facing)]}."""
        if m not in self._tedges:
            out = {}
            for x, y, t in self.rom.triggers(m):
                for conds, ev in self.rom.trigger_script(t):
                    f = next((c[1] for c in conds if c[0] == "facing"), None)
                    for dm, dx, dy in self.trigger_loads(ev):
                        if dm != m: out.setdefault((x, y), []).append((dm, dx, dy, f))
            self._tedges[m] = out
        return self._tedges[m]

    def say(self, *a): print(time.strftime("%H:%M:%S"), *a, flush=True)

    def edges(self, m):
        out = []
        for e in self.rom.entrances(m):
            out.append((e["map"] & 0x1FF, e["x"], e["y"]))
        return out

    def has_exit(self, m):
        """Does local map m have tiles that leave to the parent map (properties byte 2 == 0 on a passable tile)?"""
        if m not in self._exits:
            rom = self.maps.r
            try:
                p = self.maps.props(m)
                lay = self.maps.layout(((p[12] | p[13] << 8) & 0x3FF) - 1)
                pr = decompress(rom, 0xFC540 + u16(rom, 0xFC540 + 2 * p[5]), True)
                self._exits[m] = any(pr[2 * t + 1] == 0 and pr[2 * t] & 3 for t in set(lay) if 2 * t + 1 < len(pr))
            except Exception: self._exits[m] = False
        return self._exits[m]

    def grid_rom(self, m):
        """Tile ids and property bytes of local map m straight from the ROM (same data the engine copies to WRAM)."""
        if m not in self._grids:
            rom = self.maps.r; p = self.maps.props(m)
            lay = self.maps.layout(((p[12] | p[13] << 8) & 0x3FF) - 1)
            pr = decompress(rom, 0xFC540 + u16(rom, 0xFC540 + 2 * p[5]), True)
            self._grids[m] = (list(lay), bytes(pr))
        return self._grids[m]

    def reach_entrances(self, m, x, y):
        """Entrances of local map m that can be walked to from tile (x, y)."""
        rs = self.reach_set(m, x, y)
        return [e for e in self.rom.entrances(m) if (e["x"], e["y"]) in rs]

    def reach_set(self, m, x, y):
        """All tiles of local map m walkable from (x, y) (property rule plus forced-move doorway tiles); cached per room."""
        key = self._room.get((m, x, y))
        if key is not None: return self._rooms[key]
        ids, props = self.grid_rom(m)
        self.load_free(m)
        seen, st = {(x, y)}, [(x, y)]
        while st:
            c = st.pop()
            for d, (dx, dy) in DIRS.items():
                n = (c[0] + dx, c[1] + dy)
                if n in seen or not (0 <= n[0] < 64 and 0 <= n[1] < 64): continue
                if not self.n.can_move_m(m, ids, props, c, n, d): continue
                seen.add(n); st.append(n)
            for d, n in self.n.hop_edges(m, c):
                if n not in seen and 0 <= n[0] < 64 and 0 <= n[1] < 64: seen.add(n); st.append(n)
        key = (m, min(seen)); fs = frozenset(seen); self._rooms[key] = fs
        for t in fs: self._room[(m,) + t] = key
        return fs

    def room_key(self, m, x, y):
        self.reach_set(m, x, y); return self._room[(m, x, y)]

    def route_maps(self, a, b, tile=None):
        """Dijkstra over (map, arrival tile) nodes: local maps may hold several disconnected rooms. Entrances the tile
        properties say are walkable cost 1; others (the property rule is not complete) cost 8 and are tried last."""
        import heapq
        _, x0, y0, _l = self.n.pos()
        start = (a, x0, y0) if a < 3 else (a,) + self.room_key(a, x0, y0)[1]
        dist, prev, pq = {start: 0}, {start: None}, [(0, start)]
        found = None
        while pq:
            d0, node = heapq.heappop(pq); m, x, y = node
            if d0 > dist[node]: continue
            if m == b and (tile is None or m < 3 or tile in self.reach_set(m, x, y)): found = node; break
            if m < 3:
                outs = [(e, 1) for e in self.rom.entrances(m)]
            else:
                good = {(e["x"], e["y"]) for e in self.reach_entrances(m, x, y)}
                outs = [(e, 1 if (e["x"], e["y"]) in good else 8) for e in self.rom.entrances(m)]
                good2 = self.reach_set(m, x, y)
                for (tx, ty), lst in self.trigger_edges(m).items():
                    for dm, dx, dy, f in lst:
                        outs.append(({"x": tx, "y": ty, "map": dm, "dx": dx, "dy": dy, "trig": f}, 2 if (tx, ty) in good2 else 9))
                if b < 3 and (self.has_exit(m) or m in self.world_doors): outs.append(({"x": -1, "y": -1, "map": b, "dx": 0, "dy": 0}, 1))
            for e, w in outs:
                if (m, e["x"], e["y"]) in self.bad: continue
                dm = e["map"]
                nn = (dm, e["dx"] & 0x3F, e["dy"] & 0x3F) if dm >= 3 or dm == b else (dm, 0, 0)
                if dm >= 3: nn = (dm,) + self.room_key(dm, nn[1], nn[2])[1]
                if "trig" in e: self.trig_info[(m, e["x"], e["y"], dm)] = e["trig"]
                if nn not in dist or d0 + w < dist[nn]:
                    dist[nn] = d0 + w; prev[nn] = (node, e["x"], e["y"]); heapq.heappush(pq, (dist[nn], nn))
        end = found if found is not None else (next((n for n in prev if n[0] == b), None) if tile is None else None)
        if end is None: return None
        path, c = [], end
        while prev[c]: p = prev[c]; path.append((p[0][0], p[1], p[2], c[0])); c = p[0]
        return path[::-1]

    def go_map(self, target, tile=None):
        for _ in range(10):
            m = self.n.stable()[0]
            if m == target and (tile is None or m < 3 or tile in self.reach_set(m, *self.n.fpos()[1:3])): return True
            path = self.route_maps(m, target, tile)
            if not path and self.n.pos()[3] == 1:
                res = self.n.leave_map(); self.say("  leave map", m, res, self.n.pos()); self.n.dialog(); time.sleep(0.5)
                if res == "mapchange": continue
            if not path: self.say("no entrance path", m, "->", target); return False
            self.load_free(m)
            fm, x, y, dm = path[0]
            if x < 0:
                res = self.n.leave_map(); self.say("  leave map", fm, res, self.n.pos()); self.n.dialog(); time.sleep(0.5); continue
            r = self.walk(x, y)
            self.say("  entrance", fm, (x, y), "->", dm, r, self.n.pos())
            if (fm, x, y, dm) in self.trig_info and r == "arrived":
                f = self.trig_info[(fm, x, y, dm)]
                if f is not None: self.n.hold(self.FACE_KEY[f], 8)
                for _ in range(3): self.n.press("A", 4, 20)
                self.n.settle(); self.say("  trigger", (x, y), "->", dm, self.n.pos())
                continue
            if r == "nopath" and self.n.pos()[3] == 0 and self.board_vehicle(): continue    # reachable by boat/other vehicle
            if r == "nopath": self.bad.add((fm, x, y)); continue    # unreachable entrance on foot: try another route
            if r == "arrived" and self.n.stable()[0] == fm and (fm, x, y, dm) not in self.trig_info:
                px, py = self.n.fpos()[1:3]
                if abs(px - x) + abs(py - y) <= 1:
                    self.bounce()                  # arrival tile is the entrance tile itself: step off and back on
                    self.n.settle(10)
                    qx, qy = self.n.fpos()[1:3]
                    if self.n.stable()[0] == fm and abs(qx - x) + abs(qy - y) <= 2: self.bad.add((fm, x, y))
                continue
            if r not in ("mapchange", "arrived"): return False
            self.n.dialog(); time.sleep(0.5)
        return False

    OPP = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
    def bounce(self):
        m0 = self.n.stable()[0]
        for d in ("DOWN", "UP", "LEFT", "RIGHT"):
            moved, p = self.n.step(d)
            if p[0] != m0: return True
            if moved:
                moved, p = self.n.step(self.OPP[d])
                return True
        return False

    def board_vehicle(self):
        """On the world map and on foot: walk to a parked vehicle (data at $0ADD, 4 bytes each: flags, height, x, y) and board it."""
        if self.n.cur_veh(): return False
        m = self.n.pos()[0]
        for i in range(6):
            b = self.n.read(0xADD + 4 * i, 4)
            if (b[0] >> 5) != m and b[0] & 0xE0 != 0 or (b[2], b[3]) == (0, 0): continue
            if b[0] & 0x1F == 0 and b[1] == 0x80: continue
            if (i, b[2], b[3]) in self.tried_veh: continue
            self.tried_veh.add((i, b[2], b[3]))
            for ox, oy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):      # the parked position can be a tile off
                r = self.n.goto(b[2] + ox, b[3] + oy, 0)
                self.say("  vehicle", i, (b[2] + ox, b[3] + oy), r, "now on vehicle", self.n.cur_veh())
                if self.n.cur_veh(): return True
                _m, px, py = self.n.fpos()
                for d, (dx, dy) in DIRS.items():        # adjacent to the parked vehicle: step onto it
                    if abs(px + dx - b[2]) + abs(py + dy - b[3]) < abs(px - b[2]) + abs(py - b[3]):
                        self.n.step(d); time.sleep(1.0)
                        if self.n.cur_veh(): return True
                if r != "nopath": break
        return False

    FACE = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}
    FACE_KEY = {0: "UP", 1: "RIGHT", 2: "DOWN", 3: "LEFT"}

    def facing_for(self, m, x, y, ev):
        for tx, ty, t in self.rom.triggers(m):
            if (tx, ty) == (x, y):
                for conds, e in self.rom.trigger_script(t):
                    if e == ev:
                        for c in conds:
                            if c[0] == "facing": return c[1]
        return None

    def walk(self, x, y):
        """goto, boarding a parked vehicle when the target needs one (river and sea tiles on the world map)."""
        self.load_free(self.n.pos()[0])
        r = self.n.goto(x, y, self.veh)
        for _ in range(3):
            if r != "nopath" or self.n.pos()[3] != 0 or self.n.cur_veh(): break
            self.tried_veh.clear()
            if not self.board_vehicle(): break
            r = self.n.goto(x, y, self.veh)
        return r

    def do_trigger(self, m, x, y, ev=None):
        if not self.go_map(m, (x, y)): return False
        f = self.facing_for(m, x, y, ev) if ev else None
        if f is not None:
            # triggers that need a facing direction: stand on the tile and press that direction (a blocked move turns the party), then A
            r = self.walk(x, y); self.say("  trigger tile", (m, x, y), r, self.n.pos())
            self.n.hold(self.FACE_KEY[f], 8)
            if self.n.pos()[:3] != (m, x, y): self.say("  moved off the tile", self.n.pos()); self.walk(x, y); self.n.hold(self.FACE_KEY[f], 8)
        else:
            r = self.walk(x, y); self.say("  trigger tile", (m, x, y), r, self.n.pos())
        if self.n.pos()[:3] == (m, x, y):
            for _ in range(3): self.n.press("A", 4, 20)   # triggers that need the A button
        self.n.settle()
        return True

    def run(self, first=1, last=999):
        for i, s in enumerate(self.steps, 1):
            if i < first or i > last: continue
            self.say("STEP", i, s["kind"], s.get("event", ""), s.get("where", s.get("why")))
            ok = True
            if s["kind"] == "event" and s["where"][0] == "trigger":
                w = s["where"]; ok = self.do_trigger(w[1], w[2], w[3], s['event'])
            elif s["kind"] == "event" and s["where"][0] == "board ship":
                ok = self.go_map(0)
                self.n.dialog(); self.n.release_all()
                if ok and not self.n.cur_veh():
                    self.tried_veh.clear(); ok = self.board_vehicle()
                self.n.settle()
            elif s["kind"] == "entrance":
                ok = self.go_map(s["to"][0])
            else:
                self.say("  (manual step)"); ok = False
            for f in s.get("sets", []):
                if f not in (510, 511): self.say("  flag", f, self.n.flag(f))
            self.say("  now", self.n.pos(), "ok" if ok else "FAILED")
            if not ok: return i
            self.n.save_as("/tmp/route_step_%03d.state" % i)
        return None

if __name__ == "__main__":
    f = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    l = int(sys.argv[3]) if len(sys.argv) > 3 else 999
    r = Runner()
    if not os.environ.get('NOLOAD'): r.n.load_from('/tmp/route_step_%03d.state' % (f - 1)) if f > 1 else r.n.load(1)
    time.sleep(1)
    print("stopped at", r.run(f, l))
