#!/usr/bin/env python3
"""Monotone reachability over the FFV event graph (an over-approximation, so a blocker it reports is real).

event_path.py searches concrete states and drowns in flag combinations. Here the state is a pair of global flag sets
  S = event/NPC flags that can be SET at some point, C = flags that can be CLEAR at some point
plus the reachable "contexts" (local map | world map + vehicle + connected component) and the parked-vehicle positions.
A trigger/NPC group can run when each of its conditions is satisfiable (`set f` needs f in S, `clear f` needs f in C) and every
group before it in the same script can fail. Running an event adds the flags it sets to S and those it clears to C, and
adds the place where it leaves the party. Rounds repeat until nothing grows; each event records the round it first became
runnable, giving a dependency layering of the whole game, and the discovery order is a candidate event path.
Usage: event_reach.py <rom.sfc> [out.json]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_path import Sim, St, NPC, WORLD_MAPS
from event_relevance import compute
from event_vm import Rom

ALL = (1 << 1024) - 1
# events the data never starts but the story needs (verified by the user from play):
# first Antlion's Nest climb attempt (194) follows the nest entry scene (223); 195-198 are the later attempts
FOLLOWS = {223: (194,)}
# trigger events that are only valid in the context an event creates: 1861 (open water beside the Void ship, map 156) only after 1734/1762 (which call 1732)
ONLY_AFTER = {1861: (1734, 1762)}

class Reach:
    def __init__(self, rom_path, relevant=True):
        rom0 = Rom(rom_path)
        self.eff, self.groups, rel, self.need, _ = compute(rom0)
        self.sim = Sim(rom_path, set(rel) if relevant else None)
        self.rom, self.world = self.sim.rom, self.sim.world
        init_npc = self.rom.initial_npc_flags() << NPC
        self.S = init_npc
        self.C = ALL & ~init_npc
        self.ctx = {}           # (map, veh, comp) -> sample (x, y)
        self.parked = set()     # (vehicle, world map, x, y)
        self.parents = {}       # local map -> set of (world map, x, y) it can be left to
        self.log = []           # (round, kind, key, detail)
        self.first = {}         # event -> round
        self.ended = False
        self._runs = {}
        self._conds = {}
        self.ctx_src, self.flag_src, self.ev_src, self.parked_src = {}, {}, {}, {}
        self.rnd = 0
        self.changed = False
        self.special = set()    # contexts entered by the Void-ship events (see ONLY_AFTER)

    # ---- helpers ----
    def mk(self, w):
        return tuple(sorted({f for f, _, _, _ in self.world.mods[w] if self.S >> f & 1}))

    def ctx_of(self, m, veh, x, y):
        if m >= WORLD_MAPS:
            return (m, 0, 0, ())
        mk = self.mk(m)
        c = self.world.comp_of(m, veh, x, y, mk)
        if not c:  # placed on a tile this vehicle cannot normally enter (e.g. a town tile): it steps onto a neighbour
            ids = self.world.comps(m, veh, mk)
            near = [ids[(y + dy) % 256][(x + dx) % 256] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            near = [n for n in near if n]
            c = max(near, key=lambda n: sum(r.count(n) for r in ids)) if near else 0
        return (m, veh, c, mk)

    def add_ctx(self, m, veh, x, y, why, **prov):
        k = self.ctx_of(m, veh, x, y)
        if k not in self.ctx:
            self.ctx[k] = (x, y)
            self.ctx_src[k] = dict(prov, why=why, rnd=self.rnd)
            self.changed = True
            self.log.append((self.rnd, "context", k, why))
        return k

    def can_fail(self, c):
        if c[0] == "set": return bool(self.C >> c[1] & 1)
        if c[0] == "clear": return bool(self.S >> c[1] & 1)
        return True

    def can_pass(self, c):
        if c[0] == "set": return bool(self.S >> c[1] & 1)
        if c[0] == "clear": return bool(self.C >> c[1] & 1)
        return True

    def runnable(self, groups):
        """Events of a trigger/NPC script that can run, honouring that earlier groups must be able to fail."""
        out, falls = [], []
        for conds, ev in groups:
            if all(self.can_pass(c) for c in conds):
                out.append(ev)
                self._conds[ev] = (conds, list(falls))
            fl = [c[1] for c in conds if c[0] == "clear" and self.S >> c[1] & 1]
            if not any(self.can_fail(c) for c in conds):
                break  # this group always runs, nothing after it can
            if fl: falls.append(fl[0])  # to get past this group one of its `clear` flags must be set
        return out

    def apply_event(self, ev, m, x, y, veh, why, frm=None):
        if ev <= 0 or ev >= 1939 or (self.sim.allowed is not None and ev not in self.sim.allowed):
            return
        if ev not in self.first:
            self.first[ev] = self.rnd
            self.log.append((self.rnd, "event", ev, why))
            self.ev_src[ev] = dict(frm=frm, why=why, conds=self._conds.get(ev, ([], [])), rnd=self.rnd)
            self.changed = True
        for f in FOLLOWS.get(ev, ()):
            self.apply_event(f, m, x, y, veh, "follows %d" % ev, frm)
        key = (ev, m, x, y, veh)
        if key not in self._runs:
            r0 = self.sim.run(St(map=m, x=x, y=y, veh=veh, flags=0), ev, [])
            r1 = self.sim.run(St(map=m, x=x, y=y, veh=veh, flags=ALL), ev, [])
            self._runs[key] = [(a[0], b[0]) for a, b in zip(r0, r1)]
        for a, b in self._runs[key]:
            clears = ~b.flags & ALL
            sets = a.flags & ~clears
            if sets & ~self.S:
                new = sets & ~self.S
                for f in range(1024):
                    if new >> f & 1: self.flag_src[f] = ev
                self.S |= sets; self.changed = True
            if clears & ~self.C:
                self.C |= clears; self.changed = True
            for p in a.parked:
                if p not in self.parked:
                    self.parked.add(p); self.changed = True; self.parked_src[p] = ("event", ev, frm)
            if a.ended:
                if not self.ended:
                    self.ended = True; self.changed = True; self.end_ev = ev
                    self.log.append((self.rnd, "ENDING", ev, why))
                continue
            if (a.map, a.x, a.y, a.veh) != (m, x, y, veh):
                k2 = self.add_ctx(a.map, a.veh if a.map < WORLD_MAPS else 0, a.x, a.y, "event %d" % ev, kind="event", ev=ev, frm=frm)
                if ev in (1734, 1762) and a.map == 156 and k2 not in self.special:
                    self.special.add(k2); self.changed = True
                if a.map >= WORLD_MAPS and a.parent:
                    ps = self.parents.setdefault(a.map, set())
                    if a.parent not in ps:
                        ps.add(a.parent); self.changed = True

    # ---- one round ----
    def step(self):
        for k, (x, y) in list(self.ctx.items()):  # world-map changes made since: the same places under the new layout
            if k[0] < WORLD_MAPS and k[3] != self.mk(k[0]):
                self.add_ctx(k[0], k[1], x, y, "world change", kind="same", frm=k)
        for k in list(self.ctx):
            m, veh, comp, mk = k
            md = self.sim.map_data(m)
            if m < WORLD_MAPS:
                ok = lambda x, y: self.world.touches(m, veh, comp, x, y, mk)
            else:
                ok = lambda x, y: True
            for x, y, t in md["trig"]:
                if ok(x, y):
                    for ev in self.runnable(self.rom.trigger_script(t)):
                        if ev in ONLY_AFTER and k not in self.special: continue
                        self.apply_event(ev, m, x, y, veh, ("trigger", m, x, y), k)
            for n in md["npc"]:
                if self.S >> (NPC + n["script"]) & 1 and ok(n["x"], n["y"]):
                    for ev in self.runnable(self.rom.npc_script(n["script"])):
                        self.apply_event(ev, m, n["x"], n["y"], veh, ("npc", m, n["x"], n["y"], n["script"]), k)
            if not (m < WORLD_MAPS and veh in (2, 3, 6)):
                for e in md["ent"]:
                    if ok(e["x"], e["y"]):
                        dm = e["map"]
                        if dm < WORLD_MAPS:
                            self.add_ctx(dm, veh if m < WORLD_MAPS else 0, e["dx"], e["dy"], "entrance %d (%d,%d)" % (m, e["x"], e["y"]), kind="entrance", frm=k)
                        else:
                            self.add_ctx(dm, 0, e["dx"] & 0x3F, e["dy"] & 0x3F, "entrance %d (%d,%d)" % (m, e["x"], e["y"]), kind="entrance", frm=k)
                            ps = {(m, e["x"], e["y"])} if m < WORLD_MAPS else self.parents.get(m, set())
                            cur = self.parents.setdefault(dm, set())
                            if not ps <= cur:
                                cur |= ps; self.changed = True
            if m >= WORLD_MAPS:
                for (pw, px, py) in list(self.parents.get(m, ())):
                    self.add_ctx(pw, 0, px, py, "exit %d" % m, kind="exit", frm=k)
                    for ev in self.runnable(self.rom.trigger_script(14)):
                        self.apply_event(ev, pw, px, py, 0, ("exit", m), k)
            else:
                if veh == 0:
                    for (v, pm, px, py) in list(self.parked):
                        if pm == m and self.world.touches(m, 0, comp, px, py, mk):
                            self.add_ctx(m, v, px, py, "board %d" % v, kind="board", frm=k, parked=(v, pm, px, py))
                            if v == 5:
                                for ev in self.runnable(self.rom.trigger_script(254)):
                                    self.apply_event(ev, m, px, py, v, ("board ship",), k)
                else:
                    for (lx, ly, px, py) in self.sim.landings(m, veh, comp, mk):
                        self.add_ctx(m, 0, lx, ly, "land %d" % veh, kind="land", frm=k)
                        if (veh, m, px, py) not in self.parked:
                            self.parked.add((veh, m, px, py)); self.changed = True; self.parked_src[(veh, m, px, py)] = ("land", None, k)
                # hard-coded world triggers (field-main.asm FieldLoop): (x1, x2, y1, y2), vehicles, trigger script
                spots = {1: ((0x9f, 0xa2, 0xa1, 0xa2), None, 9), 0: ((0x3d, 0x43, 0x9e, 0xa5), (6,), 16), 2: ((0xb6, 0xba, 0x87, 0x8b), (5, 6), 12)}
                if m in spots:
                    (x1, x2, y1, y2), vs, script = spots[m]
                    if (vs is None or veh in vs) and ok(x1, y1):
                        for ev in self.runnable(self.rom.trigger_script(script)):
                            self.apply_event(ev, m, x1, y1, veh, ("world", m, script), k)

    def run(self, start_event=16, max_rounds=400):
        self.rnd = 0
        for st, lg in self.sim.run(St(flags=self.S), start_event, []):
            self.S |= st.flags
            self.C |= ALL & ~st.flags
            self.add_ctx(st.map, st.veh, st.x, st.y, "start", kind="start")
            self.parked |= set(st.parked)
            if st.parent: self.parents.setdefault(st.map, set()).add(st.parent)
        self.first[start_event] = 0
        while self.rnd < max_rounds:
            self.rnd += 1
            self.changed = False
            self.step()
            if not self.changed:
                break
        return self

if __name__ == "__main__":
    r = Reach(sys.argv[1]).run()
    print("rounds", r.rnd, "contexts", len(r.ctx), "events run", len(r.first), "ending reached", r.ended)
    maps = sorted({k[0] for k in r.ctx})
    print("maps reachable", len(maps), maps)
    print("needed flags never set:", sorted(f for f in r.need if f < 512 and not r.S >> f & 1))
    if len(sys.argv) > 2:
        json.dump([(a, b, str(c), str(d)) for a, b, c, d in r.log], open(sys.argv[2], "w"))
