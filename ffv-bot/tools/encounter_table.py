#!/usr/bin/env python3
"""Every battle (random encounters, scripted/boss battles) in the regions the route has reached, grouped by region.

usage: encounter_table.py [--rom PATH] [--reached data/reached_regions.json] [--out-json data/encounters_by_region.json] [--out-md docs/encounters_by_region.md]
ROM: --rom PATH or env FFV_ROM.

Reached regions come from data/reached_regions.json (made from the simulated route: the maps the party stood on and the world-map
tiles it walked/sailed over). Where the battles come from (everything8215/ff5 field-main.asm, checked against the ROM):
  local map   random battle group = u16 at 0xD08000 + 2*map; rate = 2 bits at 0xD084C0 (0 normal, 1 low, 2 high, 3 very high); battles only if
              bit 7 of map property byte 6 (or 7) is set. Pick by a 0-255 roll: <90 first, <180 second, <240 third, else fourth of the group.
  world map   group = u16 at 0xD07A00 + 2*(256*world + 32*(y>>5) + 4*(x>>5) + (terrain & 3)), terrain = tile property byte 2 & 15; rate = 2 bits at
              0xD08400 + 64*world + 8*(y>>5) + (x>>5), shifted by 2*[3,2,1,0,3,2,1,0][terrain]; rate 3 = no battles.
  event battle  commands $BD / $E2 with group = byte1 & 0x7F; 0xD07800 + 4*group holds two battle ids (first below a 0xC0 roll = 75 %, second 25 %).
Monster names: letters, digits and the digraphs of the English table; this ROM's other digraph bytes show as {xx} (e.g. Kil{a8}r = Killer), the raw name bytes are in the monsters table; monster ids are exact."""
import sys, os, json, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "battle")); sys.path.insert(0, os.path.join(ROOT, "sim"))
from data import GameData

DEFAULT_ROM = "/mnt/project-files/FFV/Final Fantasy 5 (Japan) (patched)(2).sfc"
MAP_PROP = 0xE9C00                       # 512 x 26 bytes; byte 6/7 bit 7 = random battles on, byte 24 = default battle background
WORLD_RATE = 0x108400
WORLD_PROP = 0xFEA00                     # + 576 * tileset + 3 * tile
TILESET = [0, 1, 0, 2, 2]
RATE_NAMES = ["normal", "low", "high", "very high"]
WORLD_RATE_NAMES = ["normal", "low", "high", "none"]
SHIFT = [3, 2, 1, 0, 3, 2, 1, 0]
BG_W1 = [0, 1, 2, 3, 4, 5, 3, 7, 8, 0, 0, 0, 0, 0, 0, 0]; BG_W2 = [33, 1, 2, 3, 4, 5, 3, 7, 8]
WORLD_NAMES = ["World 1", "World 2", "World 3"]
VEH_NAMES = {0: "on foot", 1: "chocobo", 2: "black chocobo", 3: "hiryuu", 4: "submarine", 5: "ship", 6: "airship"}
PROBS = (90 / 256, 90 / 256, 60 / 256, 16 / 256)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=os.environ.get("FFV_ROM", DEFAULT_ROM))
    ap.add_argument("--reached", default=os.path.join(ROOT, "data", "reached_regions.json"))
    ap.add_argument("--out-json", default=os.path.join(ROOT, "data", "encounters_by_region.json"))
    ap.add_argument("--out-md", default=os.path.join(ROOT, "docs", "encounters_by_region.md"))
    a = ap.parse_args()
    rom = open(a.rom, "rb").read(); d = GameData(rom)
    reached = json.load(open(a.reached))
    names = json.load(open(os.path.join(ROOT, "data", "map_names.json")))
    areas = json.load(open(os.path.join(ROOT, "data", "map_areas.json")))
    from rom import Rom
    from events import EventRom
    R = Rom(a.rom); E = EventRom(R.r)

    tt = json.load(open(os.path.join(ROOT, "data", "text_en.json")))
    TEXT = {int(k, 16): (v[0] if isinstance(v, list) else v) for k, v in tt["text"].items()}
    TEXT.update({int(k, 16): v for k, v in tt["dte"].items() if len(k) == 4})      # single-byte digraphs
    TEXT.update({0xFF: "", 0x96: " "})
    from data import MONSTER_NAMES
    def mon_name(i):
        """monster name from the ROM bytes: letters/digits/digraphs from the English table, unknown tokens (this ROM's other digraphs) as {xx}"""
        raw = rom[MONSTER_NAMES + 10 * i:MONSTER_NAMES + 10 * i + 10]
        return "".join(TEXT.get(b, "{%02x}" % b if b else "") for b in raw).strip(), raw.hex()

    mon_seen = {}
    def battle_rec(bid):
        b = d.battle(bid)
        for m in b["monster_ids"]:
            if m is not None and m not in mon_seen:
                mon_seen[m] = d.monster(m); mon_seen[m]["name"], mon_seen[m]["name_bytes"] = mon_name(m)
        groups = {}
        for m in b["monster_ids"]:
            if m is not None: groups.setdefault(m, [mon_name(m)[0], 0])[1] += 1
        return dict(battle=bid, boss=b["boss"], no_run=b["no_run"], monsters=[dict(id=m, name=v[0], count=v[1]) for m, v in groups.items()],
                    flags=b["flags"], intro=b["intro"], flee=b["flee"], ap=b["ap"], music=b["music"], raw=b["raw"])

    SUB = ("Shop", "Inn", "Tavern", "Jail", "Storage", "Throne Room", "King's Room", "Queen's Room", "Lenna's Room", "Rooftop", "Grand Hall", "Sealed Room", "House")
    def label(m):
        """region heading: the area title of the map; floors ("1F", "B0") and untitled maps join the nearest earlier map that carries a place
        title (map ids are numbered place by place; shops/jails/rooms do not count as places, so they join their town or castle; maps\n        with no earlier title within 25 ids are listed under "(untitled maps)")"""
        for k in range(m, max(m - 25, 10), -1):
            t = areas.get(str(k))
            if t and not any(s in t for s in SUB): return t
        return "(untitled maps)"

    # ---- scripted battles per map: events started by this map's triggers and NPCs (following event calls) ----
    def battles_in(ev, seen=None, via=()):
        seen = set() if seen is None else seen
        if ev in seen or ev >= E.N: return []
        seen.add(ev); out = []
        for c in E.cmds(ev):
            if c.op in (0xBD, 0xE2): out.append((c.b[1] & 0x7F, c.b[2], ev, c.op == 0xE2))
            elif c.op == 0xCD:
                out += battles_in(c.b[1] | c.b[2] << 8, seen, via + (ev,))
        return out

    route_events = set(reached.get("route_events", []))
    route_battle_events = set()
    for ev in route_events: route_battle_events |= {t[2] for t in battles_in(ev)}

    def event_battles(m):
        found = {}
        try:
            srcs = []
            for x, y, ts in R.event_triggers(m):
                for conds, ev in R.trigger_script(ts): srcs.append(("trigger", (x, y), ev, conds))
            for n in R.npcs(m):
                for conds, ev in R.npc_script(n["script"]): srcs.append(("npc", (n["x"], n["y"]), ev, conds))
        except Exception:
            return []
        for kind, pos, ev, conds in srcs:
            for grp, bg, where, can_lose in battles_in(ev):
                key = (grp, where)
                rec = found.setdefault(key, dict(event_group=grp, started_by_event=where, can_lose=can_lose, background=bg if bg != 0xFF else "map default",
                                                 sources=[], on_route=where in route_battle_events))
                rec["sources"].append(dict(kind=kind, x=pos[0], y=pos[1], event=ev, conditions=[list(c) for c in conds]))
        out = []
        for rec in found.values():
            ids = d.event_battle(rec["event_group"])
            rec["battles"] = [dict(chance=0.75, **battle_rec(ids[0])), dict(chance=0.25, **battle_rec(ids[1]))] if ids[1] != ids[0] else [dict(chance=1.0, **battle_rec(ids[0]))]
            out.append(rec)
        return sorted(out, key=lambda r: (r["started_by_event"], r["event_group"]))

    # ---- local maps ----
    regions = {}
    for ms, route_steps in sorted(reached["local_maps"].items(), key=lambda kv: int(kv[0])):
        m = int(ms)
        prop = rom[MAP_PROP + 26 * m:MAP_PROP + 26 * m + 26]
        enabled = bool(prop[6] & 0x80 or prop[7] & 0x80)
        me = d.map_encounters(m)
        rec = dict(map=m, name=names.get(ms, ""), reached_at_route_steps=[route_steps[0], route_steps[-1]], random_battles_enabled=enabled,
                   default_background=prop[24], event_battles=event_battles(m))
        if enabled:
            rec.update(random_rate=RATE_NAMES[me["rate"]], random_group=me["group"],
                       random_battles=[dict(chance=round(p, 4), **battle_rec(b)) for b, p in me["battles"]])
        regions.setdefault(label(m) or "(unnamed)", {"kind": "local", "maps": []})["maps"].append(rec)

    # ---- world map blocks ----
    wr = {}
    for w, x, y, veh, step in reached["world_tiles"]:
        ts = TILESET[w]; rows_t = None
        wr.setdefault((w, x >> 5, y >> 5), []).append((x, y, veh, step))
    # tile ids come from the simulator's world model (event world changes follow the flags at that route step in the chain; the base map is used here)
    from world_model import World
    W = World(rom)
    for (w, bx, by), tiles in sorted(wr.items()):
        terr = {}
        for x, y, veh, step in tiles:
            t = W.grid[w][y][x]
            terrain = rom[WORLD_PROP + 576 * TILESET[w] + 3 * t + 2] & 15 if t < 192 else 0
            terr.setdefault(terrain, dict(tiles=set(), vehicles=set(), steps=set()))
            terr[terrain]["tiles"].add((x, y)); terr[terrain]["vehicles"].add(veh); terr[terrain]["steps"].add(step)
        sub = []
        rate_byte = rom[WORLD_RATE + 64 * w + 8 * by + bx]
        for terrain, info in sorted(terr.items()):
            gi = 256 * w + 32 * by + 4 * bx + (terrain & 3)
            rate = (rate_byte >> (2 * SHIFT[terrain & 7])) & 3
            ge = d.world_encounters(gi)
            bg = (BG_W1 + BG_W2)[((w & 1) << 4) | terrain] if terrain < 16 else 0
            r = dict(terrain=terrain, group_index=gi, random_group=ge["group"], rate=WORLD_RATE_NAMES[rate], tiles_visited=len(info["tiles"]),
                     vehicles=[VEH_NAMES[v] for v in sorted(info["vehicles"])], reached_at_route_steps=[min(info["steps"]), max(info["steps"])],
                     background="0x15 (ship)" if info["vehicles"] == {5} else bg, example_tile=sorted(info["tiles"])[0])
            if rate != 3: r["random_battles"] = [dict(chance=round(p, 4), **battle_rec(b)) for b, p in ge["battles"]]
            sub.append(r)
        regions.setdefault("%s: tiles x %d-%d, y %d-%d" % (WORLD_NAMES[w], bx * 32, bx * 32 + 31, by * 32, by * 32 + 31), {"kind": "world", "world": w, "terrains": sub})

    out = dict(rom=os.path.basename(a.rom), source="route in data/reached_regions.json; tables per tools/encounter_table.py docstring",
               regions=regions, monsters={str(k): v for k, v in sorted(mon_seen.items())})
    os.makedirs(os.path.dirname(a.out_json), exist_ok=True)
    json.dump(out, open(a.out_json, "w"), indent=1)
    write_md(out, a.out_md)
    nb = len({r_["battle"] for reg in regions.values() for r_ in iter_battles(reg)})
    print("%d regions, %d distinct battles, %d monsters -> %s, %s" % (len(regions), nb, len(mon_seen), a.out_json, a.out_md))

def iter_battles(reg):
    for m in reg.get("maps", []):
        for b in m.get("random_battles", []): yield b
        for e in m["event_battles"]:
            for b in e["battles"]: yield b
    for t in reg.get("terrains", []):
        for b in t.get("random_battles", []): yield b

def fmt_mons(b):
    return ", ".join("%s%s [%d]" % (m["name"], " x%d" % m["count"] if m["count"] > 1 else "", m["id"]) for m in b["monsters"])

def write_md(out, path):
    L = ["# Battles in the regions reached so far", "",
         "Generated by `tools/encounter_table.py` from the ROM and `data/reached_regions.json` (the simulated route up to Crescent). Full details (monster stats, raw battle records, event conditions) are in `data/encounters_by_region.json`.",
         "Coverage: the route's first state is the world map at (157,150) after the opening scenes, so anything before that is not listed; the route ends at Crescent. The listed battles are everything the ROM can start in those maps and world-map blocks, not only the ones the party actually fought. Scripted battles marked `on the simulated route` are started by events the route triggers. Regions are headed by the map's place title (floors and shops join their dungeon or town); each map keeps its own number.",
         "Battle ids are indices into the battle property table (0xD03000). Monster ids in brackets. `{xx}` in a name is a digraph byte this ROM uses that is not in the English table (the raw bytes are in the JSON).", ""]
    for name, reg in out["regions"].items():
        L += ["## " + name, ""]
        if reg["kind"] == "local":
            for m in reg["maps"]:
                head = "### Map %d%s" % (m["map"], (" (%s)" % m["name"]) if m["name"] else "")
                L += [head, ""]
                if m["random_battles_enabled"]:
                    L += ["Random battles: %s rate, group %d, background %d" % (m["random_rate"], m["random_group"], m["default_background"]), ""]
                    for b in m["random_battles"]: L.append("- %4.1f%%  battle %d: %s" % (b["chance"] * 100, b["battle"], fmt_mons(b)))
                else:
                    L.append("Random battles: none")
                L.append("")
                if m["event_battles"]:
                    L += ["Scripted battles:", ""]
                    for e in m["event_battles"]:
                        src = e["sources"][0]
                        L.append("- event %d, group %d, %s at (%d,%d)%s%s:" % (e["started_by_event"], e["event_group"], src["kind"], src["x"], src["y"],
                                 ", on the simulated route" if e["on_route"] else "", ", can be lost" if e["can_lose"] else ""))
                        for b in e["battles"]:
                            L.append("  - %3.0f%%  battle %d%s: %s" % (b["chance"] * 100, b["battle"], " [boss]" if b["boss"] else "", fmt_mons(b)))
                    L.append("")
        else:
            for t in reg["terrains"]:
                L.append("**Terrain %d** (%s; %d tiles visited; %s), rate %s, background %s" % (t["terrain"], ", ".join(t["vehicles"]), t["tiles_visited"],
                         "example tile (%d,%d)" % tuple(t["example_tile"]), t["rate"], t["background"]))
                for b in t.get("random_battles", []): L.append("- %4.1f%%  battle %d: %s" % (b["chance"] * 100, b["battle"], fmt_mons(b)))
                L.append("")
    open(path, "w").write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
