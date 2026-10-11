"""Frames per main-battle-loop iteration (BattleTickerA $7C53 changes once per iteration), snes9x vs BattleSim."""
import sys, os, struct
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim
from diff_battle import PAD_RELEASE

def series(rom, nframes=1800, start=260, encounter=5, spc=None):
    e = H.Snes9x(rom); e.load_ra_state(D.STATE); w = e.wram; e.pad = 0x0400
    for f in range(start):
        if f < 60: w[0x4f0] = encounter & 0xFF; w[0x4f1] = encounter >> 8; w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff
        if f == PAD_RELEASE: e.pad = 0        # stop holding DOWN once the battle is under way: held input makes the real menu redraw and costs time the sim (no input) does not spend
        e.run(1)
    b = D.blocks(e.serialize())
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])
    A = []
    for f in range(nframes): e.run(1); A.append(w[0x7C53])
    sim = BattleSim(rom); sim.load_wram(b["RAM"][:0x20000]); m = sim.m
    if spc == 'state': sim.use_spc(b["SND"])
    elif spc == 'boot': sim.use_spc()
    for r, v in ((cpu.R_PB, pb), (cpu.R_DB, db), (cpu.R_P, p & 0xFF), (cpu.R_E, (p >> 8) & 1), (cpu.R_A, a), (cpu.R_D, d), (cpu.R_S, s),
                 (cpu.R_X, x), (cpu.R_Y, y), (cpu.R_PC, pc), (cpu.R_NMITIMEN, 0x81), (cpu.R_MEMSEL, b["FIL"][0x420D] & 1), (cpu.R_FRAMECYC, 306900 - 8)): m.setreg(r, v)
    sim.load_hardware(b["FIL"], b["DMA"]); sim.running = True; sim.step(1); B = []
    for f in range(nframes): sim.step(1); B.append(sim.w[0x7C53])
    return A, B, b

def gaps(S):
    out, last, lastv = [], None, S[0]
    for f, v in enumerate(S):
        if v != lastv:
            if last is not None: out.append(f - last)
            last = f; lastv = v
    return out

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    A, B, _ = series(D.ROM, 1800, encounter=int(args[0]) if args else 5, spc="state" if "--spc" in sys.argv else "boot" if "--spc-boot" in sys.argv else None)
    ga, gb = gaps(A), gaps(B)
    print("snes9x iterations", len(ga), "frames/iter hist", {k: ga.count(k) for k in sorted(set(ga))})
    print("sim    iterations", len(gb), "frames/iter hist", {k: gb.count(k) for k in sorted(set(gb))})
    print("snes9x first 60:", ga[:60]); print("sim    first 60:", gb[:60])
