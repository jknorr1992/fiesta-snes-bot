"""Goal-driven bot: reads the live game state from RetroArch, plans toward the given goals with the simulator and acts with
pad inputs only. After every action it compares the game with the plan; any deviation (or a mismatch) triggers a re-plan from the
live state, so the player can wander off and the bot picks up from wherever the party stands.

usage: bot.py [--rom PATH] [--dry] [--fresh] goal [goal ...]        goal = map:N | flag:N | pos:MAP:X:Y | @FILE     (goals are pursued in order;
  @FILE reads goals from a file, e.g. @../data/goals_to_ship.txt = the story flags from the start to the Karnak ship deck scene, flag 56)
  --fresh run the title screen and opening scenes first (a freshly booted game, no save state)
  --dry   plan from the live state and print the macro-actions without pressing anything
ROM: --rom PATH or env FFV_ROM (your FF5r 'patched(1)' ROM; no default path)
env: FFV_HOST (RetroArch host, default 127.0.0.1), N_EXP (search budget per goal, default 20000), plus the usual FFV_STATES / network settings.
Live NPC positions are read from the object table; hidden NPCs and NPC facing are not (the flags decide visibility)."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "--rom" in sys.argv:                      # must be set before the sim modules read FFV_ROM
    i = sys.argv.index("--rom"); os.environ["FFV_ROM"] = sys.argv[i + 1]; del sys.argv[i:i + 2]
from execute import *
from explore import search, chain
from relaxed import Relaxed
from localmap import LocalMap


def live_state(S, ex):
    """simulator State built from RAM: event flags, npc flags, map, position, facing, vehicle table, parent map, and the tile changes
    of the current local map (live BG1 tile ids compared with the ROM layout)."""
    s = State()
    s.flags = int.from_bytes(ex.read(0xA14, 64), "little")
    s.npcf = int.from_bytes(ex.read(0xA54, 128), "little")
    m, x, y = ex.fpos()
    s.map, s.x, s.y = m, x, y
    s.face = ex.u8(0xADB) & 3
    s.veh = ex.u8(0xADC)
    raw = ex.read(0xADD, 24)
    s.vtab = [list(raw[4 * i:4 * i + 4]) for i in range(6)]
    pm = ex.read(0xAF5, 4)
    s.parent = (pm[0] | pm[1] << 8, pm[2], pm[3])
    tb = ex.read(0xAFB, 5)                      # event timer (d1): enabled, counter, event
    if tb[0] == 1 and not S.timer_idle(tb[3] | tb[4] << 8, m): s.timer = (tb[1] | tb[2] << 8, tb[3] | tb[4] << 8)
    if m >= 5:
        base = LocalMap(S.R, m)
        ids = b"".join(ex.read(0x10000 + i, 1024) for i in range(0, 4096, 1024))
        s.mods = {(i % 64, i // 64): ids[i] for i in range(4096) if ids[i] != base.layer[i]}
        s.z = ex.u8(0xBC3) & 3                  # party z-level ($c3): stairs tiles keep the level the party came from
        raw = ex.read(0x1477, 0x14 * 27)         # live object table: NPCs that stand somewhere other than their default tile (moved by a scene or walking about)
        for i, n in enumerate(S.R.npcs(m)):
            if i >= 27: break
            o = raw[0x14 * i:0x14 * i + 0x14]
            if o[8] | o[9] << 8 == n["script"] and (o[1] & 63, o[3] & 63) != (n["x"], n["y"]): s.npcpos[i] = (o[1] & 63, o[3] & 63)
            if o[8] | o[9] << 8 == n["script"] and bool(o[14] & 1) != s.npc_flag(n["script"]): s.npcvis[i] = bool(o[14] & 1)    # shown/hidden by an object action rather than by the npc flag
        S.with_z(s)
    return s


def near_objects(ex, lv, r=10):
    """NPC/object table ($1477, 0x14 bytes each: tile x at +1, tile y at +3) entries within r tiles of the party, for deviation reports"""
    raw = ex.read(0x1477, 0x14 * 27); out = []
    for i in range(27):
        o = raw[0x14 * i:0x14 * i + 0x14]
        if abs(o[1] - lv["x"]) <= r and abs(o[3] - lv["y"]) <= r: out.append("%d: %d,%d s%d" % (i, o[1], o[3], o[8] | o[9] << 8))
    return "; ".join(out)


def fresh_intro(ex, limit=1500):
    """From a freshly booted game: A through the title and opening scenes, keep the default name on the name screen, until the player
    has control on the world map with the opening scenes finished (position steady and no message for several polls)."""
    quiet = 0
    for _ in range(limit):
        m = ex.u16(0xAD4) & 0xFFF
        if m == 13: ex.name_screen(); quiet = 0; continue
        if m == 0 and not ex.busy():
            quiet += 1
            if quiet >= 6: return True
            ex.wait(20); continue
        quiet = 0
        ex.press("A", 3, 25)
    return False


def fast_config(ex):
    """Config menu: Bat.Speed and Msg.Speed to 1 (fastest), the rest as it is (user's request). Menu: X, Config is the fifth row, B leaves it.
    Config byte WRAM $970: bits 4-6 message speed, bit 3 battle mode (1 = Wait), bits 0-2 battle speed."""
    if ex.u8(0x970) & 0x77 == 0: return True
    for _ in range(3):
        ex.press("X", 4, 10); ex.wait(40)
        for _ in range(4): ex.press("DOWN", 4, 10); ex.wait(10)
        ex.press("A", 4, 10); ex.wait(40)
        ex.press("DOWN", 4, 10); ex.wait(10)                      # Bat.Speed (the cursor starts on Bat.Mode)
        for _ in range(5): ex.press("LEFT", 4, 10); ex.wait(8)
        ex.press("DOWN", 4, 10); ex.wait(10)                      # Msg.Speed
        for _ in range(5): ex.press("LEFT", 4, 10); ex.wait(8)
        for _ in range(3): ex.press("B", 4, 10); ex.wait(30)
        if ex.u8(0x970) & 0x77 == 0: return True
    return False


def pending_trigger(S, s):
    """event id of an event trigger that stands unfired under the party at its current tile (conditions hold), else 0"""
    if s.map < 5: return 0
    ts = S.trig_at(s.map).get((s.x, s.y))
    if ts is None: return 0
    return S.pick(s, S.R.trigger_script(ts), s.face, False) or 0


def nudge(S, ex, s):
    """Standing on a trigger tile that never fired (arrival through an entrance): press a direction that cannot move the party (a wall
    if there is one) so the field loop runs the trigger, then let the scene play."""
    lm = S.localmap(s)
    open_dirs = {d for d, _ in lm.moves(s.x, s.y, s.z)}
    d = next((d for d in (1, 2, 3, 4) if d not in open_dirs), 1)
    print("   trigger under the party did not fire on arrival: pressing %s to start it" % BTN[d], flush=True)
    ex.press(BTN[d], 4, 10); ex.wait(30); ex.settle()


def goal_fn(spec):
    if spec == "end": return (lambda s: s.ended), "end"          # the ending (event command $BF $0F) has run
    if spec.startswith("open:"): return (lambda s: True), "open"  # open:UP|RIGHT|DOWN|LEFT = face that way and press A (a chest or an NPC next to the party): live only
    k, *v = spec.split(":"); v = [int(a) for a in v]
    if k == "map": return (lambda s: s.map == v[0]), "map"
    if k == "flag": return (lambda s: s.flag(v[0])), "flag"
    if k == "pos": return (lambda s: (s.map, s.x, s.y) == tuple(v)), "pos"
    raise SystemExit("unknown goal " + spec)


def heuristic(Rl, spec):
    if spec == "end" or spec.startswith("open:"): return None
    k, *v = spec.split(":"); v = [int(a) for a in v]
    if k == "map": return lambda s: Rl.levels(s.flags, s.map, s.npcf, (s.x, s.y))[Rl.oM + v[0]]
    if k == "flag": return lambda s: Rl.h_flag(v[0], s.flags, s.map, s.npcf, (s.x, s.y))
    if k == "pos": return lambda s: Rl.h_pos(v[0], v[1], v[2], s.flags, s.map, s.npcf, (s.x, s.y))
    return None


def main(argv):
    dry = "--dry" in argv; fresh = "--fresh" in argv
    goals = []
    for a in argv:                              # @file = goals listed in a file (whitespace separated)
        if a.startswith("@"): goals += [g for l in open(a[1:]) for g in l.split("#")[0].split()]
        elif not a.startswith("--"): goals.append(a)
    if not goals: raise SystemExit(__doc__)
    S = Sim(); Rl = Relaxed(S); ex = Exec(S)
    if fresh:
        print("fresh game: running the title and opening scenes", flush=True)
        if not fresh_intro(ex): print("intro did not reach the world map", flush=True); return 1
        print("config: battle and message speed fastest: %s (byte $970 = %#x)" % (fast_config(ex), ex.u8(0x970)), flush=True)
    n_exp = int(os.environ.get("N_EXP", "20000"))
    for spec in goals:
        if spec.startswith("open:"):
            print("open: face %s and press A" % spec[5:], flush=True)
            if not dry:
                ex.press(spec[5:], 4, 10); ex.wait(12); ex.press("A", 4, 10); ex.wait(60)
                for _ in range(8): ex.press("A", 4, 10); ex.wait(40)             # the item message
                ex.settle()
            continue
        goal, _ = goal_fn(spec)
        h = heuristic(Rl, spec)
        replans = 0
        while True:
            s = live_state(S, ex)
            for _ in range(3):
                ev = pending_trigger(S, s)
                if not ev or ex.u8(0xB57): break
                ex.wait(60)
                s2 = live_state(S, ex)
                if (s2.map, s2.x, s2.y, s2.flags) != (s.map, s.x, s.y, s.flags): s = s2; continue
                nudge(S, ex, s); s = live_state(S, ex)
            if goal(s): print("goal %s reached" % spec, flush=True); break
            f, k = search(S, s, goal, max_exp=n_exp, wh=1.0 if h else 0.0, h=h, log=2000)
            if not f:
                print("no plan to %s from map %d (%d,%d) in %d expansions" % (spec, s.map, s.x, s.y, k), flush=True); return 1
            plan = chain(f)
            print("plan to %s: %d actions (replan %d)" % (spec, len(plan), replans), flush=True)
            for i, (p, a, n) in enumerate(plan): print("  [%d] %s" % (i, a.desc), flush=True)
            if dry: return 0
            ok = True
            for i, (p, a, n) in enumerate(plan):
                print("-> %s" % a.desc, flush=True)
                ex.quick = i + 1 < len(plan) and plan[i + 1][1].frames is not None      # a timed stretch follows: no scene-settling that would eat into the timer
                ex.do(p, a)
                lv = ex.live()
                outs = S.execute(p, a)
                print("   live now: map %d (%d,%d), plan expects %s" % (lv["map"], lv["x"], lv["y"], sorted({(o.map, o.x, o.y) for o in outs})), flush=True)
                for _ in range(10):                     # a scene or map transition may still be running: let it finish before judging
                    if any((o.map, o.x, o.y) == (lv["map"], lv["x"], lv["y"]) and not (lv["flags"] ^ o.flags) & S.read_mask & ~(1 << 511) for o in outs): break
                    ex.settle(); ex.wait(60); lv = ex.live()
                match = [o for o in outs if (o.map, o.x, o.y) == (lv["map"], lv["x"], lv["y"])]
                diffs = compare(S, lv, match[0] if match else n)
                if diffs:
                    print("   deviation: %s -> re-planning from the live state" % "; ".join(diffs), flush=True)
                    print("   live objects near the party (index: x,y script): %s" % near_objects(ex, lv), flush=True)
                    ok = False; break
            replans += 0 if ok else 1
            if replans > 20: print("too many re-plans", flush=True); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
