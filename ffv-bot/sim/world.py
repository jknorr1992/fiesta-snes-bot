"""World-map (map ids 0-4) engine, transcribed from field-main.asm: CheckPlayerMoveWorld, _c01055, _c010c8 (disembark), CheckVehicle.

World tile properties are 3 bytes per tile (file offset 0xFEA00 + 576 * tileset + 3 * tile):
  byte0: vehicle pass mask, indexed by vehicle type: 0x01 foot, 0x02 chocobo, 0x04 black chocobo, 0x08 hiryuu, 0x10 submarine, 0x20 ship, 0x40 airship
  byte1: low nibble = directions the tile may be left in (up 8, right 1, down 4, left 2) for foot/chocobo;
         inverted for ship/submarine (a SET bit blocks leaving); high nibble = bit 0x10/0x20/0x40/0x80: chocobo/black chocobo/hiryuu/airship cannot land
Vehicle type numbers: 0 foot, 1 chocobo, 2 black chocobo, 3 hiryuu, 4 submarine, 5 ship, 6 airship.
"""
import struct
from ffv_decomp import world_row

TILESET = [0, 1, 0, 2, 2]
VEH_BIT = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
LAND_BIT = {1: 0x10, 2: 0x20, 3: 0x40, 6: 0x80}
LEAVE = {1: 0x08, 2: 0x01, 3: 0x04, 4: 0x02}
DIRS = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}

class World:
    def __init__(self, rom_bytes):
        r = rom_bytes
        self.grid, self.p0, self.p1, self.p2 = {}, {}, {}, {}
        self._mod_grid = {}
        self.mods = self._load_mods(r)
        for w in range(5):
            rows = []
            for y in range(256):
                p = struct.unpack_from("<H", r, 0xFE000 + 512 * w + 2 * y)[0]
                rows.append(world_row(r, 0x70000 + p))
            self.grid[w] = rows
            base = 0xFEA00 + 576 * TILESET[w]
            self.p0[w] = [r[base + 3 * t] for t in range(192)]
            self.p1[w] = [r[base + 3 * t + 1] for t in range(192)]
            self.p2[w] = [r[base + 3 * t + 2] for t in range(192)]

    @staticmethod
    def _load_mods(r):
        ptrs = struct.unpack_from("<6H", r, 0x6ABD)
        out = {}
        for m in range(5):
            lst = []
            for o in range(ptrs[m], ptrs[m + 1], 6):
                y, x, size, sw, tp = struct.unpack_from("<BBBBH", r, o)
                lst.append((256 + ((sw + 0xD0) & 0xFF), x, y, list(r[tp:tp + size])))
            out[m] = lst
        return out

    def mod_key(self, w, flags):
        return tuple(sorted({f for f, _, _, _ in self.mods[w] if flags >> f & 1}))

    def rows(self, w, mk=()):
        if not mk: return self.grid[w]
        k = (w, mk)
        if k not in self._mod_grid:
            rows = [bytearray(r) for r in self.grid[w]]
            for f, x, y, tiles in self.mods[w]:
                if f in mk:
                    for i, t in enumerate(tiles): rows[y][(x + i) & 255] = t
            self._mod_grid[k] = [bytes(r) for r in rows]
        return self._mod_grid[k]

    def tile(self, w, x, y, mk=()): return self.rows(w, mk)[y & 255][x & 255]

    def step(self, w, veh, x, y, d, mk=(), vehicle_ahead=False):
        """Try to move one tile from (x, y) in direction d with vehicle type `veh` (0 = foot).
        Returns (nx, ny, disembark) or None. `vehicle_ahead`: a ship/submarine sits on the destination tile (foot party may always step there)."""
        dx, dy = DIRS[d]
        nx, ny = (x + dx) & 255, (y + dy) & 255
        if veh == 0 and vehicle_ahead: return nx, ny, False
        ct = self.tile(w, x, y, mk); dt = self.tile(w, nx, ny, mk)
        if ct >= 192 or dt >= 192: return None
        ok = True
        if veh in (0, 1):
            ok = bool(self.p1[w][ct] & LEAVE[d])
        elif veh in (4, 5):
            ok = bool((self.p1[w][ct] ^ 0x0F) & LEAVE[d])
        if ok and self.p0[w][dt] & VEH_BIT[veh]:
            return nx, ny, False
        # blocked: a ship/submarine steps ashore if the destination is passable on foot
        if veh in (4, 5) and self.p0[w][dt] & 1: return nx, ny, True
        return None

    def can_land(self, w, veh, x, y, mk=()):
        t = self.tile(w, x, y, mk)
        if t >= 192 or veh not in LAND_BIT: return veh in (4, 5)
        return self.p1[w][t] & LAND_BIT[veh] == 0
