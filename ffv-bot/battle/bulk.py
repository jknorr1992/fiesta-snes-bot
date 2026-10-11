#!/usr/bin/env python3
"""Run many battles in parallel to gather statistics (win rate, duration, damage taken, drops...).

    bulk.py 0x5d --runs 512 --jobs 4                    # encounter 0x5d with a fresh 4-member party, RNG seeds 0..255 cycled
    bulk.py 0x5d --spec party.json --background 0x12    # party / gear / items from a checked JSON description (party.py)
    bulk.py 0x5d --state s.state4                       # ...or from a RetroArch save state

    from bulk import Job, run_many, summarize
    jobs = [Job(encounter=0x5d, background=0x12, seed=s % 256) for s in range(1000)]
    results = run_many(jobs, fields={"default": field.to_wram()}, processes=4)      # list of dicts, same order as jobs
    print(summarize(results))

The only per-battle randomness is the game's own RNG state: `seed` (0-255) is the starting index into the ROM's random table
(the byte at field.RNG_SEED), exactly what differs between two real encounters.  Every worker keeps one BattleSim and resets the
hardware phase between battles, so a result is identical whether it ran first, last or alone."""
import argparse, os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataclasses import dataclass
from multiprocessing import Pool

@dataclass
class Job:
    encounter: int
    background: int = 0
    seed: int = 0              # 0-255: starting index of the game's RNG table
    field: str = "default"     # key into the `fields` dict given to run_many
    max_frames: int = 60000
    policy: str = "fight"      # fight (direct commands, default) | pad (real menu with joypad taps)

_W = {}

def _init(rom_path, fields):
    from sim import BattleSim
    from data import GameData
    rom = open(rom_path, "rb").read()
    _W["sim"] = BattleSim(rom); _W["fields"] = fields; _W["rom"] = rom

def _run(job):
    from scenario import run_battle, FightPolicy, PadFightPolicy
    from field import RNG_SEED
    w = bytearray(_W["fields"][job.field]); w[RNG_SEED] = job.seed & 0xFF
    pol = PadFightPolicy() if job.policy == "pad" else FightPolicy()
    r = run_battle(bytes(w), job.encounter, job.background, policy=pol, max_frames=job.max_frames, sim=_W["sim"])
    return dict(encounter=job.encounter, seed=job.seed, outcome=r.outcome, frames=r.frames, gil=r.gil_gained, exp=r.exp_gained, items=r.items_won,
                hp_after=[c.hp for c in r.party_after], hp_before=[c.hp for c in r.party_before], monsters_left=r.monsters_left, crash=r.crash)

def run_many(jobs, fields, processes=None, rom_path=None, chunksize=4):
    """Run jobs on `processes` worker processes (default: all cores).  Results come back in job order."""
    from sim import DEFAULT_ROM
    rom_path = rom_path or DEFAULT_ROM
    processes = processes or os.cpu_count() or 1
    if processes == 1:
        _init(rom_path, fields); return [_run(j) for j in jobs]
    with Pool(processes, initializer=_init, initargs=(rom_path, fields)) as p:
        return p.map(_run, jobs, chunksize=chunksize)

def summarize(results):
    n = len(results); out = {}
    for o in sorted({r["outcome"] for r in results}): out[o] = sum(r["outcome"] == o for r in results) / n
    fr = sorted(r["frames"] for r in results)
    q = lambda p: fr[min(n - 1, int(p * n))]
    lost = [sum(max(0, b - a) for a, b in zip(r["hp_after"], r["hp_before"])) for r in results]
    return dict(runs=n, outcomes=out, frames_mean=statistics.fmean(fr), frames_p5=q(.05), frames_median=q(.5), frames_p95=q(.95),
                gil_mean=statistics.fmean(r["gil"] for r in results), hp_lost_mean=statistics.fmean(lost),
                item_drops={i: sum(i in r["items"] for r in results) / n for i in sorted({i for r in results for i in r["items"]})})

def main(argv=None):
    from sim import DEFAULT_ROM
    from data import GameData
    from field import Field
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("encounter", type=lambda s: int(s, 0)); ap.add_argument("--rom", default=DEFAULT_ROM)
    ap.add_argument("--runs", type=int, default=256); ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--state"); ap.add_argument("--spec", help="JSON party description (party.py)"); ap.add_argument("--party", type=int, default=4); ap.add_argument("--background", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--frames", type=int, default=60000); ap.add_argument("--policy", default="fight", choices=("fight", "pad"))
    a = ap.parse_args(argv)
    d = GameData(open(a.rom, "rb").read())
    from run import field_from_args
    f = field_from_args(d, a)
    jobs = [Job(a.encounter, a.background, i % 256, "default", a.frames, a.policy) for i in range(a.runs)]
    t = time.time(); res = run_many(jobs, {"default": f.to_wram()}, a.jobs, a.rom); dt = time.time() - t
    print(d.battle_summary(a.encounter)); s = summarize(res)
    for k, v in s.items(): print(f"  {k}: {v}")
    game_s = sum(r["frames"] for r in res) / 60
    print(f"{a.runs} battles in {dt:.1f}s wall = {a.runs / dt:.1f} battles/s; {game_s / dt:.0f}x real time ({game_s / 3600:.1f} h of play)")

if __name__ == "__main__": main()
