"""Where is the main thread when each NMI fires?  snes9x (an RAM NMI logger placed *in front of* the game's own NMI vector stub at
$1F00 — WRAM only, the ROM is untouched) vs the sim (PC at the end of every frame).  Shows whether the sim's main loop spends
time where the real one does, frame by frame."""
import sys, os, struct, collections
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim
from diff_battle import PAD_RELEASE

LOGGER = bytes.fromhex(
    "08" "C230" "48" "DA"                  # php ; rep #$30 ; pha ; phx
    "AFF00F7F" "AA"                        # lda $7f0ff0 ; tax
    "A307" "9F00107F" "E8E8"               # lda $07,s (interrupted PC) ; sta $7f1000,x ; inx inx
    "A309" "9F00107F" "E8E8"               # lda $09,s (interrupted PBR) ; sta $7f1000,x ; inx inx
    "AF537C7E" "9F00107F" "E8E8"           # lda $7e7c53 (BattleTickerA) ; sta $7f1000,x ; inx inx
    "8A" "8FF00F7F"                        # txa ; sta $7f0ff0
    "FA" "68" "28"                         # plx ; pla ; plp
    "5CEA19C1")                            # jml $c119ea (the game's own NMI handler)

def snes9x_log(rom, nframes, start=260, encounter=5):
    e = H.Snes9x(rom); e.load_ra_state(D.STATE); w = e.wram; e.pad = 0x0400
    for f in range(start):
        if f < 60: w[0x4f0] = encounter & 0xFF; w[0x4f1] = encounter >> 8; w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff
        if f == PAD_RELEASE: e.pad = 0        # stop holding DOWN once the battle is under way: held input makes the real menu redraw and costs time the sim (no input) does not spend
        e.run(1)
    b = D.blocks(e.serialize())
    w[0x10FF0] = 0; w[0x10FF1] = 0                                  # 7f:0ff0 = log index (wram[0x10000 + ..])
    for i, c in enumerate(LOGGER): w[0x1F10 + i] = c
    w[0x1F00:0x1F04] = bytes([0x5C, 0x10, 0x1F, 0x00])              # jml $001f10
    for f in range(nframes): e.run(1)
    n = (w[0x10FF0] | w[0x10FF1] << 8) // 6
    log = [(w[0x11000 + 6 * i] | w[0x11001 + 6 * i] << 8 | w[0x11002 + 6 * i] << 16, w[0x11004 + 6 * i]) for i in range(n)]
    return log, b

def sim_log(rom, b, nframes):
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])
    sim = BattleSim(rom); sim.load_wram(b["RAM"][:0x20000]); m = sim.m
    for r, v in ((cpu.R_PB, pb), (cpu.R_DB, db), (cpu.R_P, p & 0xFF), (cpu.R_E, (p >> 8) & 1), (cpu.R_A, a), (cpu.R_D, d), (cpu.R_S, s),
                 (cpu.R_X, x), (cpu.R_Y, y), (cpu.R_PC, pc), (cpu.R_NMITIMEN, 0x81), (cpu.R_MEMSEL, b["FIL"][0x420D] & 1), (cpu.R_FRAMECYC, 306900 - 8)): m.setreg(r, v)
    sim.load_hardware(b["FIL"], b["DMA"]); sim.running = True; out = []
    for f in range(nframes): sim.step(1); out.append((m.pc24, sim.rb(0x7C53)))
    return out

if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    A, b = snes9x_log(D.ROM, N); B = sim_log(D.ROM, b, N)
    print(len(A), len(B))
    for i in range(min(len(A), len(B), 120)):
        print(f"{i:4d}  snes9x {A[i][0]:06x} t={A[i][1]}    sim {B[i][0]:06x} t={B[i][1]}")
