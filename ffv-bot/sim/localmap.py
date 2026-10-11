"""Local (sub-)map movement rules, transcribed from field-main.asm: CheckPlayerMoveSub, CheckPartyCanMove, UpdateCurrZLevel.

Tile properties (2 bytes per BG1 tile id):
  b1 bit0-1: passable z-levels (3 = stairs), bit2: bridge (passable from/to any level), bit3/4/5: sprite priority/hide/damage
  b2 low nibble: directions the tile may be LEFT in (up 8, right 1, down 4, left 2); bit6 forced facing (skips that test);
     bit7 auto-move tile (the party is moved in the direction coded in the low nibble); 0x00 = map exit; 0xFF = through-tile
The party carries a z-level ($c3): it is set to b1&3 whenever the party stands on a non-bridge, non-stairs tile, kept otherwise.
Coordinates wrap at 64.
"""
DIRS = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}      # up, right, down, left
LEAVE_MASK = {1: 0x08, 2: 0x01, 3: 0x04, 4: 0x02}
AUTO_DIR = {0x1: 2, 0x2: 4, 0x4: 3, 0x8: 1}               # b2 low nibble of an auto-move tile -> direction (_c0172a)

class LocalMap:
    def __init__(self, rom, m, tile_overrides=None, blocked=()):
        """tile_overrides: {(x, y): tile id} replacing BG1 tiles (event tile changes); blocked: tiles occupied by objects."""
        self.m = m
        self.layer = bytearray(rom.layer(m))
        if tile_overrides:
            for (x, y), t in tile_overrides.items(): self.layer[(y & 63) * 64 + (x & 63)] = t
        self.tp = rom.tileprops(m)
        self.blocked = set(blocked)

    def tile(self, x, y): return self.layer[(y & 63) * 64 + (x & 63)]
    def props(self, x, y): return self.tp[self.tile(x, y)]

    @staticmethod
    def z_after(b1, z):
        """party z-level after standing on a tile with byte1 b1"""
        if b1 & 4: return z
        if b1 & 3 == 3: return z
        return b1 & 3

    def step(self, x, y, z, d):
        """Try to walk one tile in direction d from (x, y) with z-level z. -> (nx, ny, nz) or None. z must already reflect the current tile."""
        b1, b2 = self.props(x, y)
        if not b2 & 0x40 and not b2 & LEAVE_MASK[d]: return None
        dx, dy = DIRS[d]
        nx, ny = (x + dx) & 63, (y + dy) & 63
        nb1, nb2 = self.props(nx, ny)
        if (nx, ny) in self.blocked:                     # object at the destination tile
            if nb1 & 4:
                if z == 1: return None
            elif b1 & 4:
                if z != 2: return None
            else: return None
        if b1 & 4:
            if not (nb1 & 4) and not (nb1 & 3 & z): return None
        else:
            if not (nb1 & ((b1 & 3) | 4)): return None
        return nx, ny, self.z_after(nb1, z)

    def forced(self, x, y):
        """auto-move tile: direction the party is pushed, or None"""
        b1, b2 = self.props(x, y)
        if b2 & 0x80 and b2 != 0xFF: return AUTO_DIR.get(b2 & 0x0F)
        return None

    def is_exit(self, x, y): return self.props(x, y)[1] == 0

    def moves(self, x, y, z):
        """all single-step successors from standing state (x, y, z): list of (dir, (nx, ny, nz)). Handles auto-move tiles."""
        mv = self.__dict__.setdefault("_mv", {})
        r = mv.get((x, y, z))
        if r is None: r = mv[(x, y, z)] = self._moves(x, y, z)
        return r

    def _moves(self, x, y, z):
        f = self.forced(x, y)
        if f:
            dx, dy = DIRS[f]
            nx, ny = (x + dx) & 63, (y + dy) & 63
            return [(f, (nx, ny, self.z_after(self.props(nx, ny)[0], z)))]
        out = []
        for d in (1, 2, 3, 4):
            r = self.step(x, y, z, d)
            if r: out.append((d, r))
        return out
