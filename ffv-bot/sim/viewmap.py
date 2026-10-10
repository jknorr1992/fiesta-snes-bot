"""Render a local map (tools/map_render) with the simulator's reachable tiles tinted and markers; usage: viewmap.py <pkl state> out.png"""
import sys, pickle
sys.path.insert(0, '.')
from sim import *
sys.path.insert(0, '../tools')
import map_render

def render(S, s, out, scale=1):
    rom = open(DEFAULT_ROM, 'rb').read()
    maps = map_render.Maps(rom)
    img = maps.render(s.map)
    from PIL import ImageDraw
    lm = S.localmap(s)
    d = ImageDraw.Draw(img, "RGBA")
    # reachable set (ignoring terminal rules): plain BFS over lm.moves
    import collections
    start = (s.x, s.y, s.z); seen = {start}; q = collections.deque([start])
    while q:
        c = q.popleft()
        for dd, n in lm.moves(*c):
            if n not in seen: seen.add(n); q.append(n)
    for x, y, z in seen:
        d.rectangle([x * 16, y * 16, x * 16 + 15, y * 16 + 15], fill=(0, 255, 0, 70))
    for x, y, ts in S.R.event_triggers(s.map):
        d.rectangle([x * 16, y * 16, x * 16 + 15, y * 16 + 15], outline=(255, 140, 0, 255), width=2)
        d.text((x * 16 + 1, y * 16 + 1), "t%d" % ts, fill=(255, 255, 255, 255))
    for e in S.R.entrances(s.map):
        d.rectangle([e["x"] * 16, e["y"] * 16, e["x"] * 16 + 15, e["y"] * 16 + 15], outline=(0, 200, 255, 255), width=2)
        d.text((e["x"] * 16 + 1, e["y"] * 16 + 1), "->%d" % e["map"], fill=(255, 255, 255, 255))
    for i, script, x, y, st in S.objects(s):
        d.rectangle([x * 16, y * 16, x * 16 + 15, y * 16 + 15], outline=(60, 255, 60, 255), width=2)
        d.text((x * 16 + 1, y * 16 + 1), "n%d" % script, fill=(255, 255, 255, 255))
    d.rectangle([s.x * 16, s.y * 16, s.x * 16 + 15, s.y * 16 + 15], outline=(255, 0, 255, 255), width=3)
    img.convert("RGB").save(out)
    print("wrote", out, "reachable tiles", len({(a, b) for a, b, _ in seen}))

if __name__ == "__main__":
    S = Sim(); s = pickle.load(open(sys.argv[1], 'rb')); render(S, s, sys.argv[2])
