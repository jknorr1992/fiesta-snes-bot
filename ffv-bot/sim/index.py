"""Reverse index: which triggers / NPCs / entrances / other events run each event."""
import functools
from events import EventRom
from rom import Rom

class Index:
    def __init__(self, rom=None):
        self.R = rom or Rom()
        self.E = EventRom(self.R.r)
        self.src = {}     # event -> list of (kind, map, x, y, conds, extra)
        self.nmaps = 511
        for m in range(self.nmaps):
            try:
                for x, y, ts in self.R.event_triggers(m):
                    for conds, ev in self.R.trigger_script(ts):
                        self.src.setdefault(ev, []).append(("trigger", m, x, y, conds, ts))
                for n in self.R.npcs(m):
                    for conds, ev in self.R.npc_script(n["script"]):
                        self.src.setdefault(ev, []).append(("npc", m, n["x"], n["y"], conds, n["script"]))
            except Exception as e:
                pass
        for ev in range(self.E.N):
            for c in self.E.cmds(ev):
                if c.op == 0xCD:
                    self.src.setdefault(c.b[1] | c.b[2] << 8, []).append(("call", ev, 0, 0, (), None))
        # special trigger scripts not tied to a map position (world-map hooks etc.)
        for ts in range(704):
            for conds, ev in self.R.trigger_script(ts):
                self.src.setdefault(ev, []).append(("script", -1, 0, 0, conds, ts))
