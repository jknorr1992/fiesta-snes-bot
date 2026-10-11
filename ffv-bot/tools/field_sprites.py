#!/usr/bin/env python3
"""Field sprite (OAM) synthesis: ports of DrawPlayerSprite, DrawObjSprites, UpdateOverlay and DrawOverlaySprites (field-main.asm).

`Mem` is a 128 KB WRAM image ($7E0000 = offset 0, $7F0000 = 0x10000).  `Mem.from_wram(w)` copies the ranges the routines use from any
`field_screen.Wram`; the draw routines then rebuild the OAM shadow ($0200-$041F) exactly as the game does, so live OAM and synthesized
OAM can be compared byte for byte, and `field_state.py` can fill a Mem from the simulator state instead of the emulator.
Direct-page variables are at DP + n (DP = $0B00); all arithmetic is 8-bit/16-bit wrapped like the 65816."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DP = 0xB00
OAM = 0x200

# ROM tables (file offsets = C0 bank offsets)
JUMP_Y = 0x22DB      # _c022db: party jump height by counter / object z height
FRAMES = 0x2A95      # _c02a95: 16-byte sprite frame records: (x,y,tile,attr) x 4 (upper-left, upper-right, lower-left, lower-right)
OVL_TILE = 0x2A55    # _c02a55
OVL_POS = 0x2A75     # _c02a75
OBJ_ANIM_SHIFT = (3, 4, 5, 2)
OBJ_ANIM_FRAME = (0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1, 2, 3, 0, 4, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
VEH_OFS = 0x2063     # _c02063 word table: vehicle graphic tile offsets
VEH_ANIM = 0x209B    # _c0209b animation masks
VEH_FRAMES = 0x20B7  # _c020b7 vehicle sprite records (4 bytes x 4)
SHADOW = (0x3044, 0x3044, 0x3045, 0x3045, 0x3045, 0x3046, 0x3046, 0x3046)
OVL_DX = ((0, 0x80, 0, -0x80), (0, 0x100, 0, -0x100), (0, 0x180, 0, -0x180), (0, 0x200, 0, -0x200),
          (0, 0x80, 0, -0x80), (0, 0x100, 0, -0x100), (0, 0x180, 0, -0x180), (0, 0x400, 0, -0x400))
OVL_DY = ((-0x80, 0, 0x80, 0), (-0x100, 0, 0x100, 0), (-0x180, 0, 0x180, 0), (-0x200, 0, 0x200, 0),
          (-0x80, 0, 0x80, 0), (-0x100, 0, 0x100, 0), (-0x180, 0, 0x180, 0), (-0x400, 0, 0x400, 0))

# WRAM ranges the sprite routines read
RANGES = ((0x0AD0, 0x20), (0x0B00, 0x100), (0x108C, 0x20), (0x10A0, 0x10), (0x10BE, 0x4E), (0x1104, 0x10), (0x1114, 2), (0x1186, 0x400),
          (0x13C6, 0x10), (0x1477, 0x14 * 16), (0x169F, 1), (0x1873, 0x100))

class Mem:
    def __init__(self, rom):
        self.b = bytearray(0x20000)
        self.rom = rom if isinstance(rom, (bytes, bytearray)) else rom.r

    @classmethod
    def from_wram(cls, w, rom, ranges=RANGES):
        m = cls(rom)
        for a, n in ranges:
            m.b[a:a + n] = w.read(a, n)
        return m

    def u8(self, o): return self.b[o]
    def u16(self, o): return self.b[o] | self.b[o + 1] << 8
    def s8(self, o, v): self.b[o] = v & 0xFF
    def s16(self, o, v): self.b[o] = v & 0xFF; self.b[o + 1] = v >> 8 & 0xFF
    def dp8(self, n): return self.b[DP + n]
    def dp16(self, n): return self.u16(DP + n)
    def r8(self, o): return self.rom[o]
    def r16(self, o): return self.rom[o] | self.rom[o + 1] << 8

    def reset_sprites(self):
        """ResetSprites: all 128 sprites off-screen (y = $F0), high table cleared"""
        for i in range(128): self.b[OAM + 4 * i + 1] = 0xF0
        for i in range(0x20): self.b[0x400 + i] = 0

    def oam(self): return bytes(self.b[OAM:OAM + 0x220])

# ---- party ----
def draw_player(m):
    if not m.dp8(0xBD) or m.dp8(0xCB): return
    be = m.dp8(0xBE)
    if be & 0x80: be = (be & 0x7F) >> 1
    d = m.r8(JUMP_Y + be)
    gfx = m.u8(0xADA)
    if m.u8(0x10FB) & 0x80:                               # auto-move tile
        x = 0x240; m.b[DP + 0x15] = m.dp8(0xC6)
    else:
        step = None
        if m.dp8(0xBC) and not (m.dp8(0x57) and m.dp8(0xBA) == 0):
            if gfx == 0x0A:
                m.b[DP + 0x15] = m.dp8(0xC6)
                a = ((m.dp8(0x3F) & 2) >> 1) + 0x0A
                step = "fixed"
            else:
                a = (((m.dp8(0x61) + m.dp8(0x63)) & 0xFF) >> 4) & 1
                a = (m.u8(0xADB) * 2 + a) & 0xFF
                m.b[DP + 0xBB] = a
        else:
            a = m.dp8(0xBB)
        if step is None:
            if 7 <= gfx < 0x0D:
                a = (a + 0x40) & 0xFF
                if gfx >= 9: a = (a + 8) & 0xFF
            m.b[DP + 0x15] = (((m.dp8(0x61) >> 4) + 1) & 1) + m.dp8(0xC6) & 0xFF
        x = (a & 0xFF) << 4
    # attribute bytes for the upper (08) and lower (09) halves
    p = (m.u8(0x110F) & 0x10) >> 1
    if gfx >= 0x0B: h08 = h09 = 0x30
    elif d or m.dp8(0xC6) != 0x70: h08 = h09 = 0x30
    else:
        ca = m.dp8(0xCA)
        if ca & 8:
            if ca & 4: h08 = h09 = p | 0x20
            else: h08 = p | 0x20; h09 = 0x28
        elif not ca & 4: h08 = p | 0x30; h09 = 0x28
        elif not m.dp8(0xC3) & 2: h08 = h09 = 0x30
        else: h08 = p | 0x20; h09 = 0x28
    c5, y15 = m.dp8(0xC5), m.dp8(0x15)
    sx, sy = m.u8(0x10A0), m.u8(0x10A2)
    r = m.rom
    t = FRAMES + x
    y = m.dp16(0xC7)
    # upper half: records 0,1 -> OAM $0200+y, +4; lower half: records 2,3 -> $0208+$1E8, +4
    for i in range(4):
        o = (0x200 + y + 4 * i) if i < 2 else (0x3F0 + 4 * (i - 2))
        rec = t + 4 * i
        m.b[o] = (r[rec] + c5 - sx) & 0xFF
        m.b[o + 1] = (r[rec + 1] + y15 - sy - d) & 0xFF
        m.b[o + 2] = r[rec + 2]
        m.b[o + 3] = r[rec + 3] | (h08 if i < 2 else h09)

# ---- objects (NPCs) ----
def draw_objs(m, hiryuu_ok=True):
    n = m.dp8(0xE6)
    if not n: return
    sx, sy = m.u8(0x10A0), m.u8(0x10A2)
    r = m.rom
    e9 = 0; v23 = 0
    for _ in range(n):
        y = e9
        try:
            if (m.u8(0x169F) and y < 0x28) or not m.u8(0x1485 + y) & 1: raise StopIteration
            an = m.u8(0x147E + y); t = an & 0x70
            if t not in (0, 0x50):
                s = t >> 2
                sh = OBJ_ANIM_SHIFT[an & 3]
                a = (m.dp8(0x3E) >> sh) & 3 | s
                f = (OBJ_ANIM_FRAME[a] + m.u8(0x1487 + y)) & 0xFF
            else:
                v = m.u8(0x147C + y) & 0x7F
                if 0x10 <= v < 0x40: f = v
                else:
                    if not m.u8(0x1488 + y) and m.u8(0x1485 + y) & 8:
                        s = 0
                        if an & 8 or not m.u8(0x147D + y) & 0x80:
                            s = 2 if m.u8(0x147D + y) & 0x20 else 0
                        f = (m.u8(0x147C + y) & 0x7F) | s
                    elif not m.u8(0x147C + y) & 0x80: f = m.u8(0x147C + y) & 0x7F
                    else: f = ((m.u8(0x147D + y) & 0x20) >> 5) + m.u8(0x147C + y) & 0x7F
            if f < 8 and an & 0x70 == 0x50: f += 0x40
            X = (f & 0xFF) << 4
            px = ((m.u16(0x1477 + y) >> 3) + 0x10 - m.dp16(0x61)) & 0x7FF
            if px >= 0x200: raise StopIteration
            d0 = px >> 1
            tt = (m.u8(0x1488 + y) >> 2) if m.u16(0x1485 + y) & 4 else (m.u8(0x1488 + y) >> 1)
            h = r[JUMP_Y + tt] << 1
            py = ((m.u16(0x1479 + y) >> 3) + 0x10 - m.dp16(0x63) - h) & 0x7FF
            if py < 0x7F0 and py >= 0x1E0: raise StopIteration
            f0 = (py >> 1) + ((m.u16(0x1477 + y) >> 6) & 1)
            ax = (d0 - sx) & 0xFF
            m.b[0x244 + v23] = m.b[0x31C + v23] = ax
            m.b[0x240 + v23] = m.b[0x318 + v23] = (ax - 8) & 0xFF
            ay = (f0 - sy - 3) & 0xFF
            m.b[0x319 + v23] = m.b[0x31D + v23] = ay
            m.b[0x241 + v23] = m.b[0x245 + v23] = (ay - 8) & 0xFF
            T = FRAMES + X
            t1, t2 = m.u16(0x1481 + e9), m.u16(0x1483 + e9)
            m.s16(0x242 + v23, m.r16(T + 2) + t1)
            m.s16(0x246 + v23, m.r16(T + 6) + t1)
            m.s16(0x31A + v23, m.r16(T + 10) + t2)
            m.s16(0x31E + v23, m.r16(T + 14) + t2)
        except StopIteration:
            pass
        v23 += 8; e9 += 0x14

# ---- overlay (tops of trees etc. drawn over the party) ----
def update_overlay(m):
    for k in (0x108C, 0x108D, 0x108E, 0x108F): m.b[k] = 0
    ba = m.dp8(0xBA)
    x = ((ba - 1) << 3) if ba else 0
    r = m.rom
    for yi in range(0, 16, 4):
        m.b[0x13C6 + yi] = r[OVL_POS + x]
        m.b[0x13C7 + yi] = r[OVL_POS + x + 1]
        tile, hi = _overlay_tile(m, r[OVL_TILE + x])
        v = ((tile + 0x1C0) | 0x0400 | hi << 8) & 0xFFFF
        if v & 0x10: v |= 0x4000
        m.s16(0x13C8 + yi, v & 0xFFEF)
        x += 2

def _overlay_tile(m, y):
    """overlay tile and priority flag (0x80 -> OAM attribute bit 7) for neighbour index y (the branches of UpdateOverlay)"""
    c3 = m.dp8(0xC3)
    p10fa = m.u8(0x10FA); p10f2y = m.u8(0x10F2 + y)
    look = lambda: m.u8(0x1873 + m.u8(0x10BE + y))
    if y == 0x12:
        if p10fa & 4 and c3 == 2:
            return (2, 0x80) if m.u8(0x1104) & 3 == 1 else (0, 0)
    elif y < 6 and m.u8(0x10F8 + y) & 4:
        if c3 != 2: return (0, 0)
        if p10f2y & 3 != 1: return (look(), 0x80)
        return (2, 0x80)
    q = p10fa & 7
    if (q == 5 and c3 != 2) or q == 1:
        if p10f2y & 3 == 2: return (0, 0)
    if p10f2y & 4:
        return (0, 0) if c3 == 1 else (look(), 0x80)
    return (look(), 0)

def draw_overlay(m):
    if not m.dp8(0xBD): return
    y = 0x100
    a = (m.dp8(0xC0) - 1) << 2
    ba = m.dp8(0xBA)
    if ba:
        i = ((ba - 1) + a) & 0xFF
        m.s16(0x108C, m.u16(0x108C) + OVL_DX[i >> 2][i & 3])
        m.s16(0x108E, m.u16(0x108E) + OVL_DY[i >> 2][i & 3])
    sx, sy = m.u8(0x10A0), m.u8(0x10A2)
    for k in range(4):
        x = 4 * k
        fl = m.u8(0x13C9 + x)
        o = (0x200 + y) if not fl & 0x80 else (0x2D8 + y)
        m.b[o] = (m.u8(0x13C6 + x) - m.u8(0x108D) - sx) & 0xFF
        m.b[o + 1] = (m.u8(0x13C7 + x) - m.u8(0x108F) - sy) & 0xFF
        m.b[o + 2] = m.u8(0x13C8 + x)
        m.b[o + 3] = fl & 0x7F
        y += 4
    m.b[0x410] = 0xAA; m.b[0x41D] = 0xA0; m.b[0x41E] = 0x0A

# ---- world map: party and vehicles ----
def draw_world_party(m):
    """_c02137: foot party / chocobo / ship etc. on the world map (OAM $0288-$029F)"""
    if not m.dp8(0xBD): return
    be = m.dp8(0xBE)
    if be & 0x80: be = (be & 0x7F) >> 1
    r = m.rom
    d = r[JUMP_Y + be]
    vh = m.u8(0xADC)
    sx, sy = m.u8(0x10A0), m.u8(0x10A2)
    a = (((m.dp8(0x61) + m.dp8(0x63)) & 0xFF) >> 4) & 1
    a = (m.u8(0xADB) * 2 + a) & 0xFF
    gfx = m.u8(0xADA)
    if gfx in (7, 8): a = (a + 0x40) & 0xFF
    X = a << 4
    if vh < 2:
        t11 = 0
        if vh == 0: y15 = (((m.dp8(0x61) >> 4) + 1) & 1) | 0x70
        else: t11 = 0x10; X += 0x400; y15 = 0x70
        ca = m.dp8(0xCA)
        if d or not ca & 0x60: h08 = h09 = 0x30
        elif ca & 0x20: h08 = 0x30; h09 = 0x00
        else: h08 = 0x30; h09 = 0x38
        for k in range(4):
            rec = FRAMES + X + 4 * k
            o = 0x288 + 4 * k
            m.b[o] = (r[rec] + 0x70 - sx) & 0xFF
            m.b[o + 1] = (r[rec + 1] + y15 - sy - d) & 0xFF
            m.b[o + 2] = (r[rec + 2] + t11) & 0xFF
            m.b[o + 3] = r[rec + 3] | (h08 if k < 2 else h09)
        return
    y = ((vh - 1) << 2) & 0xFF
    y15 = (0x78 - (m.u8(0xADE + y) & 0x7F)) & 0xFF
    g = m.u8(0xADD + y) & 3
    t11 = m.r16(VEH_OFS + ((g + (vh << 2)) << 1))
    anim = r[VEH_ANIM + (g | (vh << 2))]
    X = ((m.u8(0xADB) << 5) + (0x10 if m.dp8(0x3E) & anim else 0)) & 0xFF
    for k in range(4):
        rec = VEH_FRAMES + X + 4 * k
        o = 0x288 + 4 * k
        m.b[o] = (r[rec] + 0x78 - sx) & 0xFF
        m.b[o + 1] = (r[rec + 1] + y15 - sy - d) & 0xFF
        m.s16(o + 2, m.r16(rec + 2) + t11)

def draw_world_shadow(m):
    """_c01e64: airship-type vehicle shadow"""
    vh = m.u8(0xADC)
    if not m.dp8(0xBD) or not vh: return
    y = ((vh - 1) << 2) & 0xFF
    if ((m.u8(0xADD + y) & 0x1C) >> 2) not in (2, 3, 4, 6): return
    v = SHADOW[((m.u8(0xADE + y) & 0x7C) >> 2) % 8]
    m.s16(0x2F8, 0x7570); m.s16(0x2FA, v); m.s16(0x2FC, 0x7578); m.s16(0x2FE, v ^ 0x4000)

def draw_world_vehicles(m):
    """_c01ec5: parked vehicles visible on the current world map (OAM $0298-$02F7)"""
    if m.dp8(0x6F): return
    r = m.rom
    sx, sy = m.u8(0x10A0), m.u8(0x10A2)
    for v23 in range(0, 0x18, 4):
        y = v23
        if m.u8(0xADE + y) & 0x80 or (m.u8(0xADD + y) >> 5) != m.u8(0xAD6): continue
        a = (m.u8(0xADF + y) + r[0x205F] - m.u8(0xAD8)) & 0xFF
        if a >= r[0x2060]: continue
        x13 = ((a << 4) + 8 - ((m.dp8(0x61) & 0x1F) >> 1)) & 0xFFFF
        if x13 & 0x100: continue
        b = (m.u8(0xAE0 + y) + r[0x2061] - m.u8(0xAD9)) & 0xFF
        if b >= r[0x2062]: continue
        y15 = ((b << 4) + 8 - ((m.dp8(0x63) & 0x1F) >> 1)) & 0xFFFF
        t11 = m.r16(VEH_OFS + (((m.u8(0xADD + y) & 3) + v23 + 4) << 1))
        base = v23 << 2
        kind = m.u8(0xADD + y) & 0x1C
        X = 0x60 if kind < 8 else 0x60
        for k in range(4):
            if kind < 8:
                rec = FRAMES + X + 4 * k
                ox = (x13 + r[rec] - 8 - sx) & 0xFF
                oy = (y15 + r[rec + 1] - 7 - sy) & 0xFF
                tw = ((m.r16(rec + 2) + t11) | 0x3000) & 0xFFFF
            else:
                rec = VEH_FRAMES + X + 4 * k
                ox = (x13 + r[rec] - sx) & 0xFF
                oy = (y15 + r[rec + 1] - sy) & 0xFF
                tw = (m.r16(rec + 2) + t11) & 0xFFFF
            o = 0x298 + base + 4 * k
            m.b[o] = ox; m.b[o + 1] = oy; m.s16(o + 2, tw)
        if kind >= 8 and kind == 8:
            m.b[0x298 + base + 15] &= 0x0F; m.b[0x298 + base + 11] &= 0x0F

def synthesize(m):
    """one frame of sprite drawing as in the field main loops: world (map 0 mode 7) or local map"""
    m.reset_sprites()
    if m.u8(0xB53) == 0:
        draw_world_party(m)
        draw_world_vehicles(m)
        draw_world_shadow(m)
        return m.oam()
    draw_player(m)
    draw_objs(m)
    update_overlay(m)
    draw_overlay(m)
    return m.oam()
