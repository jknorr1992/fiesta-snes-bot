"""Time single battles in the simulator: game frames next to the wall-clock time of the run.

    python3 ffv-bot/tools/battle_clock.py ENCOUNTER [--background N] [--state WRAM_FILE] [--seed N] [--policy fight|veteran] [--repeat N]

frames / seconds is the speed in game frames per second: about 60 would be real time (the SNES runs 60 frames per second), the sim should be far
above it.  The clock covers sim.start() to the end of the battle (the policy's thinking included); loading the ROM and the party is shown apart.
Without --state a fresh four-member party is used (policy fight); a state is a raw 128 KB WRAM image, e.g. from the campaign's output dir.
This file is outside battle/ on purpose: the campaign's cache and solved-fight records are keyed by the .py files in battle/."""
import argparse, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "battle"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("encounter", type=lambda s: int(s, 0)); ap.add_argument("--background", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--rom"); ap.add_argument("--state"); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--policy", choices=("fight", "veteran"), default="fight"); ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-frames", type=int, default=60000)
    a = ap.parse_args()
    t0 = time.perf_counter()
    import campaign as C
    from scenario import run_battle, FightPolicy
    from sim import BattleSim, DEFAULT_ROM
    from data import GameData
    from field import Field, RNG_SEED
    rom_path = a.rom or os.environ.get("FFV_ROM") or DEFAULT_ROM
    rom = open(rom_path, "rb").read()
    d = GameData(rom); sim = BattleSim(rom)
    if a.policy == "veteran": C._init(rom_path, "veteran", True, 12, C.forbidden_commands(""))
    base = open(a.state, "rb").read() if a.state else Field.new(d, party=4).to_wram()
    print(f"setup {time.perf_counter() - t0:.2f}s (ROM, tables, party)")
    tot_f = tot_s = 0.0
    for i in range(a.repeat):
        w = bytearray(base); w[RNG_SEED] = (a.seed + i) & 0xFF
        pol = C._policy(d, a.encounter) if a.policy == "veteran" else FightPolicy()
        t = time.perf_counter()
        r = run_battle(bytes(w), a.encounter, a.background, policy=pol, max_frames=a.max_frames, sim=sim)
        dt = time.perf_counter() - t
        tot_f += r.frames; tot_s += dt
        print(f"seed {(a.seed + i) & 0xFF:3d}: {r.outcome} {r.frames} frames ({r.frames / 60:.1f}s of game) in {dt:.3f}s wall = {r.frames / dt:,.0f} frames/s = {r.frames / dt / 60:.1f}x real time")
    if a.repeat > 1: print(f"all: {tot_f:.0f} frames in {tot_s:.2f}s = {tot_f / tot_s:,.0f} frames/s = {tot_f / tot_s / 60:.1f}x real time")


if __name__ == "__main__":
    main()
