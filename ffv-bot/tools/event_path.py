#!/usr/bin/env python3
"""Search the FFV event graph from a new game (event $10) towards the ending ($bf0f) using only ROM data.

State: map, position, vehicle, parked vehicles, flags (bit n < 512 = event flag n, bit 512 + n = NPC flag n), party mask, parent map.
Actions: step on an event trigger, talk to a visible NPC, take an entrance, leave through the map exit (back to the parent world map,
then trigger script 14), board a parked vehicle, land. World-map positions must be reachable with the current vehicle (tile property
byte 0, see world_model.py); local maps are treated as fully walkable. RAM-compare conditions ($fa/$f9/$f8/$f5) are assumed true and
facing/button conditions satisfiable, so this over-approximates; world-map tile changes made by events are not modelled.
Usage: event_path.py <rom.sfc> [max_states] [out.json]
"""
import json, os, sys, heapq, itertools
sys.path.insert(0, os.path.dirname(__file__))
from event_vm import Rom
from world_model import World, LAND_BIT

NPC = 512
WORLD_MAPS = 5  # map ids below this are world maps (field-main.asm: cpx #$0005)
DEBUG_EVENTS = (244, 1939, 1861)  # set every flag: not part of the story

class St:
    __slots__ = ("map", "x", "y", "veh", "parked", "flags", "party", "parent", "ended")
    def __init__(self, **k):
        self.map, self.x, self.y, self.veh, self.parked = 0, 0, 0, 0, frozenset()
        self.flags, self.party, self.parent, self.ended = 0, 0, None, False
        for a, b in k.items(): setattr(self, a, b)
    def copy(self):
        n = St()
        for a in St.__slots__: setattr(n, a, getattr(self, a))
        return n

class Sim:
    def __init__(self, rom_path, allowed=None):
        self.allowed = allowed  # optional set of event ids worth running (see event_relevance.py)
        self.rom = Rom(rom_path)
        self.world = World(self.rom.r)
        self._ops, self._maps, self._land = {}, {}, {}
        self.keymask = (1 << 1024) - 1  # flags that distinguish states (see search(): only the flags some relevant group tests)

    def ops(self, e):
        if e not in self._ops:
            self._ops[e] = self.rom.event_ops(e) if 0 < e < 1939 and e not in DEBUG_EVENTS else []
        return self._ops[e]

    def map_data(self, m):
        if m not in self._maps:
            r = self.rom
            self._maps[m] = {"trig": r.triggers(m) if m < 511 else [], "ent": r.entrances(m) if m < 511 else [],
                             "npc": r.npcs(m) if m < 511 else []}
        return self._maps[m]

    @staticmethod
    def conds_ok(conds, flags):
        for c in conds:
            if c[0] == "set" and not flags >> c[1] & 1: return False
            if c[0] == "clear" and flags >> c[1] & 1: return False
        return True

    def first_group(self, groups, flags):
        for conds, ev in groups:
            if self.conds_ok(conds, flags):
                return ev
        return None

    # ---- event execution; returns list of (state, log) ----
    def run(self, st, ev, log):
        return self._run_ops([(st.copy(), list(log))], self.ops(ev), 0)

    def _run_ops(self, results, ops, depth):
        for op, d in ops:
            nxt = []
            for st, lg in results:
                if st.ended:
                    nxt.append((st, lg)); continue
                if op in (0xA2, 0xA4):
                    st.flags |= 1 << d["flag"]; lg.append(("set", d["flag"]))
                elif op in (0xA3, 0xA5):
                    st.flags &= ~(1 << d["flag"]); lg.append(("clear", d["flag"]))
                elif op == 0xCA:
                    st.flags |= 1 << (NPC + d["npc_flag"])
                elif op == 0xCB:
                    st.flags &= ~(1 << (NPC + d["npc_flag"]))
                elif op in (0xE0, 0xE1, 0xE3):
                    if d["map"] >= WORLD_MAPS and st.map < WORLD_MAPS and d["parent"]:
                        st.parent = (st.map, st.x, st.y)
                    x = d["world_x"] if d["map"] < WORLD_MAPS else d["x"]
                    st.map, st.x, st.y = d["map"], x, d["y"]
                    if d["map"] < WORLD_MAPS:
                        st.veh = d["vehicle"]
                        st.parked = frozenset(p for p in st.parked if p[0] != st.veh or st.veh == 0)
                        st.parent = None
                    lg.append(("map", d["map"], x, d["y"], d["vehicle"]) if d["map"] < WORLD_MAPS else ("map", d["map"], x, d["y"]))
                elif op == 0xD6:
                    st.map = d["map"]; st.veh = d["vehicle"]; lg.append(("world", d["map"], d["vehicle"]))
                elif op == 0xD2:
                    st.parked = frozenset(p for p in st.parked if p[0] != d["vehicle"]) | {(d["vehicle"], d["map"], d["x"], d["y"])}
                    lg.append(("vehicle", d["vehicle"], d["map"], d["x"], d["y"]))
                elif op == 0xC2:
                    st.parked = frozenset(p for p in st.parked if p[0] != d["vehicle"])
                elif op == 0xB7:
                    bit = 1 << d["char"]
                    st.party = st.party & ~bit if d["remove"] else st.party | bit
                    lg.append(("party", d["char"], "remove" if d["remove"] else "add"))
                elif op in (0xBD, 0xE2):
                    lg.append(("battle", d["battle"]))
                elif op in (0xAA, 0xAC, 0xC6):
                    lg.append(({0xAA: "item", 0xAC: "magic", 0xC6: "job"}[op], d["id"]))
                elif op == 0xBF:
                    sub = d["sub"]
                    if sub == 0x0F:
                        st.ended = True; lg.append(("ending",))
                    elif sub == 0x01 and st.parent:
                        st.map, st.x, st.y = st.parent; st.parent = None
                    elif sub in (0x02, 0x0B):
                        lg.append(("scene", {2: "crystal shatters", 0x0B: "tower of walse sinking"}[sub]))
                elif op == 0xCD and depth < 8:
                    nxt.extend(self._run_ops([(st, lg)], self.ops(d["event"]), depth + 1)); continue
                elif op == 0xF0:
                    yes = bytes.fromhex(d["yes"])
                    if yes[:1] == b"\xcd" and depth < 8:
                        nxt.extend(self._run_ops([(st.copy(), list(lg))], self.ops(int.from_bytes(yes[1:3], "little")), depth + 1))
                nxt.append((st, lg))
            results = nxt
        return results

    # ---- world helpers ----
    def region(self, st):
        if st.map >= WORLD_MAPS: return 0
        return self.world.comp_of(st.map, st.veh, st.x, st.y, self.world.mod_key(st.map, st.flags))

    def landings(self, m, v, comp, mk=()):
        """Where can vehicle v (currently in component comp of world m) put the party on foot? -> [(x, y, park_x, park_y)]"""
        k = (m, v, comp, mk)
        if k in self._land:
            return self._land[k]
        w = self.world
        out, seen_foot = [], set()
        vids, fids = w.comps(m, v, mk), w.comps(m, 0, mk)
        interesting = {(x, y) for x, y, _ in self.map_data(m)["trig"]} | {(e["x"], e["y"]) for e in self.map_data(m)["ent"]}
        if v in (4, 5):  # boats: step from a water tile in comp onto an adjacent land tile
            for y in range(256):
                for x in range(256):
                    if vids[y][x] != comp: continue
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = (x + dx) % 256, (y + dy) % 256
                        f = fids[ny][nx]
                        if f and f not in seen_foot:
                            seen_foot.add(f); out.append((nx, ny, x, y))
        else:  # flying/riding vehicles land on landable tiles; one landing per foot component that holds something to do
            want = {fids[y][x] for (x, y) in interesting if fids[y][x]} | {fids[ny][nx] for (x, y) in interesting
                    for nx, ny in (((x + 1) % 256, y), ((x - 1) % 256, y), (x, (y + 1) % 256), (x, (y - 1) % 256)) if fids[ny][nx]}
            for y in range(256):
                for x in range(256):
                    f = fids[y][x]
                    if f in want and f not in seen_foot and vids[y][x] == comp and w.can_land(m, v, x, y, mk):
                        seen_foot.add(f); out.append((x, y, x, y))
        self._land[k] = out
        return out

    # ---- actions ----
    def actions(self, st):
        m, flags = st.map, st.flags
        md = self.map_data(m)
        acts = []
        if m < WORLD_MAPS:
            comp = self.region(st)
            mk = self.world.mod_key(m, flags)
            ok = lambda x, y: self.world.touches(m, st.veh, comp, x, y, mk)
        else:
            ok = lambda x, y: True
        blocked = {(n["x"], n["y"]) for n in md["npc"] if flags >> (NPC + n["script"]) & 1} if m >= WORLD_MAPS else set()
        for x, y, t in md["trig"]:
            if ok(x, y) and (x, y) not in blocked:
                ev = self.first_group(self.rom.trigger_script(t), flags)
                if ev and (self.allowed is None or ev in self.allowed): acts.append((("trigger", m, x, y, ev), ev))
        for n in md["npc"]:
            if flags >> (NPC + n["script"]) & 1:
                ev = self.first_group(self.rom.npc_script(n["script"]), flags)
                if ev and (self.allowed is None or ev in self.allowed): acts.append((("npc", m, n["x"], n["y"], n["script"], ev), ev))
        if m < WORLD_MAPS and st.veh in (2, 3, 6):
            pass  # flying vehicles cannot enter places without landing
        else:
            for e in md["ent"]:
                if ok(e["x"], e["y"]) and (e["x"], e["y"]) not in blocked:
                    acts.append((("entrance", m, e["x"], e["y"], e["map"], e["dx"], e["dy"]), None))
        if m >= WORLD_MAPS and st.parent:
            acts.append((("exit", m), None))
        if m < WORLD_MAPS:
            if st.veh == 0:
                for (v, pm, px, py) in st.parked:
                    if pm == m and self.world.touches(m, 0, comp, px, py, mk):
                        acts.append((("board", v, px, py), None))
            else:
                for (lx, ly, px, py) in self.landings(m, st.veh, comp, mk):
                    acts.append((("land", lx, ly, px, py), None))
        return acts

    def apply(self, st, act, ev):
        if ev is not None:
            return self.run(st, ev, [])
        kind = act[0]
        s2 = st.copy()
        if kind == "entrance":
            _, m, x, y, dm, dx, dy = act
            if dm < WORLD_MAPS:
                s2.map, s2.x, s2.y, s2.parent = dm, dx, dy, None
            else:
                if m < WORLD_MAPS:
                    s2.parent = (m, x, y)
                s2.map, s2.x, s2.y = dm, dx & 0x3F, dy & 0x3F
            return [(s2, [("enter", dm, s2.x, s2.y)])]
        if kind == "exit":
            pm, px, py = st.parent
            s2.map, s2.x, s2.y, s2.parent, s2.veh = pm, px, py, None, 0
            ev14 = self.first_group(self.rom.trigger_script(14), s2.flags)
            return self.run(s2, ev14, [("exit", pm)]) if ev14 else [(s2, [("exit", pm)])]
        if kind == "board":
            _, v, px, py = act
            s2.veh, s2.x, s2.y = v, px, py
            s2.parked = frozenset(p for p in st.parked if p[0] != v)
            return [(s2, [("board", v)])]
        if kind == "land":
            _, lx, ly, px, py = act
            v = st.veh
            s2.parked = st.parked | {(v, st.map, px, py)}
            s2.veh, s2.x, s2.y = 0, lx, ly
            return [(s2, [("land", v, lx, ly)])]
        raise ValueError(act)

    def key(self, st):
        return (st.map, st.flags & self.keymask, st.party, st.veh, st.parked, st.parent, self.region(st), st.ended)

NEED_MASK = (1 << 512) - 1

def progress(st):
    return bin(st.flags & NEED_MASK).count("1")

def self_priority(st, depth):
    return depth - 3 * progress(st)

def search(rom_path, max_states=300000, verbose=True, relevant=True):
    global NEED_MASK
    allowed = None
    if relevant:
        from event_relevance import compute
        rom0 = Rom(rom_path)
        _, _, rel, need, _ = compute(rom0)
        allowed = set(rel)
        NEED_MASK = sum(1 << f for f in need if f < 512)
    sim = Sim(rom_path, allowed)
    if relevant:
        sim.keymask = sum(1 << f for f in need if f < 1024) | sum(1 << f for f in range(256, 512) if sim.world.mods and any(f == mf for m in sim.world.mods.values() for mf, _, _, _ in m))
        sim.keymask |= sum(1 << f for m in sim.world.mods.values() for mf, _, _, _ in m for f in (mf,))
    s0 = St(flags=sim.rom.initial_npc_flags() << NPC)
    tie = itertools.count()
    heap, seen, parent = [], {}, {}
    for st, lg in sim.run(s0, 16, [("start", 16)]):
        k = sim.key(st)
        if k not in seen:
            seen[k] = st; parent[k] = (None, lg); heapq.heappush(heap, (0, next(tie), k))
    best, end_key = (0, None), None
    while heap and len(seen) < max_states:
        prio, _, k = heapq.heappop(heap)
        st = seen[k]
        depth = prio + 3 * progress(st)
        if st.ended:
            end_key = k; break
        for act, ev in sim.actions(st):
            for s2, lg in sim.apply(st, act, ev):
                k2 = sim.key(s2)
                if k2 == k or k2 in seen: continue
                seen[k2] = s2; parent[k2] = (k, [("act",) + act] + lg)
                heapq.heappush(heap, (self_priority(s2, depth + 1), next(tie), k2))
        n = bin(st.flags & ((1 << 512) - 1)).count("1")
        if n > best[0]:
            best = (n, k)
            if verbose and n % 5 == 0: print("progress: flags set", n, "depth", depth, "states", len(seen), file=sys.stderr)
    target = end_key or best[1]
    path, k = [], target
    while k is not None:
        pk, lg = parent[k]; path.append((k, lg)); k = pk
    path.reverse()
    return path, bool(end_key), len(seen)

if __name__ == "__main__":
    path, reached_end, n = search(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 300000)
    print("states explored:", n, "reached ending:", reached_end, "path length:", len(path))
    for k, lg in path:
        print("map", k[0], "veh", k[3], [x for x in lg if x[0] != "act"][:8], [x for x in lg if x[0] == "act"])
