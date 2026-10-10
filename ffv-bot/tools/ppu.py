#!/usr/bin/env python3
"""Software SNES PPU for FFV's field screens: backgrounds (mode 1 / mode 0), sprites, windows and colour math, per scanline.

Input is the video memory the game would have built (`vram` 64 KiB, `cgram` 256 BGR555 words, `oam` 544 bytes) plus the register values
(`regs`, a dict keyed by register address 0x2100-0x2133 holding the last written byte, scroll registers separate), and optionally
per-scanline register overrides (`hdma`, {line: {reg: value}}), exactly what HDMA would do.  Output: 256x224 RGB numpy array.
It implements what the field/world/battle-free FFV screens use: 4bpp/2bpp 8x8 backgrounds, OBJ 8/16/32/64 sizes, BG3 priority,
windows 1/2 with the four logic modes, main/sub screen, add/subtract/half colour math.  Mosaic, mode 7 and hires are not implemented
(the world map uses mode 7: see world_screen.py when present)."""
import numpy as np

W, H = 256, 224
OBJ_SIZES = {0: ((8, 8), (16, 16)), 1: ((8, 8), (32, 32)), 2: ((8, 8), (64, 64)), 3: ((16, 16), (32, 32)),
             4: ((16, 16), (64, 64)), 5: ((32, 32), (64, 64)), 6: ((16, 32), (32, 64)), 7: ((16, 32), (32, 32))}

def decode_chars(vram, bpp):
    """all characters of VRAM decoded to (n, 8, 8) colour index arrays: 4bpp chars are 32 bytes, 2bpp 16 bytes"""
    v = np.frombuffer(bytes(vram), dtype=np.uint8)
    size = 16 * bpp // 2
    n = len(v) // size
    c = v[:n * size].reshape(n, size)
    out = np.zeros((n, 8, 8), dtype=np.uint8)
    sh = 7 - np.arange(8)
    for pair in range(bpp // 2):
        lo = c[:, pair * 16:pair * 16 + 16:2]; hi = c[:, pair * 16 + 1:pair * 16 + 16:2]      # (n, 8) rows
        out |= (((lo[:, :, None] >> sh) & 1) << (pair * 2)).astype(np.uint8)
        out |= (((hi[:, :, None] >> sh) & 1) << (pair * 2 + 1)).astype(np.uint8)
    return out

def bgr555_to_rgb(c):
    c = np.asarray(c, dtype=np.int32)
    r, g, b = c & 31, (c >> 5) & 31, (c >> 10) & 31
    return r, g, b

def to8(rgb):
    """(W, 3) 5-bit channel values -> 8-bit as RetroArch's snes9x output (RGB565 expanded): green is widened to 6 bits by replicating its top bit"""
    r = np.floor(rgb[:, 0]).astype(int); g = np.floor(rgb[:, 1]).astype(int); b = np.floor(rgb[:, 2]).astype(int)
    g6 = (g << 1) | (g >> 4)
    return np.stack([np.floor(r * 255 / 31 + 0.5), np.floor(g6 * 255 / 63 + 0.5), np.floor(b * 255 / 31 + 0.5)], axis=1).astype(np.uint8)

class Layer:
    """per-pixel result of one layer for one scanline set: idx (0 = transparent), color (r,g,b 0-31), prio"""
    __slots__ = ("color", "opaque", "prio", "pal", "obj")

class PPU:
    def __init__(self, vram, cgram, oam, regs, scroll=None, hdma=None):
        self.vram = bytes(vram).ljust(65536, b"\0")
        self.cg = np.asarray(cgram, dtype=np.int32)
        self.oam = bytes(oam).ljust(544, b"\0")
        self.regs = dict(regs)
        self.scroll = scroll or {}          # {1: (h, v), 2: (h, v), 3: (h, v)}
        self.hdma = hdma or {}
        self._chars = {}
        self.m7 = {"a": 0x100, "b": 0, "c": 0, "d": 0x100, "cx": 0, "cy": 0, "hofs": 0, "vofs": 0}

    def chars(self, bpp):
        if bpp not in self._chars: self._chars[bpp] = decode_chars(self.vram, bpp)
        return self._chars[bpp]

    def reg(self, a, line=None):
        if line is not None and line in self.hdma and a in self.hdma[line]: return self.hdma[line][a]
        return self.regs.get(a, 0)

    # ---- backgrounds ----
    def bg_plane(self, n, mode):
        """full wrapped BG plane: (palette-local colour index array, palette number array, priority array, size) for BG n (1-3)"""
        sc = self.regs.get(0x2106 + n, 0)
        base = (sc & 0xFC) << 8                           # word address
        size = sc & 3                                     # 0: 32x32, 1: 64x32, 2: 32x64, 3: 64x64
        wt, ht = (64 if size & 1 else 32), (64 if size & 2 else 32)
        bpp = 4 if (mode == 1 and n < 3) else 2
        nba = self.regs.get(0x210B if n <= 2 else 0x210C, 0)
        cbase = ((nba >> (4 * ((n - 1) & 1))) & 0xF) << 12
        chars = self.chars(bpp)
        csz = 16 if bpp == 4 else 8                       # words per char
        mp = np.frombuffer(self.vram, dtype="<u2")
        # screens: 32x32 blocks of 0x400 words; layout 64x32 = [A B], 32x64 = [A / B], 64x64 = [A B / C D]
        tm = np.zeros((ht, wt), dtype=np.uint16)
        for sy in range(ht // 32):
            for sx in range(wt // 32):
                if size == 1: off = sx
                elif size == 2: off = sy
                else: off = sy * 2 + sx
                tm[sy * 32:(sy + 1) * 32, sx * 32:(sx + 1) * 32] = mp[(base + off * 0x400) & 0xFFFF:][:0x400].reshape(32, 32) \
                    if ((base + off * 0x400) & 0xFFFF) + 0x400 <= 0x10000 else 0
        tile = tm & 0x3FF
        ci = ((cbase // csz) + tile) % (len(chars))
        px = chars[ci]                                    # (ht, wt, 8, 8)
        hf = (tm >> 14) & 1; vf = (tm >> 15) & 1
        px = np.where(hf[:, :, None, None] == 1, px[:, :, :, ::-1], px)
        px = np.where(vf[:, :, None, None] == 1, px[:, :, ::-1, :], px)
        plane = px.transpose(0, 2, 1, 3).reshape(ht * 8, wt * 8)
        pal = np.repeat(np.repeat((tm >> 10) & 7, 8, axis=0), 8, axis=1)
        pri = np.repeat(np.repeat((tm >> 13) & 1, 8, axis=0), 8, axis=1)
        return plane, pal, pri, bpp

    # ---- mode 7 ----
    def mode7_line(self, y, vofs_bias=1):
        """-> (colour index (W,), always opaque unless 0) of the mode 7 background for scanline y (0-based)"""
        m = self.m7
        sext = lambda v: v - 0x2000 if v & 0x2000 else v
        clip = lambda n: n | ~1023 if n & 0x2000 else n & 1023
        a, b, c, d = (m[k] for k in ("a", "b", "c", "d"))
        if y in self.hdma and "m7" in self.hdma[y]: a, b, c, d = self.hdma[y]["m7"]
        cx, cy, hx, vy = sext(m["cx"] & 0x1FFF), sext(m["cy"] & 0x1FFF), sext(m["hofs"] & 0x1FFF), sext(m["vofs"] & 0x1FFF)
        yy = y + vofs_bias
        ox = ((a * clip(hx - cx)) & ~63) + ((b * clip(vy - cy)) & ~63) + ((b * yy) & ~63) + (cx << 8)
        oy = ((c * clip(hx - cx)) & ~63) + ((d * clip(vy - cy)) & ~63) + ((d * yy) & ~63) + (cy << 8)
        xs = np.arange(W)
        px = (ox + a * xs) >> 8; py = (oy + c * xs) >> 8
        v = np.frombuffer(self.vram, dtype=np.uint8)
        px &= 1023; py &= 1023
        tile = v[((py >> 3) * 128 + (px >> 3)) * 2]
        return v[(tile.astype(np.int32) * 64 + (py & 7) * 8 + (px & 7)) * 2 + 1]

    # ---- sprites ----
    def sprites(self, line_range=range(H)):
        """-> dict line -> list of (x0, pixels(row array of colour index), pal, prio) in OAM priority order; implemented as full-screen
        planes instead: returns (idx plane (H, W) uint8, pal plane, prio plane) with later-lower OAM entries winning"""
        obsel = self.regs.get(0x2101, 0)
        size_sel = obsel >> 5; gap = ((obsel >> 3) & 3); nbase = (obsel & 7) << 13        # word addresses
        (sw, sh), (lw, lh) = OBJ_SIZES[size_sel]
        chars = self.chars(4)
        idx = np.zeros((H + 64, W + 64), dtype=np.uint8); pal = np.zeros_like(idx); pri = np.zeros_like(idx)
        oam = self.oam
        order = range(127, -1, -1)
        for i in order:                                    # lower index = higher priority: draw back to front
            x = oam[i * 4]; y = oam[i * 4 + 1]; t = oam[i * 4 + 2]; a = oam[i * 4 + 3]
            hi = oam[512 + (i >> 2)] >> ((i & 3) * 2) & 3
            x |= (hi & 1) << 8
            if x >= 256: x -= 512
            big = hi >> 1 & 1
            w, h = (lw, lh) if big else (sw, sh)
            y = y & 0xFF
            if y >= 240 and y + h <= 256: continue
            name = t | (a & 1) << 8
            hfl, vfl = a >> 6 & 1, a >> 7 & 1
            for ty in range(h // 8):
                for tx in range(w // 8):
                    ttx = (w // 8 - 1 - tx) if hfl else tx
                    tty = (h // 8 - 1 - ty) if vfl else ty
                    ch = ((name & 0xF0) + 16 * tty + ((name & 0xF) + ttx) % 16) | (name & 0x100)
                    ch &= 0x1FF
                    word = nbase + (0x1000 * gap + 0x1000 if ch & 0x100 else 0) + (ch & 0xFF) * 16
                    c = (word // 16) % len(chars)
                    px = chars[c]
                    if hfl: px = px[:, ::-1]
                    if vfl: px = px[::-1, :]
                    X, Y = x + tx * 8, y + ty * 8
                    for yy in range(8):
                        yo = Y + yy
                        if yo < 0: continue
                        yo = yo % 256
                        if yo >= H: continue
                        for xx in range(8):
                            xo = X + xx
                            if 0 <= xo < W and px[yy, xx]:
                                idx[yo, xo] = px[yy, xx]; pal[yo, xo] = (a >> 1) & 7; pri[yo, xo] = (a >> 4) & 3
        return idx[:H, :W], pal[:H, :W], pri[:H, :W]

    # ---- windows ----
    def window_mask(self, line, sel_reg, nibble, logic_reg, logic_shift, wsel_shift=None):
        """boolean (W,) mask: True = pixel is inside the combined window for a layer with window settings nibble at sel_reg"""
        sel = (self.reg(sel_reg, line) >> nibble) & 0xF
        w1_inv, w1_en, w2_inv, w2_en = sel & 1, sel >> 1 & 1, sel >> 2 & 1, sel >> 3 & 1
        xs = np.arange(W)
        m1 = (xs >= self.reg(0x2126, line)) & (xs <= self.reg(0x2127, line))
        m2 = (xs >= self.reg(0x2128, line)) & (xs <= self.reg(0x2129, line))
        if w1_inv: m1 = ~m1
        if w2_inv: m2 = ~m2
        if w1_en and w2_en:
            lg = (self.reg(logic_reg, line) >> logic_shift) & 3
            return [m1 | m2, m1 & m2, m1 ^ m2, ~(m1 ^ m2)][lg]
        if w1_en: return m1
        if w2_en: return m2
        return np.zeros(W, dtype=bool)

    # ---- render ----
    def render(self, vofs_bias=0):
        mode = self.regs.get(0x2105, 1) & 7
        bg = {}
        if mode != 7:
            for n in (1, 2, 3):
                bg[n] = self.bg_plane(n, mode)
        sidx, spal, spri = self.sprites()
        cg = self.cg
        out = np.zeros((H, W, 3), dtype=np.uint8)
        xs = np.arange(W)
        bg3hi = bool(self.regs.get(0x2105, 0) & 8)
        for y in range(H):
            tm = self.reg(0x212C, y); ts = self.reg(0x212D, y); tmw = self.reg(0x212E, y); tsw = self.reg(0x212F, y)
            cgws = self.reg(0x2130, y); cgadsub = self.reg(0x2131, y)
            fixed = self._fixed(y)
            def screen(layers_mask, win_mask):
                """-> color index per pixel (cgram index or -1 for backdrop), and layer id (0 backdrop, 1-3 BG, 4 OBJ, with obj pal)"""
                col = np.full(W, -1, dtype=np.int32); lid = np.zeros(W, dtype=np.int32); objpal = np.zeros(W, dtype=np.int32)
                best = np.full(W, -1)
                # layer order, front first: (layer, priority); layer "o" = sprites
                if mode == 7:
                    order = [("o", 3), ("o", 2), ("o", 1), (1, 0), ("o", 0)]
                elif mode == 1 and bg3hi:
                    order = [(3, 1), ("o", 3), (1, 1), (2, 1), ("o", 2), (1, 0), (2, 0), ("o", 1), ("o", 0), (3, 0)]
                elif mode == 1:
                    order = [("o", 3), (1, 1), (2, 1), ("o", 2), (1, 0), (2, 0), ("o", 1), (3, 1), ("o", 0), (3, 0)]
                else:
                    order = [("o", 3), (1, 1), (2, 1), ("o", 2), (1, 0), (2, 0), ("o", 1), ("o", 0)]
                r = len(order)
                for L, p in order:
                    r -= 1
                    if L == "o":
                        if not layers_mask & 0x10: continue
                        sel = (sidx[y] != 0) & (spri[y] == p)
                        wm = self.window_mask(y, 0x2125, 0, 0x212B, 0) if (win_mask & 0x10) else np.zeros(W, bool)
                        sel &= ~wm
                        c = 128 + spal[y] * 16 + sidx[y]
                        lidv = 4
                    elif mode == 7:
                        if not layers_mask & 1: continue
                        ci = self.mode7_line(y, vofs_bias)
                        sel = ci != 0
                        if win_mask & 1: sel &= ~self.window_mask(y, 0x2123, 0, 0x212A, 0)
                        c = ci; lidv = 1
                    else:
                        if not layers_mask & (1 << (L - 1)): continue
                        plane, pal, pri, bpp = bg[L]
                        h, v = self.scroll.get(L, (0, 0))
                        h = self.hdma.get(y, {}).get(("h", L), h); v = self.hdma.get(y, {}).get(("v", L), v)
                        ph, pv = plane.shape[1], plane.shape[0]
                        row = (y + v + vofs_bias) % pv
                        cols = (xs + h) % ph
                        ci = plane[row, cols]; pp = pri[row, cols]; pl = pal[row, cols]
                        sel = (ci != 0) & (pp == p)
                        if win_mask & (1 << (L - 1)):
                            sh = {1: (0x2123, 0, 0x212A, 0), 2: (0x2123, 4, 0x212A, 2), 3: (0x2124, 0, 0x212A, 4)}[L]
                            sel &= ~self.window_mask(y, sh[0], sh[1], sh[2], sh[3])
                        c = pl * (16 if bpp == 4 else 4) + ci
                        lidv = L
                    upd = sel & (best < r)
                    col = np.where(upd, c, col); lid = np.where(upd, lidv, lid); best = np.where(upd, r, best)
                    if L == "o": objpal = np.where(upd, spal[y], objpal)
                return col, lid, objpal
            main_col, main_lid, main_op = screen(tm, tmw)
            backdrop = cg[0]
            def rgb_of(col):
                c = np.where(col >= 0, cg[np.clip(col, 0, 255)], backdrop)
                return bgr555_to_rgb(c)
            mr, mg, mb = rgb_of(main_col)
            # colour window / math
            cw = self.window_mask(y, 0x2125, 4, 0x212B, 2)
            # CGWSEL bits 5-4: colour math enabled 0 always, 1 inside colour window, 2 outside, 3 never; bits 7-6: force black 0 never, 1 outside, 2 inside, 3 always
            allowed = {0: np.ones(W, bool), 1: cw, 2: ~cw, 3: np.zeros(W, bool)}[(cgws >> 4) & 3]
            force_black = {0: np.zeros(W, bool), 1: ~cw, 2: cw, 3: np.ones(W, bool)}[(cgws >> 6) & 3]
            # layers that take part in math
            lm = np.zeros(W, bool)
            for L, bit in ((1, 1), (2, 2), (3, 4)):
                if cgadsub & bit: lm |= main_lid == L
            if cgadsub & 0x10: lm |= (main_lid == 4) & (main_op >= 4)
            if cgadsub & 0x20: lm |= main_lid == 0
            do = lm & allowed
            half_ok = np.zeros(W, bool)                       # halving only applies against a real sub-screen pixel, not the fixed colour
            if cgws & 2:
                s_col, s_lid, _ = screen(ts, tsw)
                half_ok = s_lid != 0
                sr, sg, sb = rgb_of(s_col)
                sr = np.where(s_lid == 0, fixed[0], sr); sg = np.where(s_lid == 0, fixed[1], sg); sb = np.where(s_lid == 0, fixed[2], sb)
            else:
                sr, sg, sb = (np.full(W, fixed[i]) for i in range(3))
            sub = bool(cgadsub & 0x80); half = bool(cgadsub & 0x40)
            res = []
            for m, s in ((mr, sr), (mg, sg), (mb, sb)):
                if sub: r_ = m - s
                else: r_ = m + s
                if half: r_ = np.where(half_ok, r_ >> 1, r_)
                r_ = np.clip(r_, 0, 31)
                res.append(np.where(do, r_, m))
            mr, mg, mb = res
            blk = force_black
            mr, mg, mb = (np.where(blk, 0, v) for v in (mr, mg, mb))
            bright = self.reg(0x2100, y) & 15
            sc = (bright + 1) / 16.0 if (self.reg(0x2100, y) & 0x80) == 0 else 0
            rgb = np.stack([mr, mg, mb], axis=1).astype(np.float64)
            rgb = np.floor(rgb * sc)
            out[y] = to8(rgb)
        return out

    def _fixed(self, y):
        v = self.reg(0x2132, y)
        # last COLDATA write only carries one component; the full fixed colour is kept in regs["fixed"] when known
        f = self.regs.get("fixed", (0, 0, 0))
        hd = self.hdma.get(y, {})
        if "fixed" in hd: f = hd["fixed"]
        return f
