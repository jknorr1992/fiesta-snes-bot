#!/usr/bin/env python3
"""World-map connectivity per vehicle from the ROM. Tile property byte 0 (file offset 0xFEA00 + 576 * world + 3 * tile) is a bit mask of
the vehicles that can enter the tile (field-main.asm _c011b0): 0x01 foot, 0x02 chocobo, 0x04 black chocobo, 0x08 hiryuu, 0x10 submarine,
0x20 ship, 0x40 airship. Byte 1 bits 0x10/0x20/0x40/0x80 mean chocobo/black chocobo/hiryuu/airship CANNOT land there (VehicleCanLandTbl).
Vehicle ids: 0 foot, 1 chocobo, 2 black chocobo, 3 hiryuu, 4 submarine, 5 ship, 6 airship."""
import os, struct, sys
from collections import deque
sys.path.insert(0, os.path.dirname(__file__))
from ffv_decomp import world_row

TILESET = [0, 1, 0, 2, 2]  # WorldTilesetTbl: map index -> tile property set (the merged world 3 reuses set 0)
VEH_BIT = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40]
LAND_BIT = {1: 0x10, 2: 0x20, 3: 0x40, 6: 0x80}

class World:
    def __init__(self, rom_bytes):
        self.rom = rom_bytes
        self.grid, self.prop0, self.prop1 = {}, {}, {}
        self._comp, self._mod_grid = {}, {}
        self.mods = self._load_mods(rom_bytes)
        for w in range(5):  # world map indices 0-4; 3 and 4 are the underwater maps
            rows = []
            for y in range(256):
                p = struct.unpack_from("<H", rom_bytes, 0xFE000 + 512 * w + 2 * y)[0]
                rows.append(world_row(rom_bytes, 0x70000 + p))
            self.grid[w] = rows
            base = 0xFEA00 + 576 * TILESET[w]
            self.prop0[w] = [rom_bytes[base + 3 * t] for t in range(192)]
            self.prop1[w] = [rom_bytes[base + 3 * t + 1] for t in range(192)]

    @staticmethod
    def _load_mods(rom):
        """World map changes made while an event flag is set (WorldMod in field-main.asm, table at 0x6ABD / 0x726C):
        {map: [(flag, x, y, [tiles])]}; entry = y, x, size, switch, tile pointer; flag = 256 + ((switch + 0xD0) & 0xFF)."""
        ptrs = struct.unpack_from("<6H", rom, 0x6ABD)
        out = {}
        for m in range(5):
            lst = []
            for o in range(ptrs[m], ptrs[m + 1], 6):
                y, x, size, sw, tp = struct.unpack_from("<BBBBH", rom, o)
                lst.append((256 + ((sw + 0xD0) & 0xFF), x, y, list(rom[tp:tp + size])))
            out[m] = lst
        return out

    def mod_key(self, w, flags):
        """Which world-modification flags of map w are set (flags is the simulator's flag bit mask)."""
        return tuple(sorted({f for f, _, _, _ in self.mods[w] if flags >> f & 1}))

    def rows(self, w, mk=()):
        if not mk:
            return self.grid[w]
        k = (w, mk)
        if k not in self._mod_grid:
            rows = [bytearray(r) for r in self.grid[w]]
            for f, x, y, tiles in self.mods[w]:
                if f in mk:
                    for i, t in enumerate(tiles):
                        rows[y][(x + i) % 256] = t
            self._mod_grid[k] = [bytes(r) for r in rows]
        return self._mod_grid[k]

    def passable(self, w, v, x, y, mk=()):
        t = self.rows(w, mk)[y][x]
        return t < 192 and self.prop0[w][t] & VEH_BIT[v] != 0

    def can_land(self, w, v, x, y, mk=()):
        t = self.rows(w, mk)[y][x]
        return t < 192 and v in LAND_BIT and self.prop1[w][t] & LAND_BIT[v] == 0

    def comps(self, w, v, mk=()):
        """Component-id grid (list of lists, 0 = impassable) for map w, vehicle v."""
        k = (w, v, mk)
        if k in self._comp:
            return self._comp[k]
        ids = [[0] * 256 for _ in range(256)]
        n = 0
        for y0 in range(256):
            for x0 in range(256):
                if ids[y0][x0] or not self.passable(w, v, x0, y0, mk):
                    continue
                n += 1
                ids[y0][x0] = n
                q = deque([(x0, y0)])
                while q:
                    x, y = q.popleft()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = (x + dx) % 256, (y + dy) % 256  # the world wraps
                        if not ids[ny][nx] and self.passable(w, v, nx, ny, mk):
                            ids[ny][nx] = n
                            q.append((nx, ny))
        self._comp[k] = ids
        return ids

    def comp_of(self, w, v, x, y, mk=()):
        return self.comps(w, v, mk)[y][x]

    def touches(self, w, v, comp, x, y, mk=()):
        """Is tile (x, y) inside component `comp` or next to it (entrances and triggers sit on tiles you step onto)?"""
        ids = self.comps(w, v, mk)
        if comp and ids[y][x] == comp:
            return True
        return any(ids[(y + dy) % 256][(x + dx) % 256] == comp for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))

if __name__ == "__main__":
    w = World(open(sys.argv[1], "rb").read())
    for m in range(5):
        for v in range(7):
            ids = w.comps(m, v)
            n = max(max(r) for r in ids)
            print("world", m, "vehicle", v, "components", n)
    print("pocket comp at (175,143) foot:", w.comp_of(0, 0, 175, 143), "(156,139):", w.comp_of(0, 0, 156, 139), "(183,133):", w.comp_of(0, 0, 183, 133))
