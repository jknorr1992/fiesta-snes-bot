"""Differential test against real snes9x (libretro core).

Forces a random battle in snes9x (patched ROM copy, WRAM pokes on the world map), lets it run into the main battle
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

ROM = "/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc"

def forced_rom(encounter=5, out="/tmp/rom_forced_battle.sfc"):
    """ROM copy whose random-encounter group table (RandBattleGrp, 0x106800) always yields `encounter`."""
    rom = bytearray(open(ROM, "rb").read())
    for g in range(256):
        for k in range(4): rom[0x106800 + 8 * g + 2 * k] = encounter & 0xFF; rom[0x106800 + 8 * g + 2 * k + 1] = encounter >> 8
    open(out, "wb").write(rom); return out

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

def run(rom, nframes=2600, start=260):
    e = H.Snes9x(rom); e.load_ra_state(STATE); w = e.wram; e.pad = 0x0400      # chocobo on the world map, walk DOWN
    for f in range(start):
        if f < 60: w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff   # force the step-counter / probability checks
        e.run(1)
    b = blocks(e.serialize())
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])         # snes9x freeze format: big-endian
    A = []
    for f in range(nframes): e.run(1); A.append(bytes(w[0x2000:0x2600]))
    sim = BattleSim(rom); sim.load_wram(b["RAM"][:0x20000]); m = sim.m
    for r, v in ((cpu.R_PB, pb), (cpu.R_DB, db), (cpu.R_P, p & 0xFF), (cpu.R_E, (p >> 8) & 1), (cpu.R_A, a), (cpu.R_D, d),
                 (cpu.R_S, s), (cpu.R_X, x), (cpu.R_Y, y), (cpu.R_PC, pc), (cpu.R_NMITIMEN, 0x81)):
        m.setreg(r, v)
    sim.running = True; B = []
    for f in range(nframes): sim.step(1); B.append(bytes(sim.w[0x2000:0x2600]))
    ea, eb = events(A), events(B); n = min(len(ea), len(eb))
    err = [eb[i][0] - ea[i][0] for i in range(n)]
    same = all(ea[i][1] == eb[i][1] for i in range(n))
    print(f"snes9x events {len(ea)}, sim events {len(eb)}, identical sequence: {same}")
    print("timing error per event (sim - snes9x, frames):", err)
    return same, err

if __name__ == "__main__":
    run(forced_rom(int(sys.argv[1]) if len(sys.argv) > 1 else 5), int(sys.argv[2]) if len(sys.argv) > 2 else 2600)
