#!/usr/bin/env python3
"""FFV field screen renderer: rebuilds what the SNES shows from the game's own state (WRAM) plus ROM data, using ppu.py.

The renderer never reads video memory: VRAM is reconstructed exactly the way field-main.asm fills it (map graphics blocks, BG3 graphics,
party and NPC sprite graphics), the BG tilemaps come from the three map layers in WRAM $7F0000 and the tileset arrangement, the colours
from the palette buffer $0C00, the sprites from the OAM shadow $0200 and the PPU registers from the same direct-page variables the game
copies to the hardware ($45 brightness, $47 colour math, $49 sub screen, $4C fixed colour, $61-$6B scroll).
`Wram` is anything with `.read(offset, n)`; `StateWram` wraps a parsed snes9x state, `LiveWram` (play.Game) the emulator over UDP.
Source layout (all offsets are plain ROM file offsets): see notes/ff5-sfc-rom-map.txt of everything8215/ff5."""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ppu import PPU, decode_chars

DEFAULT_ROM = "/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc"
DP = 0xB00                      # field direct page

def u16(b, o): return b[o] | b[o + 1] << 8

class StateWram:
    def __init__(self, state): self.b = state.wram
    def read(self, off, n): return bytes(self.b[off:off + n])

class LiveWram:
    """WRAM of a running RetroArch (network commands READ_CORE_RAM); big reads are split into UDP-sized chunks"""
    def __init__(self, game): self.g = game
    def read(self, off, n):
        out = bytearray()
        while n > 0:
            k = min(n, 512)
            out += self.g.read(off, k); off += k; n -= k
        return bytes(out)

class Wram:
    """cached random access view; fetches each distinct range once"""
    def __init__(self, src): self.src = src; self.cache = {}
    def read(self, off, n):
        k = (off, n)
        if k not in self.cache: self.cache[k] = self.src.read(off, n)
        return self.cache[k]
    def u8(self, o): return self.read(o, 1)[0]
    def u16(self, o): b = self.read(o, 2); return b[0] | b[1] << 8
    def dp8(self, o): return self.u8(DP + o)
    def dp16(self, o): return self.u16(DP + o)

class Rom:
    """ROM data used by the field renderer"""
    def __init__(self, path=DEFAULT_ROM):
        self.r = open(path, "rb").read() if isinstance(path, str) else path

    def gfx_block(self, i):
        """one 0x2000-byte map graphics block (256 4bpp characters)"""
        p = struct.unpack_from("<I", self.r, 0x1C2D84 + 4 * i)[0]
        a = 0x1C2E24 + p
        return self.r[a:a + 0x2000].ljust(0x2000, b"\0")

    def bg3_gfx(self, i):
        p = u16(self.r, 0x1C0000 + 2 * i)
        a = 0x1C0024 + p
        return self.r[a:a + 0x1000].ljust(0x1000, b"\0")

    def party_gfx(self, g):
        """party sprite graphics for $0ADA = g (0 mini .. 8 moogle): 0x800 bytes loaded to VRAM word $6000"""
        ptr = (0xC800, 0xD000, 0xD800, 0xE000, 0xE800, 0xF000, 0xF800, 0x6C00, 0x8400)[g]
        return self.r[0x1A0000 + ptr:0x1A0000 + ptr + 0x800]

    def npc_gfx(self, g, flags1):
        """NPC sprite graphics index g (NPCProp byte 2) -> bytes (see _c03d28): list of 0x200-byte chunks loaded consecutively"""
        if g >= 0x67: ptr, bank, n = (g - 0x67) * 0x800 + 0x2A00, 0x1B0000, 4
        elif g >= 0x52: ptr, bank, n = (g - 0x52) * 0x200, 0x1B0000, 1
        elif g >= 0x4B:
            ptr, bank, n = (g - 0x4B) * 0x800 + 0xC800, 0x1A0000, (flags1 >> 6) or 4
        elif g >= 0x32:
            ptr, bank, n = (g - 0x32) * 0x400 + 0x6400, 0x1A0000, (flags1 >> 6) or 2
        else: ptr, bank, n = g * 0x200, 0x1A0000, 1
        return self.r[bank + ptr:bank + ptr + 0x200 * n]

    # math/main/sub tables of _c05bb8 (field map colour math presets: $48 math, TM, TS)
    def color_preset(self, i):
        return tuple(self.r[0x5BB8 + 3 * i:0x5BB8 + 3 * i + 3])

class Field:
    """builds a ppu.PPU for the current local-map screen from a Wram view"""
    def __init__(self, rom):
        self.rom = rom if isinstance(rom, Rom) else Rom(rom)

    # ---- pieces ----
    def build_vram(self, w, props, mapid):
        R = self.rom
        v = bytearray(65536)
        a = u16(props, 9) | props[11] << 16
        blocks = (a & 0x3F, (a >> 6) & 0x3F, (a >> 12) & 0x3F)
        for k, b in enumerate(blocks):
            v[k * 0x2000:(k + 1) * 0x2000] = R.gfx_block(b)
        d = (u16(props, 10) >> 10) & 0x3F
        v[0x8000:0x9000] = R.bg3_gfx(d)
        gfx = props[0]  # placeholder (kept for symmetry)
        party = w.u8(0xADA)
        if party < 9: v[0xC000:0xC000 + 0x800] = R.party_gfx(party)
        return v, blocks, d

    def tilemaps(self, w):
        arr = np.frombuffer(w.read(0x16E22, 0x800), dtype="<u2").reshape(4, 256)    # TL, TR, BL, BR
        maps = {}
        for n in (1, 2, 3):
            lay = np.frombuffer(w.read(0x10000 + 0x1000 * (n - 1), 0x1000), dtype=np.uint8).reshape(64, 64)
            tm = np.zeros((128, 128), dtype=np.uint16)
            tm[0::2, 0::2] = arr[0][lay]; tm[0::2, 1::2] = arr[1][lay]       # planes: TL, TR, BL, BR
            tm[1::2, 0::2] = arr[2][lay]; tm[1::2, 1::2] = arr[3][lay]
            maps[n] = tm
        return maps

    def scroll(self, w):
        """hardware BG scroll registers as written by _c04bc0 (positions are in half pixels)"""
        o = [w.u16(0x10A0 + 2 * i) for i in range(6)]
        sx = lambda v: v - 65536 if v & 0x8000 else v
        p = [w.dp16(0x61 + 2 * i) for i in range(6)]
        res = {}
        for n in (1, 2, 3):
            h = ((p[2 * (n - 1)] >> 1) + o[2 * (n - 1)]) & 0x3FF
            v = ((p[2 * (n - 1) + 1] >> 1) + o[2 * (n - 1) + 1]) & 0x3FF
            res[n] = (h, v)
        return res

    def regs(self, w, props, mapid):
        R = self.rom
        regs = {a: 0 for a in range(0x2100, 0x2134)}
        regs[0x2100] = (w.dp8(0x45) >> 4) & 15
        regs[0x2101] = 3
        regs[0x2105] = 9
        regs[0x2107] = 0x49
        regs[0x2108] = 0x52 if mapid == 0xDD else 0x51
        regs[0x2109] = 0x5A if mapid == 0xDD else 0x59
        if w.u16(0xAD4) == 0xBE: regs[0x2109] = 0x23
        regs[0x210B] = 0; regs[0x210C] = 4
        preset = R.color_preset(props[0x1110 - 0x110C] & 0x3F)
        regs[0x2123] = 0xBF if preset[1] & 1 else 0xBC     # BG1/BG2 window masks (see _c05b2d)
        regs[0x2124] = 0x0B
        regs[0x2125] = 0x33
        regs[0x2126], regs[0x2127], regs[0x2128], regs[0x2129] = 0x08, 0xF7, 0xFF, 0x00
        regs[0x212A] = 1
        regs[0x212C] = preset[1] | 1
        regs[0x212D] = w.dp8(0x49)
        regs[0x212E] = 0x17
        regs[0x2130] = 0x22
        regs[0x2131] = w.dp8(0x47)
        c = w.dp8(0x4C); inten = w.dp8(0x46) >> 3
        regs["fixed"] = tuple(inten if c & (0x20 << i) else 0 for i in range(3))
        return regs

    def build(self, w):
        mapid = w.u16(0xAD4)
        props = w.read(0x110C, 26)
        vram, blocks, d = self.build_vram(w, props, mapid)
        cg = [u16(w.read(0xC00, 512), 2 * i) for i in range(256)]
        oam = w.read(0x200, 0x220)
        p = PPU(vram, cg, oam, self.regs(w, props, mapid), self.scroll(w))
        p.bgmaps = self.tilemaps(w)
        return p

    def render(self, w):
        return self.build(w).render(1)

if __name__ == "__main__":
    from snes9x_state import State
    st = State(sys.argv[1])
    img = Field(DEFAULT_ROM).render(Wram(StateWram(st)))
    from PIL import Image
    Image.fromarray(img).save(sys.argv[2] if len(sys.argv) > 2 else "/tmp/field.png")
