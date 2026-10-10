#!/usr/bin/env python3
"""Extract a dependency-ordered event path from the monotone reachability (event_reach.py): everything that had to happen, in
an order where each step's prerequisites (flags, places, parked vehicles) come first, ending with the epilogue event.
Usage: event_route.py <rom.sfc> [out.json]"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_reach import Reach, NPC

def route(r, target_events):
    steps, done_ctx, done_ev, done_p = [], set(), set(), set()
    def need_flag(f):
        ev = r.flag_src.get(f)
        if ev is not None: need_event(ev)
    def need_parked(p):
        if p in done_p: return
        done_p.add(p)
        src = r.parked_src.get(p)
        if not src: return
        kind, ev, frm = src
        if frm: need_ctx(frm)
        if ev: need_event(ev)
    def need_ctx(k):
        if k in done_ctx: return
        done_ctx.add(k)
        src = r.ctx_src.get(k)
        if not src or src["kind"] == "start": return
        if src.get("frm"): need_ctx(src["frm"])
        if src["kind"] == "event": need_event(src["ev"])
        if src["kind"] == "board": need_parked(src["parked"])
        if src["kind"] in ("entrance", "exit", "board", "land"):
            steps.append({"kind": src["kind"], "to": list(k[:3]), "why": src["why"], "round": src["rnd"]})
    def need_event(ev):
        if ev in done_ev: return
        done_ev.add(ev)
        es = r.ev_src.get(ev)
        if es is None: return
        if es["frm"]: need_ctx(es["frm"])
        conds, falls = es["conds"]
        for c in conds:
            if c[0] == "set": need_flag(c[1])
        for f in falls: need_flag(f)
        if es["why"][0] == "npc": need_flag(NPC + es["why"][4])
        x = r.eff.get(ev, {})
        steps.append({"kind": "event", "event": ev, "where": list(es["why"]), "needs_set": [c[1] for c in conds if c[0] == "set"],
                      "sets": sorted(f for f in x.get("set", ()) if f < 512), "battles": sorted(x.get("battles", ())),
                      "round": es["rnd"]})
    for e in target_events: need_event(e)
    return steps

if __name__ == "__main__":
    r = Reach(sys.argv[1]).run()
    ends = [r.end_ev]
    steps = route(r, ends)
    print("ending events run:", ends, "steps:", len(steps), "of which events:", sum(1 for s in steps if s["kind"] == "event"))
    if len(sys.argv) > 2:
        json.dump(steps, open(sys.argv[2], "w"), indent=0)
    for s in steps:
        print(s["kind"], s.get("event", s.get("to")), s.get("where", s.get("why")), s.get("sets", ""), s.get("battles", ""))
