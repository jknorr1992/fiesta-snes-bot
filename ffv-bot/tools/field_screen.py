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

    OVERLAY_GFX = 0xDF00            # C0/DF00: 1bpp overlay sprite graphics
    OVERLAY_TILES = 0x2667          # C0/2667 OverlayTilesTbl (27 tilesets x 16 bytes)

    def npcs(self, m):
        """NPC properties of local map m (7 bytes each at 0xE59C0 + pointer table): script, gfx, x, y, flags, pal"""
        r = self.r
        a, b = u16(r, 0xE59C0 + 2 * m), u16(r, 0xE59C0 + 2 * (m + 1))
        out = []
        for o in range(0xE59C0 + a, 0xE59C0 + b, 7):
            out.append({"script": u16(r, o) & 0x3FFF, "flags1": r[o + 1], "gfx": r[o + 2], "x": r[o + 3] & 0x3F, "y": r[o + 4] & 0x3F,
                        "flags": r[o + 5], "pal": r[o + 6]})
        return out

    # ---- world map data ----
    WORLD_GFX = 0x1B8000          # DB/8000: 3 world tilesets of 256 linear 4bpp characters
    WORLD_ATTR = 0xFF9C0          # CF/F9C0: per-character palette attribute (256 bytes per tileset)
    WORLD_TILESET = 0xFF0C0       # CF/F0C0: 16x16 tile arrangements (4 planes of 192 bytes: TL, TR, BL, BR) per tileset
    WORLD_TILESET_OF_MAP = (0, 1, 0, 2, 2)

    def world_chars(self, wmap):
        """mode 7 character pixels for world map `wmap`: (256, 64) bytes = 4bpp nibbles | attribute (see TfrWorldGfx)"""
        t = self.WORLD_TILESET_OF_MAP[wmap]
        gfx = np.frombuffer(self.r, dtype=np.uint8, count=0x2000, offset=self.WORLD_GFX + t * 0x2000).reshape(256, 32)
        attr = np.frombuffer(self.r, dtype=np.uint8, count=256, offset=self.WORLD_ATTR + t * 256)
        out = np.zeros((256, 32, 2), dtype=np.uint8)
        out[:, :, 0] = gfx & 15; out[:, :, 1] = gfx >> 4
        out |= attr[:, None, None]
        return out.reshape(256, 64)

    def world_arrangement(self, wmap):
        t = self.WORLD_TILESET_OF_MAP[wmap]
        return np.frombuffer(self.r, dtype=np.uint8, count=768, offset=self.WORLD_TILESET + t * 768).reshape(4, 192)

    # math/main/sub tables of _c05bb8 (field map colour math presets: $48 math, TM, TS)
    def color_preset(self, i):
        return tuple(self.r[0x5BB8 + 3 * i:0x5BB8 + 3 * i + 3])

_T96F4 = (0x00, 0x88, 0x10, 0x98, 0x20, 0xA8, 0x30, 0xB8, 0x80, 0x08, 0x90, 0x18, 0xA0, 0x28, 0xB0, 0x38)

def _water_update(m, f):
    """one frame of the world water animation on a window m whose index 0 is $7F8622 (_c0964c on m[0:0x100], _c09722 on m[0x100:0x180]);
    m may be a list of anything (used with index lists to derive the permutation)"""
    x = _T96F4[f & 15] | 7
    v = m[x]
    for _ in range(7): m[x] = m[x - 1]; x -= 1
    x = (x & 0xF8) + 0x47
    m[x - 0x47] = m[x]                         # sta $7f85db,x
    for _ in range(7): m[x] = m[x - 1]; x -= 1
    m[x & 0xF8] = v
    x = (0, 4, 2, 6, 1, 5, 3, 7)[(f & 0x0E) >> 1] | 0x70
    v = m[0x100 + x + 8]
    while True:
        m[0x100 + x + 8] = m[0x100 + x]
        x -= 8
        if (x & 0xF8) == 0: break
    m[0x100 + x + 8] = m[0x100 + x]
    m[0x100 + x] = v

def water_unstep(m, f):
    """undo one animation step (frame counter value f) on the 0x180-byte buffer window m, in place"""
    idx = list(range(len(m)))
    _water_update(idx, f)                      # idx[i] = source index of new[i]
    old = bytes(m)
    for i, p in enumerate(idx): m[p] = old[i]

class Field:
    """builds a ppu.PPU for the current local-map screen from a Wram view"""
    synth_oam = False           # True: rebuild the OAM with field_sprites instead of reading the shadow at $0200

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
        # _c08b53: characters $300-$3CF are solid colour-1 tiles (bit plane 0 all set), used by shadow overlays
        v[0x6000:0x6000 + 0xD0 * 32] = (b"\xff\x00" * 8 + b"\x00\x00" * 8) * 0xD0
        d = (u16(props, 10) >> 10) & 0x3F
        v[0x8000:0x9000] = R.bg3_gfx(d)
        party = w.u8(0xADA)
        if party < 9: v[0xC000:0xC000 + 0x800] = R.party_gfx(party)
        self.load_npc_gfx(v, mapid)
        self.load_overlay_gfx(v, props[8])
        self.load_map_anim(w, v, props)
        return v, blocks, d

    ANIM_GFX = 0x1F9B00         # DF/9B00 MapAnimGfx

    def load_map_anim(self, w, v, props):
        """animated tile graphics (_c0996d): 12 slots at $1417 (flags, counter, offset, source) are DMA'd every frame to the last 32 BG
        characters ($2E00 words) and the BG3 animation characters ($4780 words), from ROM or from the WRAM buffer at $7F8622"""
        if props[0x1110 - 0x110C] & 0x80: return
        slots = w.read(0x1417, 0x60)
        for i in range(12):
            fl, src = slots[8 * i], u16(slots, 8 * i + 4)
            n = 0x80 if i < 8 else 0x40
            data = w.read(0x10000 + src, n) if fl & 0x80 else self.rom.r[0x1F0000 + src:0x1F0000 + src + n]
            dw = 0x2E00 + 0x40 * i if i < 8 else 0x4780 + 0x20 * (i - 8)
            v[2 * dw:2 * dw + n] = data

    def load_npc_gfx(self, v, mapid):
        """NPC sprite graphics, loaded one after another from VRAM word $6500 (_c03d28); an NPC with the same graphics as the previous one shares them"""
        R = self.rom
        pos, prev = 0x6500, None
        for e in R.npcs(mapid):
            g = e["gfx"]
            if prev is not None and g == prev: continue
            prev = g
            if g == 0xFF: continue
            data = R.npc_gfx(g, e["flags1"])
            v[pos * 2:pos * 2 + len(data)] = data
            pos += len(data) // 2

    def load_overlay_gfx(self, v, tileset):
        """overlay sprite graphics (LoadOverlayGfx): 16 overlay tiles of 1bpp data placed in bit plane 1 at VRAM words $7C00/$7D00"""
        R = self.rom
        for k in range(16):
            idx = R.r[R.OVERLAY_TILES + tileset * 16 + k]
            x = idx * 8
            dest = (0x7C00 if k < 8 else 0x7D00) + k * 32
            for half, base in ((0, dest), (1, dest + 0x100)):
                for c in range(2):
                    for row in range(8):
                        b = R.r[R.OVERLAY_GFX + x + half * 128 + c * 8 + row]
                        o = (base + c * 16 + row) * 2
                        v[o] = 0; v[o + 1] = b
                        for z in range(8, 16): v[(base + c * 16 + z) * 2:(base + c * 16 + z) * 2 + 2] = b"\0\0"


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

    def build_world(self, w):
        """mode 7 screen of the world map (map index $0AD6 < 5)"""
        R = self.rom
        wmap = w.u16(0xAD4)
        chars = R.world_chars(wmap); arr = R.world_arrangement(wmap)         # arr planes: TL, TR, BL, BR
        vram = bytearray(65536)
        px, py = w.u8(0xAD8), w.u8(0xAD9)
        band = np.frombuffer(w.read(0x10000, 0x4000), dtype=np.uint8).reshape(64, 256)   # band[y & 63][x]
        tmap = np.zeros((128, 128), dtype=np.uint8)
        for ty in range(64):
            wy = py + ((ty - py + 32) % 64 - 32)
            row = band[wy & 63]
            for tx in range(64):
                wx = (px + ((tx - px + 32) % 64 - 32)) & 255
                t = row[wx]
                if t >= 192: continue
                tmap[2 * ty, 2 * tx] = arr[0][t]; tmap[2 * ty, 2 * tx + 1] = arr[1][t]
                tmap[2 * ty + 1, 2 * tx] = arr[2][t]; tmap[2 * ty + 1, 2 * tx + 1] = arr[3][t]
        v = np.frombuffer(vram, dtype=np.uint8) if False else None
        buf = np.zeros(65536, dtype=np.uint8)
        buf[0:32768:2] = tmap.reshape(-1)
        # characters: char c pixel p lives at byte 2 * (c * 64 + p) + 1
        buf[1:2 * 256 * 64:2] = chars.reshape(-1)
        party = w.u8(0xADA)
        if party < 9:
            pg = np.frombuffer(R.party_gfx(party)[:0x200], dtype=np.uint8)
        # animated water: the game scrolls pixel rows in WRAM buffers $7F8622-$7F87A1 once per frame (_c0964c, _c09722) *after* DMAing them
        # to characters $62, $72 and $87, so the VRAM content is the buffer one update step back
        if w.u16(0xAD6) < 3:
            win = bytearray(w.read(0x18622, 0x180))
            water_unstep(win, (w.dp8(0x3E) - 1) & 255)
            buf[2 * 0x1880 + 1:2 * 0x1880 + 0x100:2] = np.frombuffer(bytes(win[0:0x80]), dtype=np.uint8)
            buf[2 * 0x1C80 + 1:2 * 0x1C80 + 0x100:2] = np.frombuffer(bytes(win[0x80:0x100]), dtype=np.uint8)
            if (w.u16(0xAD6) & 1) == 0: buf[2 * 0x21C0 + 1:2 * 0x21C0 + 0x100:2] = np.frombuffer(bytes(win[0x100:0x180]), dtype=np.uint8)
        vram = bytearray(buf.tobytes())
        if party < 9: vram[0xC000:0xC000 + 0x200] = R.party_gfx(party)[:0x200]
        cg = [u16(w.read(0xC00, 512), 2 * i) for i in range(256)]
        if self.synth_oam:
            from field_sprites import Mem, synthesize
            oam = synthesize(Mem.from_wram(w, self.rom.r))
        else: oam = w.read(0x200, 0x220)
        regs = {a: 0 for a in range(0x2100, 0x2134)}
        regs[0x2100] = (w.dp8(0x45) >> 4) & 15
        regs[0x2101] = 3; regs[0x2105] = 7
        regs[0x2123] = 0xBF; regs[0x2124] = 0x0B; regs[0x2125] = 0x33
        regs[0x2126], regs[0x2127], regs[0x2128], regs[0x2129] = 0x08, 0xF7, 0xFF, 0x00
        regs[0x212A] = 1; regs[0x212C] = 0x13; regs[0x212D] = w.dp8(0x49); regs[0x212E] = 0x17
        regs[0x2130] = 0x22; regs[0x2131] = w.dp8(0x47)
        c = w.dp8(0x4C); inten = w.dp8(0x46) >> 3
        regs["fixed"] = tuple(inten if c & (0x20 << i) else 0 for i in range(3))
        p = PPU(vram, cg, oam, regs, {})
        o = [w.u16(0x10A0 + 2 * i) for i in range(2)]
        hofs = ((w.dp16(0x61) >> 1) + o[0]) & 0x1FFF; vofs = ((w.dp16(0x63) >> 1) + o[1]) & 0x1FFF
        p.m7 = {"a": 0x100, "b": 0, "c": 0, "d": 0x100, "cx": hofs + 0x78, "cy": vofs + 0x78, "hofs": hofs, "vofs": vofs}
        return p

    def build(self, w):
        if w.u8(0xB53) == 0: return self.build_world(w)
        mapid = w.u16(0xAD4)
        props = w.read(0x110C, 26)
        vram, blocks, d = self.build_vram(w, props, mapid)
        cg = [u16(w.read(0xC00, 512), 2 * i) for i in range(256)]
        if self.synth_oam:
            from field_sprites import Mem, synthesize
            oam = synthesize(Mem.from_wram(w, self.rom.r))
        else: oam = w.read(0x200, 0x220)
        p = PPU(vram, cg, oam, self.regs(w, props, mapid), self.scroll(w), self.wave_hdma(w))
        p.bgmaps = self.tilemaps(w)
        return p

    def wave_hdma(self, w):
        """BG2/BG3 wave effect (maps with HDMA select 1/2 in the map properties): channels 3-6 ($5E bits 3-6) write BG2/BG3 H/V scroll for every line from
        16-word tables in WRAM ($7F6DE2 horizontal, $7F6E02 vertical) that repeat every 16 lines"""
        en = w.dp8(0x5E)
        if not en & 0x78: return {}
        hw = np.frombuffer(w.read(0x16DE2, 32), dtype="<u2"); vw = np.frombuffer(w.read(0x16E02, 32), dtype="<u2")
        out = {}
        for y in range(224):
            d = {}
            if en & 0x08: d[("h", 2)] = int(hw[y % 16]) & 0x3FF
            if en & 0x20: d[("h", 3)] = int(hw[y % 16]) & 0x3FF
            if en & 0x10: d[("v", 2)] = int(vw[y % 16]) & 0x3FF
            if en & 0x40: d[("v", 3)] = int(vw[y % 16]) & 0x3FF
            out[y] = d
        return out

    def render(self, w):
        return self.build(w).render(1)

if __name__ == "__main__":
    from snes9x_state import State
    st = State(sys.argv[1])
    img = Field(DEFAULT_ROM).render(Wram(StateWram(st)))
    from PIL import Image
    Image.fromarray(img).save(sys.argv[2] if len(sys.argv) > 2 else "/tmp/field.png")
