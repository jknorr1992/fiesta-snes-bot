"""Duration of the game's NMI handler (C1:19EA .. RTI): snes9x (H/V counter latched at entry and exit by a WRAM-resident wrapper, ROM untouched)
vs BattleSim (cycle counter)."""
import sys, os, struct, collections
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim

def latch(): return "AF372100" + ("AF3C2100" "9F00107F" "E8") * 2 + ("AF3D2100" "9F00107F" "E8") * 2
ENTRY = bytes.fromhex("08" "C230" "48" "DA" "AFF00F7F" "AA" "E220" + latch() + "C230" "8A" "8FF00F7F" "FA" "68" "28" "4B" "F4601F" "08" "5CEA19C1")
EXIT  = bytes.fromhex("08" "C230" "48" "DA" "AFF00F7F" "AA" "E220" + latch() + "C230" "8A" "8FF00F7F" "FA" "68" "28" "40")

def real(rom, nframes, start=260, encounter=5):
    e = H.Snes9x(rom); e.load_ra_state(D.STATE); w = e.wram; e.pad = 0x0400
    for f in range(start):
        if f < 60: w[0x4f0] = encounter & 0xFF; w[0x4f1] = encounter >> 8; w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff
        if f == D.PAD_RELEASE: e.pad = 0
        e.run(1)
    w[0x10FF0] = 0; w[0x10FF1] = 0
    for i, c in enumerate(ENTRY): w[0x1F10 + i] = c
    for i, c in enumerate(EXIT): w[0x1F60 + i] = c
    w[0x1F00:0x1F04] = bytes([0x5C, 0x10, 0x1F, 0x00])
    for f in range(nframes): e.run(1)
    n = (w[0x10FF0] | w[0x10FF1] << 8) // 8; out = []
    for i in range(n):
        o = 0x11000 + 8 * i
        def t(k): return ((w[o + k + 3] & 1) << 8 | w[o + k + 2]) * 1364 + ((w[o + k + 1] & 1) << 8 | w[o + k]) * 4
        out.append((t(4) - t(0)) % 357368)
    return out

if __name__ == "__main__":
    r = real(D.ROM, 600)
    print("real NMI handler (cycles incl ~250 wrapper): n", len(r), "mean", sum(r) / len(r), "min", min(r), "max", max(r))
    print(collections.Counter(x // 2000 * 2000 for x in r).most_common(10))

def sim_times(rom, b, nframes):
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])
    sim = BattleSim(rom); sim.load_wram(b["RAM"][:0x20000]); m = sim.m
    for r, v in ((cpu.R_PB, pb), (cpu.R_DB, db), (cpu.R_P, p & 0xFF), (cpu.R_E, (p >> 8) & 1), (cpu.R_A, a), (cpu.R_D, d), (cpu.R_S, s),
                 (cpu.R_X, x), (cpu.R_Y, y), (cpu.R_PC, pc), (cpu.R_FRAMECYC, 306900 - 8)): m.setreg(r, v)
    sim.load_hardware(b["FIL"], b["DMA"]); sim.running = True; sim.step(1)
    m.clear_hooks(); m.hook(0xC119EA, 0); m.hook(0xC11A56, 0)
    out = []; t0 = None; fr0 = m.reg(cpu.R_FRAMES)
    while m.reg(cpu.R_FRAMES) - fr0 < nframes:
        rc = m.run(2 ** 62, 0)
        if rc >= 100:
            if m.pc24 == 0xC119EA: t0 = m.cyc_total
            elif t0 is not None: out.append(m.cyc_total - t0)
            m.skip_hook()
    return out
