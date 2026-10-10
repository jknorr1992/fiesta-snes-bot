#!/usr/bin/env python3
"""Turn data/event_path.json (tools/event_route.py) into advisor goals in data/route.json.
Keeps the verified opening goals and the job/level goals; the old guide-derived goals move to "guide_goals" for reference.
Event steps complete when the first story flag (<512) they set is set; entrance steps when the player is in the destination map;
land/board steps are skipped (no RAM condition). Each goal needs the one before it, so racing ahead completes the skipped ones.
Usage: route_to_goals.py"""
import json, os
D = os.path.join(os.path.dirname(__file__), "..", "data")
route = json.load(open(os.path.join(D, "route.json")))
steps = json.load(open(os.path.join(D, "event_path.json")))
names = json.load(open(os.path.join(D, "map_names.json")))
old = route.get("guide_goals") or route["goals"]
base = [g for g in route["goals"] if g["id"] in ("meteor_site", "first_battle", "reach_galuf", "leave_forest", "choose_jobs", "levels_5")]
nm = lambda m: {0: "World 1", 1: "World 2", 2: "World 3"}.get(m) or names.get(str(m)) or "map %d" % m
TRANSIENT = (510, 511)  # flicker during events, not progress markers
goals, prev = list(base), "levels_5"
for i, s in enumerate(steps, 1):
    g = {"id": "ep_%03d" % i, "needs": [prev], "verified": False, "source": "event_path"}
    if s["kind"] == "event":
        w = s["where"]
        place = {"trigger": "trigger", "npc": "NPC", "exit": "leaving", "world": "world trigger"}.get(w[0], w[0])
        loc = "%s (%s,%s)" % (nm(w[1]), w[2], w[3]) if len(w) >= 4 else nm(w[1]) if len(w) > 1 else ""
        g["title"] = "Event %d: %s %s" % (s["event"], place, loc)
        fl = [f for f in s["sets"] if f not in TRANSIENT]
        if fl: g["when"] = {"flag": fl[0]}; g["title"] += " (sets flag %d)" % fl[0]
        if s["battles"]: g["title"] += " [battle %s]" % ",".join(map(str, s["battles"]))
        if w[0] in ("trigger", "npc") and len(w) >= 4: g["at"] = {"map": w[1], "x": w[2], "y": w[3]}
    elif s["kind"] == "entrance":
        g["title"] = "Enter %s (%s)" % (nm(s["to"][0]), s["why"]); g["when"] = {"in_map": s["to"][0]}
    else:
        continue
    goals.append(g); prev = g["id"]
route["goals"] = goals
route["guide_goals"] = old
route["_note"] = "Opening goals verified in play; ep_* goals are generated from the ROM event data (tools/route_to_goals.py, docs/event_path.md). Old guide-derived goals are kept in guide_goals."
json.dump(route, open(os.path.join(D, "route.json"), "w"), indent=1)
print(len(goals), "goals")
