"""Differential test against real snes9x (libretro core).

Forces a random battle in snes9x (WRAM pokes on the world map; the ROM is never modified), lets it run into the main battle
loop, then resumes BattleSim from that exact CPU/WRAM state with zero input and compares when each combatant's
HP / MP / status changes.

    diff_battle.py [encounter=5] [frames=2600]

Result on encounter 5, 2600 frames: all 22 HP/MP/status events identical (same damage values, same order);
event times within 7 frames (< 0.3 %) of snes9x after calibrating sim.CYC_SCALE."""
import sys, os, re, struct
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu
from sim import BattleSim

STATE = "/mnt/project-files/retroarch-testbed/states/Snes9x/Final Fantasy V (Japan) (patched)(1).state4"

from sim import DEFAULT_ROM as ROM
PAD_RELEASE = 100

def blocks(d):
    out, o = {}, d.find(b"NAM:")
    while o != -1 and o < len(d):
        m = re.match(rb"([A-Z0-9]{3}):(\d{6}):", d[o:o + 11])
        if not m: break
        n = int(m.group(2)); out[m.group(1).decode()] = d[o + 11:o + 11 + n]; o += 11 + n
    return out

def sig(x):
    return tuple((x[0x80 * i + 6] | x[0x80 * i + 7] << 8, x[0x80 * i + 10] | x[0x80 * i + 11] << 8, x[0x80 * i + 0x1A:0x80 * i + 0x1E].hex()) for i in range(12))

def events(T):
    out, p = [], None
    for f, x in enumerate(T):
        s = sig(x)
        if s != p: out.append((f, s)); p = s
    return out

def run(rom, nframes=2600, start=260, encounter=5, spc=None):
    """spc: None = constant-stall sound shortcut; 'state' = real sound driver on an SPC700 started from the snes9x SPC state; 'boot' = driver uploaded by InitSound."""
    e = H.Snes9x(rom); e.load_ra_state(STATE); w = e.wram; e.pad = 0x0400      # chocobo on the world map, walk DOWN
    for f in range(start):
        if f < 60: w[0x4f0] = encounter & 0xFF; w[0x4f1] = encounter >> 8; w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff   # force the step-counter / probability checks
        if f == PAD_RELEASE: e.pad = 0        # stop holding DOWN once the battle is under way: held input makes the real menu redraw and costs time the sim (no input) does not spend
        e.run(1)
    b = blocks(e.serialize())
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])         # snes9x freeze format: big-endian
    A = []
    for f in range(nframes): e.run(1); A.append(bytes(w[0x2000:0x2600]))
    sim = BattleSim(rom); sim.load_wram(b["RAM"][:0x20000]); m = sim.m
    if spc == 'state': sim.use_spc(b["SND"])
    elif spc == 'boot': sim.use_spc()
    for r, v in ((cpu.R_PB, pb), (cpu.R_DB, db), (cpu.R_P, p & 0xFF), (cpu.R_E, (p >> 8) & 1), (cpu.R_A, a), (cpu.R_D, d),
                 (cpu.R_S, s), (cpu.R_X, x), (cpu.R_Y, y), (cpu.R_PC, pc), (cpu.R_NMITIMEN, 0x81), (cpu.R_MEMSEL, b["FIL"][0x420D] & 1), (cpu.R_FRAMECYC, 306900 - 8)):
        m.setreg(r, v)
    sim.load_hardware(b["FIL"], b["DMA"]); sim.running = True; sim.step(1); B = []
    for f in range(nframes): sim.step(1); B.append(bytes(sim.w[0x2000:0x2600]))
    ea, eb = events(A), events(B); n = min(len(ea), len(eb))
    err = [eb[i][0] - ea[i][0] for i in range(n)]
    same = all(ea[i][1] == eb[i][1] for i in range(n))
    print(f"snes9x events {len(ea)}, sim events {len(eb)}, identical sequence: {same}")
    print("timing error per event (sim - snes9x, frames):", err)
    return same, err

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spc = "state" if "--spc" in sys.argv else "boot" if "--spc-boot" in sys.argv else None
    run(ROM, int(args[1]) if len(args) > 1 else 2600, encounter=int(args[0]) if args else 5, spc=spc)
