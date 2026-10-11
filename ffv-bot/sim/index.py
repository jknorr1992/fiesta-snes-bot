"""Reverse index: which triggers / NPCs / entrances / other events run each event."""
import functools
from events import EventRom
from rom import Rom

def implied(groups):
    """condition groups (first match wins) -> (conds + what the failed earlier groups imply, event): an earlier group that is a single
    flag test fails only when that flag has the opposite value (an earlier unconditional group makes the later ones unreachable)"""
    out = []; neg = []
    for conds, ev in groups:
        if any(c[0] == "never" for c in neg): break
        out.append((tuple(conds) + tuple(neg), ev))
        if not conds: neg.append(("never",))
        elif len(conds) == 1 and conds[0][0] == "flag": neg.append(("flag", conds[0][1], not conds[0][2]))
    return [(tuple(c for c in cs if c[0] != "never"), ev) for cs, ev in out]

class Index:
    def __init__(self, rom=None):
        self.R = rom or Rom()
        self.E = EventRom(self.R.r)
        self.src = {}     # event -> list of (kind, map, x, y, conds, extra)
        self.nmaps = 511
        for m in range(self.nmaps):
            try:
                for x, y, ts in self.R.event_triggers(m):
                    for conds, ev in implied(self.R.trigger_script(ts)):
                        self.src.setdefault(ev, []).append(("trigger", m, x, y, conds, ts))
                for n in self.R.npcs(m):
                    for conds, ev in implied(self.R.npc_script(n["script"])):
                        self.src.setdefault(ev, []).append(("npc", m, n["x"], n["y"], conds, n["script"]))
            except Exception as e:
                pass
        for ev in range(self.E.N):
            for c in self.E.cmds(ev):
                if c.op == 0xCD:
                    self.src.setdefault(c.b[1] | c.b[2] << 8, []).append(("call", ev, 0, 0, (), None))
        # special trigger scripts not tied to a map position (world-map hooks etc.)
        for ts in range(704):
            for conds, ev in implied(self.R.trigger_script(ts)):
                self.src.setdefault(ev, []).append(("script", -1, 0, 0, conds, ts))
