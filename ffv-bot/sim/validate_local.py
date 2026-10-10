"""Validate localmap.py against the live emulator: BFS on the predicted graph, walk there, probe all 4 directions on each tile.
usage: validate_local.py <state slot> [max tiles]"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from play import Game
from rom import Rom
from localmap import LocalMap, DIRS
from collections import deque

BTN = {1: "UP", 2: "RIGHT", 3: "DOWN", 4: "LEFT"}

def rd(g, off, n):
    out = b""
    for i in range(0, n, 128): out += g.read(off + i, min(128, n - i))
    return out

def pos(g): return (g.u8(0xAD8), g.u8(0xAD9), g.u16(0xAD6))

def tap(g, d):
    g.press(BTN[d], hold=3, gap=0); g.wait(14)

def main():
    slot, limit = int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 40
    g = Game(); R = Rom()
    g.load(slot)
    x0, y0, m = pos(g)
    assert g.u8(0xB53) == 1, "not a local map"
    live = rd(g, 0x10000, 4096); obj = rd(g, 0x13000, 4096)
    blocked = {(i % 64, i // 64) for i in range(4096) if obj[i] and obj[i] != 0xFF}
    lm = LocalMap(R, m, blocked=blocked)
    lm.layer = bytearray(live)     # use the live layer so only the movement rules are under test
    z0 = lm.z_after(lm.props(x0, y0)[0], 1)
    trig = {(x, y) for x, y, _ in R.event_triggers(m)} | {(e["x"], e["y"]) for e in R.entrances(m)}
    # BFS over predicted graph, avoiding trigger/exit tiles
    start = (x0, y0, z0); dist = {start: None}; q = deque([start]); order = []
    while q:
        s = q.popleft(); order.append(s)
        for d, n in lm.moves(*s):
            if n not in dist and (n[0], n[1]) not in trig and not lm.is_exit(n[0], n[1]) and not lm.forced(n[0], n[1]):
                dist[n] = (s, d); q.append(n)
    print("map", m, "start", start, "predicted reachable states", len(order), "tiles", len({(a, b) for a, b, _ in order}))
    seen = set(); bad = []; tested = 0
    cur = start
    def path(to):
        p = []
        while dist[to]: p.append(dist[to][1]); to = dist[to][0]
        return p[::-1]
    def goto(target):
        nonlocal cur
        # route from cur to target in predicted graph (BFS again from cur)
        prev = {cur: None}; qq = deque([cur])
        while qq:
            s = qq.popleft()
            if s == target: break
            for d, n in lm.moves(*s):
                if n not in prev and (n[0], n[1]) not in trig and not lm.is_exit(n[0], n[1]):
                    prev[n] = (s, d); qq.append(n)
        if target not in prev: return False
        p = []; t = target
        while prev[t]: p.append(prev[t][1]); t = prev[t][0]
        for d in p[::-1]:
            tap(g, d)
        x, y, _ = pos(g)
        cur = (x, y, lm.z_after(lm.props(x, y)[0], cur[2]))
        return (x, y) == (target[0], target[1])
    for s in order:
        if tested >= limit: break
        if (s[0], s[1]) in seen: continue
        if not goto(s): print("lost route to", s, "at", cur); break
        seen.add((s[0], s[1])); tested += 1
        x, y = s[0], s[1]
        for d in (1, 2, 3, 4):
            pred = lm.step(x, y, cur[2], d) is not None
            before = pos(g)
            tap(g, d)
            after = pos(g)
            moved = (after[0], after[1]) != (before[0], before[1])
            if after[2] != before[2]:
                print("map change at", (x, y), d); g.load(slot); cur = start; break
            if moved:
                dx, dy = DIRS[d]
                if (after[0], after[1]) != ((x + dx) & 63, (y + dy) & 63): print("odd move", (x, y), d, after)
                tap(g, {1: 3, 2: 4, 3: 1, 4: 2}[d])      # step back
                back = pos(g)
                if (back[0], back[1]) != (x, y): print("could not step back", back); cur = (back[0], back[1], cur[2])
            if pred != moved: bad.append(((x, y, cur[2]), d, pred, moved, lm.props(x, y), lm.props((x + DIRS[d][0]) & 63, (y + DIRS[d][1]) & 63)))
    print("tested tiles", tested, "mismatches", len(bad))
    for b in bad[:40]: print(b)

if __name__ == "__main__": main()
