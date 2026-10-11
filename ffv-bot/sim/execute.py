"""Play a simulator macro-path on the emulator test bed (pad input only, dev cheats for battles) and compare the live game with the
simulation after every action: map, position and event flags. Position is never written."""
import sys, os, time, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from sim import *
from play_nav import Nav
from play import IDS

import play_nav; play_nav.HOLDS = (1, 3, 14)
BTN = {1: "UP", 2: "RIGHT", 3: "DOWN", 4: "LEFT"}

class Diverged(Exception): pass

class Exec(Nav):
    def __init__(self, S):
        super().__init__(DEFAULT_ROM, fast=1.0, host=os.environ.get('FFV_HOST', '127.0.0.1'))
        self.S = S

    def fpos(self):
        """(map, x, y). The map comes from $0AD6 (the current map): $0AD4 keeps the old id after a map exit (LoadParentMap only sets $0AD6)."""
        b = self.read(0xAD4, 6)
        return ((b[2] | b[3] << 8) & 0xFFF, b[4], b[5])

    def live(self):
        m, x, y = self.fpos()
        raw = self.read(0xA14, 64)
        return {"map": m, "x": x, "y": y, "flags": int.from_bytes(raw, "little"), "local": self.u8(0xB53)}

    def do(self, s, act):
        """perform `act` from sim state s on the emulator"""
        if act.path:
            n, p = self.walk_dirs(act.path)
            if n < len(act.path):
                self.settle()
                return
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
        if act.kind in ("land", "boardA"):                  # A lands the vehicle we ride / boards the one we stand on
            self.press("A", 4, 10); self.wait(90)
        if act.kind in ("talk", "turn", "press"):
            self.wait(30)                                   # let the last step's walk animation finish before turning / pressing
            if act.stand: self.press(BTN[act.stand], 4, 6)
            if act.kind != "turn": self.press("A", 4, 10)
        self.settle()

    def walk_dirs(self, dirs):
        """walk with one tap per tile (hold-and-poll proved unreliable). Auto-move tiles (conveyors) can advance the party past the
        tile just stepped on, so a position that matches a later tile of the path is accepted too. Stops on a mismatch."""
        m0, cx, cy = self.fpos()
        DX = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}
        mask = 255 if m0 < 5 else 63
        tiles = []
        x, y = cx, cy
        for d in dirs:
            x, y = (x + DX[d][0]) & mask, (y + DX[d][1]) & mask; tiles.append((x, y))
        k = 0                                   # tiles[0:k] done
        stall = 0
        while k < len(dirs):
            if k and dirs[k] != dirs[k - 1]: self.wait(24)
            moved, p = self.step(BTN[dirs[k]])
            if self.u8(0x18) == 1 or (not moved and self.u8(0xB57)):       # random battle (or a message box): win it, then go on
                self.fight(); [self.press('A', 3, 20) for _ in range(20 if self.u8(0xB53) == 0 else 8)]; self.settle(); p = self.pos()
                if (p[1], p[2]) == (tiles[k - 1] if k else (cx, cy)): continue     # battle came before the step: redo it
            if p[0] != m0: return k + 1, self.pos()
            cur = (p[1], p[2])
            j = next((i for i in range(k, min(k + 4, len(tiles))) if tiles[i] == cur), None)
            if j is None and cur == (tiles[k - 1] if k else (cx, cy)) and stall < 15:     # blocked (wandering NPC, e.g. castle guards): wait and retry
                stall += 1; self.wait(45); continue
            if j is None: return k, self.pos()
            stall = 0
            k = j + 1
        return k, self.pos()

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
