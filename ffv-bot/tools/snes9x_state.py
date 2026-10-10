"""Read a RetroArch/snes9x save state (RZIP-wrapped snes9x freeze format) and expose VRAM, CGRAM, OAM, WRAM and PPU registers.
Used to validate the screen renderer against what the emulator really had in video memory.  Usage: snes9x_state.py <state>"""
import re, struct, zlib, sys

def unrzip(d):
    if d[:8] != b"#RZIPv\x01#": return d
    chunk = struct.unpack_from("<I", d, 8)[0]; total = struct.unpack_from("<Q", d, 12)[0]
    o, out = 20, bytearray()
    while o < len(d) and len(out) < total:
        n = struct.unpack_from("<I", d, o)[0]; o += 4
        out += zlib.decompress(d[o:o + n]); o += n
    return bytes(out)

class State:
    def __init__(self, path):
        d = unrzip(open(path, "rb").read())
        self.blocks = {}
        o = d.find(b"NAM:")
        while o != -1 and o < len(d):
            m = re.match(rb"([A-Z0-9]{3}):(\d{6}):", d[o:o + 11])
            if not m: break
            n = int(m.group(2)); self.blocks[m.group(1).decode()] = d[o + 11:o + 11 + n]; o += 11 + n
        self.raw = d
        self.vram, self.wram = self.blocks.get("VRA", b""), self.blocks.get("RAM", b"")

if __name__ == "__main__":
    s = State(sys.argv[1])
    for k, v in s.blocks.items(): print(k, len(v))

def ppu_inputs(st):  # snes9x freeze data stores multi-byte integers big-endian
    """-> dict(vram, cgram, oam, regs, scroll, fixed) taken from a parsed State (registers are FillRAM's last-written bytes)"""
    import struct
    p, f = st.blocks["PPU"], st.blocks["FIL"]
    regs = {a: f[a] for a in range(0x2100, 0x2134)}
    scroll = {}
    for n in range(3):
        o = 14 + 11 * n
        sc, h, v, bgsize, nb, scs = struct.unpack_from(">HHHBHH", p, o)
        scroll[n + 1] = (h & 0x3FF, v & 0x3FF)
    cg = struct.unpack_from(">256H", p, 64)
    oam = p[2003:2003 + 544]
    a, b, c, d, cx, cy, hx, vy = struct.unpack_from(">hhhhhhhh", p, 2573)
    m7 = {"a": a, "b": b, "c": c, "d": d, "cx": cx, "cy": cy, "hofs": hx, "vofs": vy}
    return {"m7": m7, "vram": st.vram, "cgram": cg, "oam": oam, "regs": regs, "scroll": scroll}
