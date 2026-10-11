"""Goal-driven bot: reads the live game state from RetroArch, plans toward the given goals with the simulator and acts with
pad inputs only. After every action it compares the game with the plan; any deviation (or a mismatch) triggers a re-plan from the
live state, so the player can wander off and the bot picks up from wherever the party stands.

usage: bot.py [--rom PATH] [--dry] [--fresh] goal [goal ...]        goal = map:N | flag:N | pos:MAP:X:Y     (goals are pursued in order)
  --fresh run the title screen and opening scenes first (a freshly booted game, no save state)
  --dry   plan from the live state and print the macro-actions without pressing anything
ROM: --rom PATH or env FFV_ROM (your FF5r 'patched(1)' ROM; no default path)
env: FFV_HOST (RetroArch host, default 127.0.0.1), N_EXP (search budget per goal, default 20000), plus the usual FFV_STATES / network settings.
Not in the live state (defaults are assumed): moved/hidden NPC positions, so a plan can be off in scenes that move NPCs."""
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
    if m >= 5:
        base = LocalMap(S.R, m)
        ids = b"".join(ex.read(0x10000 + i, 1024) for i in range(0, 4096, 1024))
        s.mods = {(i % 64, i // 64): ids[i] for i in range(4096) if ids[i] != base.layer[i]}
        S.with_z(s)
    return s


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


def goal_fn(spec):
    k, *v = spec.split(":"); v = [int(a) for a in v]
    if k == "map": return (lambda s: s.map == v[0]), "map"
    if k == "flag": return (lambda s: s.flag(v[0])), "flag"
    if k == "pos": return (lambda s: (s.map, s.x, s.y) == tuple(v)), "pos"
    raise SystemExit("unknown goal " + spec)


def heuristic(Rl, spec):
    k, *v = spec.split(":"); v = [int(a) for a in v]
    if k == "map": return lambda s: Rl.levels(s.flags, s.map, s.npcf)[Rl.oM + v[0]]
    if k == "flag": return lambda s: Rl.h_flag(v[0], s.flags, s.map, s.npcf)
    return None


def main(argv):
    dry = "--dry" in argv; fresh = "--fresh" in argv
    goals = [a for a in argv if not a.startswith("--")]
    if not goals: raise SystemExit(__doc__)
    S = Sim(); Rl = Relaxed(S); ex = Exec(S)
    if fresh:
        print("fresh game: running the title and opening scenes", flush=True)
        if not fresh_intro(ex): print("intro did not reach the world map", flush=True); return 1
    n_exp = int(os.environ.get("N_EXP", "20000"))
    for spec in goals:
        goal, _ = goal_fn(spec)
        h = heuristic(Rl, spec)
        replans = 0
        while True:
            s = live_state(S, ex)
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
                ex.do(p, a)
                lv = ex.live()
                outs = S.execute(p, a)
                for _ in range(10):                     # a scene or map transition may still be running: let it finish before judging
                    if any((o.map, o.x, o.y) == (lv["map"], lv["x"], lv["y"]) for o in outs): break
                    ex.settle(); ex.wait(60); lv = ex.live()
                match = [o for o in outs if (o.map, o.x, o.y) == (lv["map"], lv["x"], lv["y"])]
                diffs = compare(S, lv, match[0] if match else n)
                if diffs:
                    print("   deviation: %s -> re-planning from the live state" % "; ".join(diffs), flush=True)
                    ok = False; break
            replans += 0 if ok else 1
            if replans > 20: print("too many re-plans", flush=True); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
