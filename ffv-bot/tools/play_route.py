#!/usr/bin/env python3
"""Play-test the generated route (data/event_path.json) on the test bed. Usage: play_route.py <rom.sfc> [first_step [last_step]]
Saves a state to slot N after each step (slot = step index mod 9 + 1 is NOT used; states are written to /tmp/route_states by copy)."""
import sys, os, json, time, shutil
sys.path.insert(0, os.path.dirname(__file__))
from collections import deque
from play_nav import Nav
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
        self.veh = None
        self.maps = Maps(open(ROM, 'rb').read()); self._exits = {}
        for m in range(512):
            try: self.n.avoid[m] = {(e['x'], e['y']) for e in self.rom.entrances(m)}
            except Exception: pass

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

    def route_maps(self, a, b):
        q, prev = deque([a]), {a: None}
        while q:
            m = q.popleft()
            if m == b: break
            for dm, x, y in self.edges(m):
                if dm not in prev: prev[dm] = (m, x, y); q.append(dm)
            if m >= 3 and b < 3 and b not in prev and self.has_exit(m): prev[b] = (m, -1, -1); q.append(b)
        if b not in prev: return None
        path, c = [], b
        while prev[c]: p = prev[c]; path.append((p[0], p[1], p[2], c)); c = p[0]
        return path[::-1]

    def go_map(self, target):
        for _ in range(8):
            m = self.n.stable()[0]
            if m == target: return True
            path = self.route_maps(m, target)
            if not path and self.n.pos()[3] == 1:
                res = self.n.leave_map(); self.say("  leave map", m, res, self.n.pos()); self.n.dialog(); time.sleep(0.5)
                if res == "mapchange": continue
            if not path: self.say("no entrance path", m, "->", target); return False
            fm, x, y, dm = path[0]
            if x < 0:
                res = self.n.leave_map(); self.say("  leave map", fm, res, self.n.pos()); self.n.dialog(); time.sleep(0.5); continue
            r = self.n.goto(x, y, self.veh)
            self.say("  entrance", fm, (x, y), "->", dm, r, self.n.pos())
            if r not in ("mapchange", "arrived"): return False
            self.n.dialog(); time.sleep(0.5)
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

    def do_trigger(self, m, x, y, ev=None):
        if not self.go_map(m): return False
        f = self.facing_for(m, x, y, ev) if ev else None
        if f is not None:
            # triggers that need a facing direction: stand on the tile and press that direction (a blocked move turns the party), then A
            r = self.n.goto(x, y, self.veh); self.say("  trigger tile", (m, x, y), r, self.n.pos())
            self.n.hold(self.FACE_KEY[f], 8)
            if self.n.pos()[:3] != (m, x, y): self.say("  moved off the tile", self.n.pos()); self.n.goto(x, y, self.veh); self.n.hold(self.FACE_KEY[f], 8)
        else:
            r = self.n.goto(x, y, self.veh); self.say("  trigger tile", (m, x, y), r, self.n.pos())
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
