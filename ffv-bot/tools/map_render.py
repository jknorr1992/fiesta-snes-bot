#!/usr/bin/env python3
"""Render FFV local maps from ROM data (tileset graphics, tile arrangements, layouts, palettes) with event markers.

ROM tables (file offsets, headerless; CE9C00 etc. are SNES addresses, file offset = address - 0xC00000):
  map properties   0xE9C00, 26 bytes per map. p[5] tile-property set, p[8] tileset (arrangement), p[9]&0x3F / (w>>6)&0x3F / (w>>4)&0x3F
                   the three graphics blocks (w = p[9..10], p[10..11]), p[12..14] layout indices of BG1/BG2/BG3, p[22] palette index
  layouts          u16 pointers at 0xB0000 (bank increments whenever a pointer is smaller than the previous one), data from 0xB0290;
                   headerless LZSS -> 64x64 tile bytes (row stride 64)
  tileset          28 u16 pointers at 0xF0000 (relative), headerless LZSS -> 0x800 bytes = four u16[256] planes: top-left, bottom-left,
                   top-right, bottom-right 8x8 tiles of each 16x16 tile (SNES BG entry: tile 0-9, palette 10-12, priority 13, hflip 14, vflip 15)
  graphics         4-byte pointers at 0x1C2D84 (relative to 0x1C2E24), 4bpp planar, blocks land at character 0, 256 and 512
  palettes         0x3BB00 + 256 * p[22]: 8 palettes of 16 BGR555 colours
Event markers: orange = event trigger (event ids of its script), cyan = entrance (destination map), green = NPC (script id).
Usage: map_render.py <rom.sfc> <map> [out.png] [--scale N] [--event E ...]   (--event highlights tiles whose script runs event E)"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ffv_decomp import decompress

def u16(r, o): return r[o] | r[o + 1] << 8

class Maps:
    def __init__(self, rom):
        self.r = rom
        ptrs = [u16(rom, 0xB0000 + 2 * i) for i in range(324)]
        bank, prev, self.layout_off = 0xCB, None, []
        for p in ptrs:
            if prev is not None and p < prev: bank += 1
            self.layout_off.append((bank - 0xC0) * 0x10000 + p); prev = p
        self.gfx_ptr = [struct.unpack_from("<I", rom, 0x1C2D84 + 4 * i)[0] for i in range(41)]

    def props(self, m): return self.r[0xE9C00 + 26 * m:0xE9C00 + 26 * m + 26]

    def layout(self, idx):
        return decompress(self.r, self.layout_off[idx], True) if 0 <= idx < 324 else bytes(4096)

    def tileset(self, i):
        base = 0xF0000
        return decompress(self.r, base + u16(self.r, base + 2 * i), True)

    def gfx(self, i):
        """One 0x2000-byte graphics block (256 4bpp characters). The pointer table's length differences are NOT block lengths
        (blocks overlap the next entry's start); whole 0x2000 blocks match save-state VRAM dumps byte for byte."""
        a = 0x1C2E24 + self.gfx_ptr[i]
        return self.r[a:a + 0x2000].ljust(0x2000, b"\0")

    def palette(self, i):
        o = 0x3BB00 + 256 * i
        cols = []
        for k in range(128):
            v = u16(self.r, o + 2 * k)
            cols.append(((v & 31) * 255 // 31, (v >> 5 & 31) * 255 // 31, (v >> 10 & 31) * 255 // 31))
        return cols

    @staticmethod
    def tile4(data, c):
        """8x8 pixels of 4bpp planar character c -> list of 64 colour indices"""
        b = data[c * 32:c * 32 + 32]
        out = []
        for y in range(8):
            p0, p1, p2, p3 = b[2 * y], b[2 * y + 1], b[16 + 2 * y], b[17 + 2 * y]
            for x in range(8):
                s = 7 - x
                out.append((p0 >> s & 1) | (p1 >> s & 1) << 1 | (p2 >> s & 1) << 2 | (p3 >> s & 1) << 3)
        return out

    def render(self, m, layers=(1, 0)):
        """RGBA PIL image of the map (64x64 16x16 tiles = 1024x1024)"""
        from PIL import Image
        p = self.props(m)
        tileset = self.tileset(p[8])
        w1, w2 = p[9] | p[10] << 8, p[10] | p[11] << 8
        gi = [p[9] & 0x3F, (w1 >> 6) & 0x3F, (w2 >> 4) & 0x3F]
        chars = b"".join(self.gfx(g) for g in gi)
        pal = self.palette(p[22])
        lay = [self.layout(((p[12] | p[13] << 8) & 0x3FF) - 1), self.layout((((p[13] | p[14] << 8) & 0xFFC) >> 2) - 1),
               self.layout((((p[14] | p[15] << 8) & 0x3FF0) >> 4) - 1)]
        cache = {}
        def tile(entry):
            if entry not in cache:
                c = entry & 0x3FF
                px = self.tile4(chars, c) if c < 768 else [0] * 64
                if entry & 0x4000: px = [px[y * 8 + 7 - x] for y in range(8) for x in range(8)]
                if entry & 0x8000: px = [px[(7 - y) * 8 + x] for y in range(8) for x in range(8)]
                base = (entry >> 10 & 7) * 16
                cache[entry] = [(0, 0, 0, 0) if v == 0 else pal[base + v] + (255,) for v in px]
            return cache[entry]
        img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 255))
        for li in layers:
            data = lay[li]
            if len(data) < 4096: continue
            layer = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
            px = layer.load()
            for ty in range(64):
                for tx in range(64):
                    t = data[ty * 64 + tx]
                    for q, (ox, oy) in enumerate(((0, 0), (0, 8), (8, 0), (8, 8))):  # planes: TL, BL, TR, BR
                        e = tileset[q * 512 + 2 * t] | tileset[q * 512 + 2 * t + 1] << 8
                        pix = tile(e)
                        for y in range(8):
                            for x in range(8):
                                c = pix[y * 8 + x]
                                if c[3]: px[tx * 16 + ox + x, ty * 16 + oy + y] = c
            img.alpha_composite(layer)
        return img

def markers(rom_path, maps, m, highlight=()):
    """Event trigger / entrance / NPC marker list: (kind, x, y, label, highlighted)"""
    from event_vm import Rom
    rom = Rom(rom_path)
    out = []
    for x, y, t in rom.triggers(m):
        evs = [ev for _, ev in rom.trigger_script(t)]
        out.append(("trigger", x, y, ",".join(map(str, evs)), any(e in highlight for e in evs)))
    for e in rom.entrances(m):
        out.append(("entrance", e["x"], e["y"], "->%d" % e["map"], False))
    for n in rom.npcs(m):
        evs = [ev for _, ev in rom.npc_script(n["script"])]
        out.append(("npc", n["x"], n["y"], "n%d:%s" % (n["script"], ",".join(map(str, evs))), any(e in highlight for e in evs)))
    return out

def draw(img, marks, scale=1):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img, "RGBA")
    colors = {"trigger": (255, 140, 0), "entrance": (0, 200, 255), "npc": (60, 220, 60)}
    for kind, x, y, label, hl in marks:
        c = colors[kind]
        x0, y0 = x * 16, y * 16
        d.rectangle([x0, y0, x0 + 15, y0 + 15], outline=(255, 0, 255) if hl else c, width=3 if hl else 1, fill=c + (70,))
        d.text((x0 + 1, y0 + 1), label, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
    return img

if __name__ == "__main__":
    args = sys.argv[1:]
    rom_path, m = args[0], int(args[1])
    out = args[2] if len(args) > 2 and not args[2].startswith("--") else "/tmp/map_%d.png" % m
    hl = [int(args[i + 1]) for i, a in enumerate(args) if a == "--event"]
    rom = open(rom_path, "rb").read()
    maps = Maps(rom)
    img = draw(maps.render(m), markers(rom_path, maps, m, hl))
    if "--crop" in args:  # --crop x0 y0 x1 y1 in tiles
        i = args.index("--crop"); x0, y0, x1, y1 = map(int, args[i + 1:i + 5])
        img = img.crop((x0 * 16, y0 * 16, x1 * 16, y1 * 16))
    img.convert("RGB").save(out)
    print("wrote", out, img.size)
