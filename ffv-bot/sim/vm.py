"""Event VM: executes FFV event scripts over an abstract game State and enumerates the outcomes (branches).

Faithful to field-main.asm for everything that influences where the party can go: event flags, NPC flags, tile changes
(f3/f4), map loads (e0/e1/e3, d6, bf01), party steps (01-04 walk one tile ignoring collisions during events), vehicles
(d2, c2, d6), object moves, nested events (cd) and blocks (c7, ce, cf). Dialog, music, battles (assumed won), items, jobs
are no-ops here (item/job gains are recorded in state.log for inspection).
Branches: f0 yes/no (both), f1 random (all options), e2 battle that can be lost (win path only), e7 (ending only).
"""
import copy
from events import EventRom, Cmd

FACE_DX = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}      # $0adb: 0 up, 1 right, 2 down, 3 left

class State:
    __slots__ = ("flags", "npcf", "map", "x", "y", "z", "face", "veh", "vtab", "parent", "mods", "npcpos", "npcvis", "mods2", "npcface", "log", "ended", "gameover", "pc")
    def __init__(self):
        self.flags = 0           # event flags 0-511 as an int bitmask
        self.npcf = 0            # npc flags
        self.map = 0; self.x = 0; self.y = 0; self.z = 1; self.face = 2
        self.veh = 0             # $0adc: current vehicle (0 foot)
        self.vtab = [[0, 0x80, 0, 0] for _ in range(6)]    # $0add + 4*i : [map<<5|type bits, flags (0x80 hidden), x, y]
        self.parent = (0, 0, 0)  # $0af5 map, $0af7 x/y
        self.mods = {}           # (x, y) -> tile id overrides for the current local map's BG1
        self.npcpos = {}         # object index -> (x, y) moved NPCs on the current map
        self.npcvis = {}         # object index -> bool, set by object actions 09/0a on the current map
        self.mods2 = {}          # (layer, x, y) -> tile id overrides of BG2/BG3 (layer 1, 2); visual only, not part of key()
        self.npcface = {}        # object index -> graphic frame (facing * 2) set by d3/d8/moves; visual only, not part of key()
        self.log = []
        self.ended = False; self.gameover = False
        self.pc = None

    def clone(self):
        s = State.__new__(State)
        for k in State.__slots__:
            v = getattr(self, k, None)
            if k in ("vtab",): v = [list(a) for a in v]
            elif k in ("mods", "npcpos", "npcvis", "mods2", "npcface"): v = dict(v or {})
            elif k == "log": v = list(v)
            setattr(s, k, v)
        return s

    def key(self):
        return (self.flags, self.npcf, self.map, self.x, self.y, self.z, self.veh, tuple(map(tuple, self.vtab)),
                tuple(sorted(self.mods.items())), tuple(sorted(self.npcpos.items())), tuple(sorted(self.npcvis.items())), self.parent, self.ended)

    def flag(self, n): return bool(self.flags >> n & 1)
    def set_flag(self, n, v=True):
        if v: self.flags |= 1 << n
        else: self.flags &= ~(1 << n)
    def npc_flag(self, n): return bool(self.npcf >> n & 1)
    def set_npc(self, n, v=True):
        if v: self.npcf |= 1 << n
        else: self.npcf &= ~(1 << n)

class VM:
    MAX_BRANCH = 64

    def __init__(self, rom):
        self.rom = rom
        self.E = EventRom(rom.r)
        self._off_idx = {}
        self.trace = None

    def offidx(self, ev):
        if ev not in self._off_idx:
            self._off_idx[ev] = {c.off: i for i, c in enumerate(self.E.cmds(ev))}
        return self._off_idx[ev]

    # ---- map load helpers ----
    def load_map(self, s, m, x, y, face, veh=None):
        """map load via e0/e1/e3/entrance: resets per-map state"""
        s.map = m; s.x = x; s.y = y; s.face = face; s.z = 1
        s.mods = {}; s.npcpos = {}; s.npcvis = {}; s.mods2 = {}; s.npcface = {}
        s.flags &= ~(0xFFFF << 256)                   # LoadSubMap and LoadWorldMap both clear $0a34/$0a35 (map-local flags 256-271)
        if veh is not None: s.veh = veh

    def run(self, s0, ev):
        """Execute event `ev` from state s0 (not modified). -> list of final States (deduplicated)."""
        outs, seen = [], set()
        work = [(s0.clone(), [(ev, 0, None, 1)])]    # stack of frames: (event, cmd index, block end offset or None, repeats left)
        steps = 0
        while work:
            s, stack = work.pop()
            while True:
                steps += 1
                if steps > 200000: raise RuntimeError("event VM runaway at event %d" % ev)
                if not stack or s.ended or s.gameover:
                    k = s.key()
                    if k not in seen: seen.add(k); outs.append(s)
                    break
                e, pc, end, rep = stack[-1]
                cmds = self.E.cmds(e)
                if pc >= len(cmds) or (end is not None and cmds[pc].off >= end):
                    stack.pop(); continue
                c = cmds[pc]
                op = c.op
                nxt = lambda n=1: stack.__setitem__(-1, (e, pc + n, end, rep))
                if op == 0xFF:
                    stack.pop(); continue
                if op in (0xCE, 0xCF, 0xC7):
                    count = 1 if op == 0xC7 else c.b[1]
                    vec = c.b[1] if op == 0xC7 else c.b[2]
                    body_start = c.off + (2 if op == 0xC7 else 3)
                    body_end = body_start + vec
                    stack[-1] = (e, self.offidx(e).get(body_end, len(cmds)), end, rep)
                    for _ in range(count):               # the body runs `count` times (bodies are short and acyclic)
                        stack.append((e, self.offidx(e).get(body_start, len(cmds)), body_end, 1))
                    continue
                if op == 0xCD:
                    stack[-1] = (e, pc + 1, end, rep)
                    stack.append((c.b[1] | c.b[2] << 8, 0, None, 1))
                    if len(stack) > 40: raise RuntimeError("event call depth")
                    continue
                if op == 0xF0:
                    # yes/no: first option runs the next 4 bytes (cd lo hi ff), second skips them
                    cm = self.E.cmds(e)
                    skip, k = 0, pc + 1
                    while skip < 4 and k < len(cm): skip += cm[k].n; k += 1
                    s2, st2 = s.clone(), [f for f in stack]
                    st2[-1] = (e, k, end, rep)
                    work.append((s2, st2))
                    nxt(); continue
                if op == 0xF1:
                    mask = c.b[1]
                    cm = self.E.cmds(e)
                    base = c.off + 2
                    ks = sorted({k for k in range(mask + 1) if (k & ~mask) == 0})
                    for k in ks[1:]:
                        tgt = self.offidx(e).get(base + 4 * k)
                        if tgt is None: continue
                        st2 = [f for f in stack]; st2[-1] = (e, tgt, end, rep)
                        work.append((s.clone(), st2))
                    tgt = self.offidx(e).get(base + 4 * ks[0], len(cm))
                    stack[-1] = (e, tgt, end, rep); continue
                if op == 0xE2:
                    # battle that can be lost: continue on the win path (skip the 4-byte lose branch)
                    cm = self.E.cmds(e)
                    skip, k = 0, pc + 1
                    while skip < 4 and k < len(cm): skip += cm[k].n; k += 1
                    stack[-1] = (e, k, end, rep); continue
                if op == 0xE7:
                    s.ended = True; continue
                if self.trace: self.trace(s, e, c)
                self.apply(s, c)
                nxt()
        return outs

    def apply(self, s, c):
        op, b = c.op, c.b
        if 1 <= op <= 4 or op == 6: s.flags &= ~(3 << 508)          # every party step clears the temporary flags 508/509
        if 1 <= op <= 4:                                  # party walks one tile (camera = party)
            dx, dy = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}[op]
            s.x = (s.x + dx) & 255 if s.map < 5 else (s.x + dx) & 63
            s.y = (s.y + dy) & 255 if s.map < 5 else (s.y + dy) & 63
            s.face = op - 1
        elif op == 5: pass                                # hop in place (event 40, live)
        elif op == 6:                                     # jump: advances one tile in the facing direction (event 1050, live)
            dx, dy = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}[s.face]
            s.x = (s.x + dx) & 255 if s.map < 5 else (s.x + dx) & 63
            s.y = (s.y + dy) & 255 if s.map < 5 else (s.y + dy) & 63
        elif op == 0x0C: s.face = (s.face + 2) & 3          # party action $0c flips the facing (eor #$04 on the direction byte)
        elif 0x10 <= op < 0x18:
            s.face = (op - 0x10) >> 1
        elif op in (0xA2, 0xA3): s.set_flag(b[1], op == 0xA2)
        elif op in (0xA4, 0xA5): s.set_flag(256 + b[1], op == 0xA4)
        elif op in (0xCA, 0xCB): s.set_npc(b[1] | b[2] << 8, op == 0xCA)
        elif op in (0xE0, 0xE1, 0xE3):
            m = b[1] | (b[2] & 3) << 8
            if b[2] & 0x40 and s.map < 5: s.parent = (s.map, s.x, s.y)
            if m < 5: x, y = b[3], b[4]; face = s.face
            else: x, y, face = b[3] & 0x3F, b[4], b[3] >> 6
            veh = b[5] >> 5
            self.load_map(s, m, x, y, face, veh)
            if veh:
                v = s.vtab[veh - 1]; v[0] = (v[0] & 0xE0) | (b[5] & 0x1F); v[1] = 0x80      # hidden: the party is inside
                if m < 5: v[2], v[3] = x, y
        elif op == 0xD6:
            m = (b[1] | b[2] << 8) & 0x1FFF; veh = b[3] >> 5
            s.map = m; s.veh = veh; s.mods = {}; s.npcpos = {}; s.npcvis = {}; s.mods2 = {}; s.npcface = {}
            s.flags &= ~(0xFFFF << 256)
            if veh:
                v = s.vtab[veh - 1]; v[0] = (v[0] & 0xE0) | (b[3] & 0x1F); v[1] = 0x80; v[2], v[3] = s.x, s.y
        elif op == 0xD2:                                  # show vehicle: map, x, y, vvvaaagg
            i = (b[4] >> 5) - 1
            if 0 <= i < 6: s.vtab[i] = [b[1] << 5 | b[4] & 0x1F, 0x00, b[2], b[3]]
        elif op == 0xC2:
            i = b[1] - 1
            if 0 <= i < 6: s.vtab[i][1] |= 0x80
        elif op in (0xF3, 0xF4):
            layer = b[1] >> 6
            if layer == 0:
                w, h = (b[3] & 15) + 1, (b[3] >> 4) + 1
                if op == 0xF3: x0, y0 = b[1] & 0x3F, b[2]
                else: x0, y0 = (b[1] & 0x3F) + s.x, (b[2] + s.y) & 255
                for j in range(h):
                    for i in range(w): s.mods[((x0 + i) & 63, (y0 + j) & 63)] = b[4 + j * w + i]
            elif layer < 3:
                w, h = (b[3] & 15) + 1, (b[3] >> 4) + 1
                if op == 0xF3: x0, y0 = b[1] & 0x3F, b[2]
                else: x0, y0 = (b[1] & 0x3F) + s.x, (b[2] + s.y) & 255
                for j in range(h):
                    for i in range(w): s.mods2[(layer, (x0 + i) & 63, (y0 + j) & 63)] = b[4 + j * w + i]
        elif op == 0xD3:                                  # set object position (absolute)
            s.npcpos[b[1] & 0x7F] = (b[2] & 0x3F, b[3]); s.npcface[b[1] & 0x7F] = (b[2] >> 6) * 2
        elif op == 0xD8:                                  # relative to the party
            dx = b[2] >> 4; dx = dx - 16 if dx & 8 else dx
            dy = b[2] & 15; dy = dy - 16 if dy & 8 else dy
            s.npcpos[b[1] & 0x1F] = ((s.x + dx) & 63, (s.y + dy) & 63); s.npcface[b[1] & 0x1F] = (b[1] >> 6) * 2
        elif 0x80 <= op < 0xA0:
            o, a = op - 0x80, b[1]
            if 1 <= a <= 4:
                p = s.npcpos.get(o)
                if p is None: p = self.npc_home(s.map, o)
                if p is not None:
                    dx, dy = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}[a]
                    s.npcpos[o] = ((p[0] + dx) & 63, (p[1] + dy) & 63); s.npcface[o] = (a - 1) * 2
            elif a == 9: s.npcvis[o] = True
            elif a == 0x0A: s.npcvis[o] = False
        elif op == 0xBF:
            sub = b[1]
            if sub == 0: s.gameover = True
            elif sub == 1:
                m, x, y = s.parent; self.load_map(s, m, x, y, s.face)
            elif sub == 0x0F: s.ended = True; s.log.append("ending")
        elif op in (0xAA, 0xAB, 0xB7, 0xC6, 0xAC):
            s.log.append((op, b[1]))

    def npc_home(self, m, o):
        if m < 5: return None
        n = self.rom.npcs(m)
        return (n[o]["x"], n[o]["y"]) if o < len(n) else None
