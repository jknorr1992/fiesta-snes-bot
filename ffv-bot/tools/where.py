#!/usr/bin/env python3
"""Render the map the party is on (read from the running emulator) with the party position circled: where.py <rom> [out.png]"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from play import Game
from map_render import Maps, markers, draw
from PIL import ImageDraw

def main(rom_path, out=None):
    g = Game(); b = g.read(0xAD4, 6)
    m, x, y = (b[0] | b[1] << 8) & 0xFFF, b[4], b[5]
    rom = open(rom_path, "rb").read(); maps = Maps(rom)
    img = draw(maps.render(m), markers(rom_path, maps, m, [])).convert("RGBA")
    d = ImageDraw.Draw(img); d.ellipse([x * 16 - 6, y * 16 - 6, x * 16 + 22, y * 16 + 22], outline=(255, 0, 0, 255), width=4)
    out = out or "/tmp/where_map%d_%d_%d.png" % (m, x, y)
    img.convert("RGB").save(out); print(m, x, y, out); return out

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
