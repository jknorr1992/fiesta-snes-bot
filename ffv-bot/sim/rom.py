"""ROM tables for FFV (FF5r Clean build; offsets are plain file offsets). Layout verified against the everything8215/ff5
disassembly (src/field/field-main.asm) and notes/ff5-sfc-rom-map.txt."""
import functools, struct, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from ffv_decomp import decompress, world_row
from events import u16

DEFAULT_ROM = "/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc"

class Rom:
    def __init__(self, path=DEFAULT_ROM):
        self.r = open(path, "rb").read()
        r = self.r
        # layout pointers: u16, bank bumps whenever a pointer is smaller than the previous one
        ptrs = [u16(r, 0xB0000 + 2 * i) for i in range(324)]
        bank, prev, self.layout_off = 0xCB, None, []
        for p in ptrs:
            if prev is not None and p < prev: bank += 1
            self.layout_off.append((bank - 0xC0) * 0x10000 + p); prev = p

    # ---- map properties (26 bytes at 0xE9C00) ----
    @functools.lru_cache(maxsize=None)
    def props(self, m): return self.r[0xE9C00 + 26 * m:0xE9C00 + 26 * m + 26]

    def prop_map_index(self, m):
        """$0ad6 after load: props word & 0xFFF (identical to m for ordinary maps)."""
        return u16(self.props(m), 0) & 0xFFF

    @functools.lru_cache(maxsize=None)
    def layer(self, m, bg=0):
        """64x64 tile-id bytes of BG1 (bg=0), BG2, BG3, as loaded into $7f0000/$7f1000/$7f2000."""
        p = self.props(m)
        if bg == 0: idx = (u16(p, 12) & 0x3FF) - 1
        elif bg == 1: idx = ((u16(p, 13) & 0xFFC) >> 2) - 1
        else: idx = ((u16(p, 14) & 0x3FF0) >> 4) - 1
        if idx < 0 or idx >= 324: return bytes(4096)
        d = decompress(self.r, self.layout_off[idx], True)
        return d.ljust(4096, b"\0")[:4096]

    @functools.lru_cache(maxsize=None)
    def tileprops(self, m):
        """256 x (byte1, byte2) tile properties of local map m."""
        idx = self.props(m)[5]
        d = decompress(self.r, 0xFC540 + u16(self.r, 0xFC540 + 2 * idx), True)
        d = d.ljust(512, b"\0")
        return [(d[2 * i], d[2 * i + 1]) for i in range(256)]

    # ---- triggers ----
    def _sub(self, base, m, size):
        r = self.r
        a, b = u16(r, base + 2 * m), u16(r, base + 2 * (m + 1))
        return [base + i for i in range(a, b, size)]

    def event_triggers(self, m):
        return [(self.r[o], self.r[o + 1], u16(self.r, o + 2)) for o in self._sub(0xE2400, m, 4)]

    def entrances(self, m):
        out = []
        for o in self._sub(0xE36C0, m, 6):
            r = self.r
            w = u16(r, o + 2)
            out.append({"x": r[o], "y": r[o + 1], "map": w & 0x3FF, "flags": r[o + 3], "dx": r[o + 4], "dy": r[o + 5]})
        return out

    def npcs(self, m):
        r = self.r
        out = []
        for o in self._sub(0xE59C0, m, 7):
            out.append({"script": u16(r, o) & 0x3FFF, "gfx": r[o + 2], "x": r[o + 3] & 0x3F, "y": r[o + 4] & 0x3F,
                        "flags": r[o + 5], "pal": r[o + 6]})
        return out

    def treasures(self, m):
        """[(x, y, b2, b3, index)] chests of local map m. MapTreasures at 0xD13000 (1 byte per map: first treasure index)."""
        r = self.r
        a, b = r[0xD13000 + m], r[0xD13000 + m + 1]
        return [(r[0xD13210 + 4 * i], r[0xD13210 + 4 * i + 1], r[0xD13210 + 4 * i + 2], r[0xD13210 + 4 * i + 3], i) for i in range(a, b)]

    # ---- condition scripts (event triggers: 0x18E080, NPC scripts: 0xE0000) ----
    @functools.lru_cache(maxsize=None)
    def trigger_script(self, idx):
        r = self.r
        a, b = u16(r, 0x18E080 + 2 * idx), u16(r, 0x18E080 + 2 * (idx + 1))
        return parse_cond(r[0x18E080 + a:0x18E080 + b])

    @functools.lru_cache(maxsize=None)
    def npc_script(self, idx):
        r = self.r
        a, b = u16(r, 0xE0000 + 2 * idx), u16(r, 0xE0000 + 2 * (idx + 1))
        data = r[0xE0000 + a:0xE0000 + b]
        # NPC scripts: condition groups, then $f0, then dialog text; skip commands by length NPCCmdNumBytes
        return parse_cond(data, npc=True)

    def initial_npc_flags(self):
        """set of NPC-flag indices set at game start (0x18E000, 1 bit each, LSB first)."""
        return {i for i in range(1024) if self.r[0x18E000 + (i >> 3)] >> (i & 7) & 1}

NPC_CMD_BYTES = [0, 0, 0, 0, 0, 4, 1, 2, 4, 5, 4, 2, 2, 2, 2, 3]   # $f0..$ff

def parse_cond(data, npc=False):
    """-> list of (conds, event). cond = ("flag", n, want) | ("ram", op, addr, cmp, val) | ("facing", d) | ("button",).
    Groups end with $ff + u16 event; the first group whose conditions all hold runs.
    (Same condition opcodes for trigger scripts and NPC scripts; NPC scripts additionally start with dialog data after $f0.)"""
    groups, conds, i = [], [], 0
    while i < len(data):
        op = data[i]
        if op == 0xFF:
            if i + 2 >= len(data): break
            groups.append((tuple(conds), u16(data, i + 1))); conds = []; i += 3
        elif op in (0xFE, 0xFD, 0xFC, 0xFB):
            conds.append(("flag", data[i + 1] + (256 if op in (0xFC, 0xFB) else 0), op in (0xFE, 0xFC))); i += 2
        elif op in (0xFA, 0xF8):            # 1-byte compare / boolean-and of RAM $0500+addr
            addr = u16(data, i + 1)
            conds.append(("ram1", op, addr & 0x3FFF, addr >> 14, data[i + 3])); i += 4
        elif op in (0xF9, 0xF5):            # 2-byte
            addr = u16(data, i + 1)
            conds.append(("ram2", op, addr & 0x3FFF, addr >> 14, u16(data, i + 3))); i += 5 if op == 0xF9 else 5
        elif op == 0xF7: conds.append(("facing", data[i + 1])); i += 2
        elif op == 0xF6: conds.append(("button",)); i += 1
        elif op == 0xF0 and npc: break
        else:
            conds.append(("?", op)); i += 1
    return groups
