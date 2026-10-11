"""Static analysis of event scripts: effects per event (including nested calls) and the cone of influence of a goal."""
import functools, sys
sys.path.insert(0, '.')
from events import EventRom
from rom import Rom
from index import Index

class Effects:
    __slots__ = ("sets", "clears", "npc_on", "npc_off", "loads", "veh", "mods", "steps", "calls", "ends", "gameover", "battle")
    def __init__(self):
        self.sets, self.clears, self.npc_on, self.npc_off = set(), set(), set(), set()
        self.loads = []; self.veh = False; self.mods = False; self.steps = False; self.calls = set(); self.ends = False; self.gameover = False
        self.battle = False

class Analysis:
    def __init__(self, idx=None):
        self.I = idx or Index()
        self.E = self.I.E; self.R = self.I.R
        self._direct = {}
        self.eff = {}
        for e in range(self.E.N): self._direct[e] = self._scan(e)
        for e in range(self.E.N): self.eff[e] = self._closure(e)

    def _scan(self, e):
        f = Effects()
        pending = None                  # a map load followed by another load with no branch or end in between is only a scene location
        for c in self.E.cmds(e):
            op, b = c.op, c.b
            if op in (0xF0, 0xF1, 0xFF, 0xCD, 0xE7) and pending is not None: f.loads.append(pending); pending = None
            if op == 0xA2: f.sets.add(b[1])
            elif op == 0xA4: f.sets.add(256 + b[1])
            elif op == 0xA3: f.clears.add(b[1])
            elif op == 0xA5: f.clears.add(256 + b[1])
            elif op == 0xCA: f.npc_on.add(b[1] | b[2] << 8)
            elif op == 0xCB: f.npc_off.add(b[1] | b[2] << 8)
            elif op in (0xE0, 0xE1, 0xE3) or op == 0xD6:
                m = b[1] | (b[2] & 3) << 8 if op != 0xD6 else (b[1] | b[2] << 8) & 0x1FFF
                pending = m                  # replaces an earlier load of the same straight-line stretch
            elif op in (0xD2, 0xC2): f.veh = True
            elif op in (0xF3, 0xF4): f.mods = True
            elif 1 <= op <= 6: f.steps = True
            elif op == 0xCD: f.calls.add(b[1] | b[2] << 8)
            elif op == 0xBF and b[1] == 0x0F: f.ends = True
            elif op == 0xBF and b[1] == 0x00: f.gameover = True
            elif op in (0xBD, 0xE2): f.battle = True
            elif op == 0xBF and b[1] == 1: f.loads.append(-1)
        if pending is not None: f.loads.append(pending)
        return f

    def _closure(self, e):
        out = Effects(); seen = set(); st = [e]
        while st:
            x = st.pop()
            if x in seen or x >= self.E.N: continue
            seen.add(x); d = self._direct[x]
            out.sets |= d.sets; out.clears |= d.clears; out.npc_on |= d.npc_on; out.npc_off |= d.npc_off
            out.loads += d.loads; out.veh |= d.veh; out.mods |= d.mods; out.steps |= d.steps; out.ends |= d.ends
            out.gameover |= d.gameover; out.battle |= d.battle
            st += list(d.calls)
        return out

    def goal_events(self):
        return [e for e in range(self.E.N) if self.eff[e].ends]

    def read_flags(self):
        r = set()
        for lst in self.I.src.values():
            for s in lst:
                for c in s[4]:
                    if c[0] == "flag": r.add(c[1])
        return r

    def cone(self, goals):
        """flags / events / maps that can influence reaching `goals`: backward closure over (a) writers of needed flags,
        (b) conditions of the sources of needed events, (c) events and entrances that lead into needed maps."""
        writers = {}
        for e in range(self.E.N):
            f = self.eff[e]
            for n in f.sets | f.clears: writers.setdefault(n, set()).add(e)
        loaders = {}
        for e in range(self.E.N):
            for m in self.eff[e].loads: loaders.setdefault(m, set()).add(e)
        enter = {}                                    # dest map -> source maps (entrance triggers)
        for m in range(511):
            for en in self.R.entrances(m): enter.setdefault(en["map"], set()).add(m)
        callers = {}
        for e in range(self.E.N):
            for c in self._direct[e].calls: callers.setdefault(c, set()).add(e)
        nf, ne, nm = set(), set(), set()
        work_e = list(goals); work_m = []
        def need_map(m):
            if m not in nm: nm.add(m); work_m.append(m)
        while work_e or work_m:
            while work_e:
                e = work_e.pop()
                if e in ne: continue
                ne.add(e)
                work_e += list(callers.get(e, ()))
                for s in self.I.src.get(e, ()):
                    if s[0] in ("trigger", "npc"): need_map(s[1])
                    if s[0] == "call": continue
                    for c in s[4]:
                        if c[0] == "flag" and c[1] not in nf:
                            nf.add(c[1]); work_e += list(writers.get(c[1], ()))
            while work_m:
                m = work_m.pop()
                if m < 5: continue
                work_e += list(loaders.get(m, ()))
                for sm in enter.get(m, ()): need_map(sm)
        return nf, ne, nm

if __name__ == "__main__":
    A = Analysis()
    g = A.goal_events(); print("goal events", g)
    fl, ev, mp = A.cone([1924])
    print("cone flags", len(fl), "events", len(ev), "maps", len(mp), "read flags", len(A.read_flags()))
