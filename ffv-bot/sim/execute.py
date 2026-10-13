"""Play a simulator macro-path on the emulator test bed (pad input only, dev cheats for battles) and compare the live game with the
simulation after every action: map, position and event flags. Position is never written."""
import sys, os, time, pickle, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from sim import *
from rom import require_rom
from localmap import LocalMap
from play_nav import Nav
from play import IDS

import play_nav; play_nav.HOLDS = (1, 3, 14)
BTN = {1: "UP", 2: "RIGHT", 3: "DOWN", 4: "LEFT"}

class Diverged(Exception): pass

class Exec(Nav):
    def __init__(self, S):
        super().__init__(require_rom(), fast=1.0, host=os.environ.get('FFV_HOST', '127.0.0.1'))
        self.S = S
        self.normalize_speed()

    def fpos(self):
        """(map, x, y). $0AD6 is the map index (shared by some map ids, see Rom.prop_map_index); $0AD4 is the id as loaded, which entrances and
        treasures use, but it keeps the old id after a map exit (LoadParentMap only sets $0AD6). So: $0AD4 when its index agrees with $0AD6."""
        b = self.read(0xAD4, 6)
        ad4 = (b[0] | b[1] << 8) & 0xFFF; ad6 = (b[2] | b[3] << 8) & 0xFFF
        m = ad4 if ad4 < 512 and self.S.R.prop_map_index(ad4) == ad6 else ad6
        return (m, b[4], b[5])

    def live(self):
        m, x, y = self.fpos()
        raw = self.read(0xA14, 64)
        return {"map": m, "x": x, "y": y, "flags": int.from_bytes(raw, "little"), "local": self.u8(0xB53)}

    quick = False

    def timer_regs(self):
        """(enabled, counter, event) of the event timer"""
        b = self.read(0xAFB, 5)
        return b[0], b[1] | b[2] << 8, b[3] | b[4] << 8

    def timed_do(self, act):
        """a step or a wait inside a timed stretch (the sim counts 8 frames a tile): no settling, the timer is running"""
        if act.kind == "walkstep":
            moved, p = self.step(BTN[act.path[0]])
            if self.u8(0x18) == 1:                                       # random battle: the timer stands still in battle
                print("   battle during a timed step", flush=True)
                self.fight(); [self.press('A', 3, 20) for _ in range(20 if self.u8(0xB53) == 0 else 8)]
            return
        en, cnt, ev = self.timer_regs()
        if act.frames <= 8 or not en:
            self.wait(act.frames); return
        for _ in range(act.frames * 3):                                  # wait until the timer's event has run: it is restarted or switched off
            self.wait(1)
            en2, cnt2, ev2 = self.timer_regs()
            if not en2 or cnt2 > cnt + 2 or ev2 != ev: break
            cnt = cnt2
        self.wait(6)                                                     # let the event finish

    def do(self, s, act):
        """perform `act` from sim state s on the emulator"""
        if act.frames is not None and act.kind in ("walkstep", "tickwait"):
            self.timed_do(act); return
        if act.path:
            n, p = self.walk_dirs(act.path)
            if n < len(act.path) and act.npc is None:
                self.settle()
                return
            if self.quick: self.wait(24); self.wait_tile(); return
        if act.kind == "wait":                              # Karnak cell: poll until the prisoner's cell is reachable, talk, mash through the scene
            for _ in range(200):
                self.wait(600)
                n, p = self.walk_dirs([4, 4])
                if n == 2:
                    self.press("A", 4, 10); self.wait(40)
                    if self.u8(0xB57): break
                self.walk_dirs([2] * n)
            else: return
            for _ in range(400):
                self.press("A", 4, 40)
                if self.fpos()[1:] == (20, 61) and not self.u8(0xB57) and not self.u8(0x59): break
            self.wait(60); self.settle(); return
        if act.kind in ("land", "boardA", "dive", "surface"):                  # A lands the vehicle we ride / boards the one we stand on
            self.press("A", 4, 10); self.wait(90)
            if act.kind == "surface": self.wait(60)           # the surfacing scene
        if act.kind in ("talk", "turn", "press"):
            self.wait(30)                                   # let the last step's walk animation finish before turning / pressing
            if act.kind == "talk" and act.npc is not None and act.stand and self.chase_talk(act): self.settle(); return
            if act.stand: self.press(BTN[act.stand], 4, 6)
            if act.kind != "turn": self.press("A", 4, 10)
        self.settle()

    def chase_talk(self, act, tries=30):
        """Talk to an NPC that walks about: the event depends on the direction the party faces, so stand on the tile on the far side of the
        NPC's live tile (the party then faces it in direction act.stand), turn to it and press A; when the NPC has moved, walk there again.
        True once a message box or script started."""
        DX = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}
        d = act.stand; dx, dy = DX[d]
        for k in range(tries):
            raw = self.read(0x1477, 0x14 * 27); o = raw[0x14 * act.npc:0x14 * act.npc + 0x14]
            nx, ny = o[1] & 63, o[3] & 63
            m, x, y = self.fpos()
            if (x + dx, y + dy) != (nx, ny):
                path = self.reroute((nx - dx, ny - dy))
                if not path: self.wait(20); continue
                self.walk_dirs(path[:3]); continue
            self.wait(4)
            self.press(BTN[d], 4, 6)
            raw = self.read(0x1477, 0x14 * 27); o = raw[0x14 * act.npc:0x14 * act.npc + 0x14]
            if (o[1] & 63, o[3] & 63) != (nx, ny): continue          # it stepped away while we turned
            self.press("A", 4, 10); self.wait(20)
            if self.u8(0xB57) or self.u8(0xB59): return True
        return False

    def live_blocked(self, m, x, y):
        """tiles of the objects (NPCs) in the live object table of the current map ($1477, 0x14 bytes each: tile x +1, tile y +3), minus the party tile"""
        raw = self.read(0x1477, 0x14 * 27); out = set()
        for i in range(27):
            o = raw[0x14 * i:0x14 * i + 0x14]
            if o[8] | o[9] << 8 and (o[1], o[3]) != (x, y): out.add((o[1] & 63, o[3] & 63))
        return out

    def reroute(self, target, avoid=()):
        """A* (BFS) from the live position to tile `target` of the current local map using the live tile ids and the live object positions.
        -> direction list or None"""
        m, x, y = self.fpos()
        if m < 5: return None
        base = LocalMap(self.S.R, m)
        ids = b"".join(self.read(0x10000 + i, 1024) for i in range(0, 4096, 1024))
        mods = {(i % 64, i // 64): ids[i] for i in range(4096) if ids[i] != base.layer[i]}
        lm = LocalMap(self.S.R, m, mods, self.live_blocked(m, x, y) | set(avoid))
        z = lm.z_after(lm.props(x, y)[0], 0)
        start = (x, y, z); prev = {start: None}; q = collections.deque([start])
        while q:
            c = q.popleft()
            if (c[0], c[1]) == tuple(target):
                path = []
                while prev[c]: c, d = prev[c]; path.append(d)
                return path[::-1]
            for d, n in lm.moves(*c):
                if n not in prev: prev[n] = (c, d); q.append(n)
        return None

    def live_forced(self):
        """True when the party stands on an auto-move (conveyor / sand current) tile of the current local map"""
        m, x, y = self.fpos()
        if m < 5: return False
        t = self.u8(0x10000 + y * 64 + x)
        b2 = self.u8(0x1186 + 2 * t + 1)
        return bool(b2 & 0x80) and b2 != 0xFF

    def settle_push(self, limit=80):
        """wait until a conveyor push has finished: the party rests on a tile that is not an auto-move tile"""
        last = None
        for _ in range(limit):
            self.wait(2); cur = self.fpos()
            if cur == last and self.on_tile() and not self.live_forced(): return
            last = cur

    def walk_dirs(self, dirs):
        """walk with one tap per tile (hold-and-poll proved unreliable). Auto-move tiles (conveyors) can advance the party past the
        tile just stepped on, so a position that matches a later tile of the path is accepted too. Stops on a mismatch."""
        m0, cx, cy = self.fpos()
        self.cheat_field()
        DX = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}
        mask = 255 if m0 < 5 else 63
        tiles = []
        x, y = cx, cy
        for d in dirs:
            x, y = (x + DX[d][0]) & mask, (y + DX[d][1]) & mask; tiles.append((x, y))
        k = 0                                   # tiles[0:k] done
        stall = 0; post_battle = False; reroutes = 0; rerouted = False; n0 = len(dirs)
        while k < len(dirs):
            if k and dirs[k] != dirs[k - 1]: self.wait(24)
            if self.live_forced(): self.settle_push(); moved, p = True, self.pos()      # on a conveyor tile the engine moves the party by itself: no tap
            else:
                moved, p = self.step(BTN[dirs[k]])
                self.settle_push(); p = self.pos()
            if self.u8(0x18) == 1 or (not moved and (self.u8(0xB57) or not self.field_running())):       # random battle (or a message box, or its result windows): win it, then go on
                print("   battle during the walk at %s" % (self.pos(),), flush=True)
                self.fight(); post_battle = True; [self.press('A', 3, 20) for _ in range(20 if self.u8(0xB53) == 0 else 8)]; self.settle(); p = self.pos()
                if (p[1], p[2]) == (tiles[k - 1] if k else (cx, cy)): continue     # battle came before the step: redo it
            if p[0] != m0: return (n0 if rerouted else k + 1), self.pos()
            cur = (p[1], p[2])
            j = next((i for i in range(k, len(tiles)) if tiles[i] == cur), None)      # a conveyor (sand current) can carry the party over several path tiles in one tap
            if j is None and cur == (tiles[k - 1] if k else (cx, cy)) and not post_battle and stall >= 2 and reroutes < 40 and tiles:
                new = self.reroute(tiles[-1], avoid=())          # something is in the way (a wandering NPC): path again around the live objects
                reroutes += 1
                if new:
                    dirs = new; cx, cy = cur; x, y = cx, cy; tiles = []
                    for d in dirs:
                        x, y = (x + DX[d][0]) & mask, (y + DX[d][1]) & mask; tiles.append((x, y))
                    k = 0; stall = 0; rerouted = True
                    print("   re-routed around live objects: %d steps from (%d,%d)" % (len(dirs), cx, cy), flush=True)
                    continue
            if j is None and cur == (tiles[k - 1] if k else (cx, cy)) and stall < (60 if post_battle else 15):     # blocked (wandering NPC, e.g. castle guards): wait and retry
                stall += 1
                if post_battle: self.press('A', 3, 20)          # the victory screens (Gil, exp, items) can outlast the first A presses
                else: self.wait(45)
                continue
            if j is None:
                print("   walker stopped: at %s, expected %s (step %d/%d, msgbox %d, battle %d)" % (cur, tiles[k], k, len(dirs), self.u8(0xB57), self.u8(0x18)), flush=True)
                return k, self.pos()
            stall = 0; post_battle = False
            k = j + 1
        return (n0 if rerouted else k), self.pos()

    def _expected(self, s, path):
        x, y = s.x, s.y
        for d in path:
            dx, dy = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}[d]
            x, y = (x + dx) & (255 if s.map < 5 else 63), (y + dy) & (255 if s.map < 5 else 63)
        return x, y

def compare(S, live, s):
    out = []
    if (live["map"], live["x"], live["y"]) != (s.map, s.x, s.y): out.append("pos live %s sim %s" % ((live["map"], live["x"], live["y"]), (s.map, s.x, s.y)))
    d = (live["flags"] ^ s.flags) & S.read_mask & ~(1 << 511)
    if d:
        out.append("flags differ live-only %s sim-only %s" % ([i for i in range(512) if d >> i & 1 and live["flags"] >> i & 1],
                                                              [i for i in range(512) if d >> i & 1 and s.flags >> i & 1]))
    return out
