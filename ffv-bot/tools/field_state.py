#!/usr/bin/env python3
"""Sim mode: build the WRAM image the game would hold for a simulator State, so field_screen.Field renders it like the live game.

`build_mem(rom, state)` fills the same WRAM variables the live renderer reads (map properties, three tile layers with the event
modifications of the state, tile properties, palette, scroll, object table, party/overlay variables) from ROM data and the State of
sim/vm.py (flags, NPC flags, tile mods, NPC positions); `render_state` returns the 256x224 picture. Everything not derivable from the State
(window colour, animation frame, mid-step positions) uses the game's initial values, so the result matches a live screenshot of a standing
party. Local maps only here; the world map uses `world_mem`.
"""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "sim"))
from field_sprites import Mem, DP
from field_screen import Field, Rom as FRom, Wram, u16, DEFAULT_ROM
from ffv_decomp import decompress

class MemSrc:
    def __init__(self, m): self.m = m
    def read(self, off, n): return bytes(self.m.b[off:off + n])

def build_mem(srom, s, party_gfx=2, frame=0, hide_party=False):
    """srom: sim.rom.Rom; s: sim.vm.State on a local map; -> Mem"""
    r = srom.r
    m = Mem(r)
    b = m.b
    mp = s.map
    props = srom.props(mp)
    b[0xB53] = 1
    m.s16(0xAD4, mp); m.s16(0xAD6, srom.prop_map_index(mp))
    b[0xAD8], b[0xAD9] = s.x & 63, s.y & 63
    b[0xADA] = party_gfx; b[0xADB] = s.face & 3; b[0xADC] = 0
    b[0x110C:0x110C + 26] = props
    # tile layers and event modifications
    for bg in range(3):
        lay = bytearray(srom.layer(mp, bg))
        if bg == 0:
            for (x, y), t in s.mods.items(): lay[(y & 63) * 64 + (x & 63)] = t
        else:
            for (l, x, y), t in s.mods2.items():
                if l == bg: lay[(y & 63) * 64 + (x & 63)] = t
        b[0x10000 + 0x1000 * bg:0x10000 + 0x1000 * (bg + 1)] = lay
    # tile properties and tileset arrangement
    tp = srom.tileprops(mp)
    for i, (b1, b2) in enumerate(tp): b[0x1186 + 2 * i] = b1; b[0x1186 + 2 * i + 1] = b2
    ts = props[8]
    ar = decompress(r, 0xF0000 + u16(r, 0xF0000 + 2 * ts), True).ljust(0x800, b"\0")[:0x800]
    b[0x16E22:0x16E22 + 0x800] = ar
    # overlay tile table
    ov = decompress(r, 0xD980 + u16(r, 0xD980 + 2 * ts), True).ljust(256, b"\0")[:256]
    b[0x1873:0x1873 + 256] = ov
    # palette (map colours; sprite palettes; entry 1 is the menu window colour)
    idx = props[22]
    pal = bytearray(0x200)
    pal[0:256] = r[0x3BB00 + 256 * idx:0x3BB00 + 256 * idx + 256]
    pal[0:2] = b"\0\0"; pal[4:6] = b"\x8c\x31"; pal[6:8] = b"\xff\x7f"
    sp = r[0x1FFC00:0x1FFC00 + 128]
    pal[0x100:0x180] = sp; pal[0x180:0x200] = sp
    b[0xC00:0xE00] = pal
    # screen state: brightness, colour math / sub screen designations from the colour preset (props[4] & 0x3F)
    preset = FRom(r).color_preset(props[4] & 0x3F)
    b[DP + 0x45] = 0xF0; b[DP + 0x46] = 0
    b[DP + 0x47] = preset[0]; b[DP + 0x48] = preset[0]
    b[DP + 0x49] = preset[2]; b[DP + 0x4A] = preset[2]
    b[DP + 0x4C] = 0xFF
    b[DP + 0x53] = 1; b[DP + 0x3E] = frame & 0xFF
    # camera: positions are in half pixels, screen centre at party pixel (x * 16 + 8, y * 16 + 8) - (0x70, 0x70)
    sx = ((s.x * 16 - 0x70) * 2) & 0x7FF; sy = ((s.y * 16 - 0x70) * 2) & 0x7FF
    m.s16(DP + 0x61, sx); m.s16(DP + 0x63, sy)
    # BG2/BG3 parallax (_c063d4, _c06bdb): tile position scaled by a per-layer multiplier (1/2, 1, 2) minus an offset, same 32 units per tile.
    # Auto-scrolling layers ($1121) and the half-tile phase left over from how the party entered the map are not modelled.
    mult = (1, 0, 2, 1)
    def par(pos, sel, off): return ((((pos << mult[sel & 3]) & 0xFF) >> 1) - off) & 0x3F
    h = props[0x14]
    for reg, v in ((0x65, par(s.x, h, props[0x13])), (0x67, par(s.y, h >> 2, props[0x12])),
                   (0x69, par(s.x, h >> 4, props[0x11])), (0x6B, par(s.y, h >> 6, props[0x10]))):
        m.s16(DP + reg, (v - 7) << 5)
    # party sprite variables
    b[DP + 0xBD] = 0 if hide_party else 1; b[DP + 0xBC] = 1; b[DP + 0xC0] = 2
    b[DP + 0xC5] = 0x70; b[DP + 0xC6] = 0x70
    b[DP + 0xBB] = (s.face & 3) * 2
    b[DP + 0xC3] = s.z if s.z in (1, 2) else 1
    cross_buffers(m, s.x & 63, s.y & 63)
    p10fa = m.u16(0x10FA); b1 = p10fa & 0xFF
    b[DP + 0xCA] = b1 & 0x0C
    hide = 1 if (b1 & 0x10 and not (b1 & 4 and b[DP + 0xC3] & 1)) else 0
    b[DP + 0xCB] = hide
    m.s16(DP + 0xC7, 0x01E8 if (b1 & 0x40 or b1 & 4) else 0x0110)
    load_objects(srom, s, m)
    return m

def cross_buffers(m, x, y):
    """_c017e8: tile ids ($10BE-$10D6) and tile properties ($10F2-$110A) around the party from BG1"""
    lay = m.b[0x10000:0x11000]
    def tid(dx, dy): return lay[((y + dy) & 63) * 64 + ((x + dx) & 63)]
    cells = [(-1, -1), (0, -1), (1, -1), (-1, 0), (0, 0), (1, 0), (-1, 1), (0, 1), (1, 1), (0, -2)]
    for k, (dx, dy) in enumerate(cells):
        t = tid(dx, dy)
        m.b[0x10BE + 2 * k] = t
        m.b[0x10F2 + 2 * k] = m.b[0x1186 + 2 * t]; m.b[0x10F3 + 2 * k] = m.b[0x1187 + 2 * t]

def load_objects(srom, s, m):
    """object table ($1477 + 0x14 * i) for the NPCs of the map, as left by _c03eaa/_c03d28 for a standing party"""
    r = srom.r
    froms = FRom(r)
    raw = froms.npcs(s.map)
    m.b[DP + 0xE6] = len(raw)
    pos, prev, base = 0x6500, None, 0
    for i, n in enumerate(raw):
        o = 0x1477 + 0x14 * i
        g = n["gfx"]
        if prev is None or g != prev:
            prev = g
            if g != 0xFF:
                base = (pos - 0x6000) >> 4
                pos += len(froms.npc_gfx(g, n["flags1"])) // 2
        t1 = 0 if g == 0xFF else base
        pal = n["pal"]
        hi_top = (((pal & 8) | 0x10) << 1) | ((pal & 7) << 1)
        hi_bot = ((pal & 0x10) | 0x20) | ((pal & 7) << 1)
        x, y = s.npcpos.get(i, (n["x"], n["y"]))
        vis = s.npcvis.get(i)
        if vis is None: vis = s.npc_flag(n["script"])
        af = (pal & 0xE0) >> 5
        m.s16(o + 0, (x & 63) << 8); m.s16(o + 2, (y & 63) << 8)
        m.b[o + 4] = 0x80                              # $147B: no movement
        anim = n["flags"] & 0x70
        if anim and anim != 0x50: m.b[o + 16] = (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x22, 0x21, 0x0F)[af]   # $1487 frame offset
        else: m.b[o + 5] = s.npcface.get(i, af * 2) & 0x7F        # $147C graphic frame
        m.b[o + 7] = n["flags"]
        m.s16(o + 8, n["script"])
        m.s16(o + 10, t1 | (hi_top << 8)); m.s16(o + 12, t1 | (hi_bot << 8))
        hi = raw_hi(r, s.map, i)
        m.b[o + 14] = (hi | 0x08 | (1 if vis else 0)) & 0xFF

def raw_hi(r, mp, i):
    """high bits (x & 0xC0, (y & 0xC0) >> 2) of the NPC position bytes: object flags in $1485"""
    a = u16(r, 0xE59C0 + 2 * mp)
    o = 0xE59C0 + a + 7 * i
    return (r[o + 3] & 0xC0) | ((r[o + 4] & 0xC0) >> 2)

WORLD_PAL = 0xFFCC0       # CF/FCC0: Bartz, Galuf, underwater world palettes (256 bytes each)
WINDOW_PAL = 0xD340       # C0/D340 gradient palette used by the world map sprites ($0D80)
WORLD_TILESET = (0, 1, 0, 2, 2)

def world_mem(srom, s, world, party_gfx=2, frame=0):
    """WRAM image for a State on the world map (map index < 5); `world` is world_model.World (rows with flag-driven changes)"""
    from field_screen import _water_update
    r = srom.r
    m = Mem(r); b = m.b
    wm = s.map
    b[0xB53] = 0
    m.s16(0xAD4, wm); m.s16(0xAD6, wm)
    b[0xAD8], b[0xAD9] = s.x & 255, s.y & 255
    b[0xADA] = party_gfx; b[0xADB] = s.face & 3; b[0xADC] = s.veh
    for i, v in enumerate(s.vtab):
        b[0xADD + 4 * i:0xADD + 4 * i + 4] = bytes(v)
    rows = world.rows(wm, world.mod_key(wm, s.flags))
    for ty in range(64):
        wy = s.y + ((ty - s.y + 32) % 64 - 32)
        b[0x10000 + 256 * (wy & 63):0x10000 + 256 * (wy & 63) + 256] = rows[wy & 255]
    pal = bytearray(0x200)
    ts = WORLD_TILESET[wm]
    pal[0:256] = r[WORLD_PAL + 256 * ts:WORLD_PAL + 256 * ts + 256]
    sp = 0x1FFC00
    pal[0x100:0x180] = r[sp + 0x100:sp + 0x180]; pal[0x100:0x140] = r[sp:sp + 0x40]
    pal[0x180:0x1A0] = r[WINDOW_PAL + (0x20 if wm >= 3 else 0):WINDOW_PAL + (0x20 if wm >= 3 else 0) + 0x20]
    pal[0x1A0:0x200] = r[sp + 0x20:sp + 0x80]
    b[0xC00:0xE00] = pal
    b[DP + 0x45] = 0xF0; b[DP + 0x47] = 0x41 if wm < 3 else 0xC1; b[DP + 0x49] = 0x10; b[DP + 0x4C] = 0xFF
    b[DP + 0x3E] = frame & 0xFF
    sx = ((s.x * 16 - 0x70) * 2) & 0x7FF; sy = ((s.y * 16 - 0x70) * 2) & 0x7FF
    m.s16(DP + 0x61, sx); m.s16(DP + 0x63, sy)
    b[DP + 0xBD] = 1; b[DP + 0xC5] = b[DP + 0xC6] = 0x70
    # water animation buffers hold the animated characters one step ahead of VRAM: start from the ROM characters (frame 0 of the cycle)
    from field_screen import Rom as FR
    ch = FR(r).world_chars(wm)
    win = bytearray(0x180)
    win[0:0x80] = ch[0x62:0x64].reshape(-1)[:0x80].tobytes(); win[0x80:0x100] = ch[0x72:0x74].reshape(-1)[:0x80].tobytes()
    win[0x100:0x180] = ch[0x87:0x89].reshape(-1)[:0x80].tobytes()
    _water_update(win, (frame - 1) & 255)
    b[0x18622:0x18622 + 0x180] = win
    return m

_WORLD = None

def render_state(srom, s, world=None, **kw):
    """256x224 RGB array of the screen the game shows for State s (local map or world map)"""
    global _WORLD
    if s.map < 5:
        if world is None:
            if _WORLD is None:
                from world_model import World
                _WORLD = World(srom.r)
            world = _WORLD
        m = world_mem(srom, s, world, **kw)
    else: m = build_mem(srom, s, **kw)
    from field_sprites import synthesize
    F = Field(FRom(srom.r)); F.synth_oam = True
    return F.render(Wram(MemSrc(m)))

def render_map(srom, s):
    """whole local map as a 1024x1024x3 image: the game screen rendered 5x5 times with the party hidden (the visible part is 240x224 pixels,
    columns 8-247) and the camera moved by changing only the position; NPCs, event modifications and all three layers are drawn exactly
    as in the game. Pixel (px, py) of the result is map pixel (px, py)."""
    from vm import State
    out = np.zeros((1024, 1024, 3), dtype=np.uint8)
    t = State.__new__(State)
    for k in State.__slots__: setattr(t, k, getattr(s, k, None))
    F = Field(FRom(srom.r)); F.synth_oam = True
    for j in range(5):
        for i in range(5):
            t.x = (7 + 15 * i) & 63; t.y = (7 + 14 * j) & 63
            img = F.render(Wram(MemSrc(build_mem(srom, t, hide_party=True))))
            ys = (224 * j + np.arange(224)) % 1024; xs = (8 + 240 * i + np.arange(240)) % 1024
            out[ys[:, None], xs[None, :]] = img[:, 8:248]
    return out

if __name__ == "__main__":
    from sim import Sim
    from PIL import Image
    S = Sim()
    st = S.initial()
    Image.fromarray(render_state(S.R, st)).save(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sim_screen.png")
