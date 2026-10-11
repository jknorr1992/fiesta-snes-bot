"""Time ONE routine called repeatedly from an identical machine state in snes9x and BattleSim (ROM untouched).

A WRAM-resident driver (7F:0000) waits for vblank, then calls TARGET (JSL for RTL routines, a fake interrupt frame for RTI handlers) and
records the elapsed time: snes9x via the H/V counters, BattleSim via its cycle counter.  Used to bisect where the sim's
NMI handler is faster than the real one.

    call_time.py rti C119EA      # the battle NMI handler
    call_time.py rtl D0DE8C      # a JSL-able routine
    call_time.py rti C119E9      # empty handler (measures the harness itself)"""
import sys, os, struct
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim

def latch4():
    return bytes.fromhex("AF37210" "0".replace(" ", "")) + b"".join(bytes.fromhex(x + "9F00107F" "E8") for x in ("AF3C2100", "AF3C2100", "AF3D2100", "AF3D2100"))

def rom_byte(rom, a):
    d = open(rom, "rb").read(); o = (a & 0x3FFFFF) + (512 if len(d) % 1024 == 512 else 0); return d[o]

def driver(kind, target):
    c = bytearray()
    c += bytes.fromhex("C210" "E220")                      # REP #$10 ; SEP #$20
    if os.environ.get("HDMA_OFF"): c += bytes.fromhex("A900" "8F0C4200")   # LDA #0 ; STA $420C : HDMA off (the game may re-enable it)
    if os.environ.get("HDMA_MASK"): c += bytes([0xA9, int(os.environ["HDMA_MASK"], 16), 0x8F, 0x0C, 0x42, 0x00])   # HDMAEN mask
    top = len(c)
    c += bytes.fromhex("AF124200" "30FA" "AF124200" "10FA")   # wait until vblank ends, then until it starts
    c += bytes.fromhex("C230" "AFF00F7F" "AA" "E220")        # X = 7F:0FF0 (record offset)
    c += latch4()
    c += bytes.fromhex("C230" "8A" "8FF20F7F" "E220")       # remember X (record offset after the pre-latch) in 7F:0FF2: the routine under test may clobber X
    c += bytes.fromhex("F40000" "2B" "F47E7E" "AB" "AB")      # D = 0, DB = 7E, as the NMI handler sets up
    pre = len(c)
    if kind == "rts":                                     # JSR-style routine: RTS lands on an RTL byte in ROM (C1:FDD2), which returns here
        c += bytes([0x4B, 0xF4, 0, 0, 0xF4, 0xD1, 0xFD, 0x5C, target & 0xFF, (target >> 8) & 0xFF, target >> 16])
        ret = len(c); c[pre + 2] = (ret - 1) & 0xFF; c[pre + 3] = (ret - 1) >> 8
    elif kind == "rti":
        retlen = 1 + 3 + 1 + 4                              # PHK PEA PHP JML
        c += bytes([0x4B, 0xF4, 0, 0, 0x08, 0x5C, target & 0xFF, (target >> 8) & 0xFF, target >> 16])
        ret = len(c); c[pre + 2] = ret & 0xFF; c[pre + 3] = ret >> 8
    else:
        c += bytes([0x22, target & 0xFF, (target >> 8) & 0xFF, target >> 16]); ret = len(c)
    c += bytes.fromhex("C230" "AFF20F7F" "AA" "E220"); c += latch4()
    c += bytes.fromhex("C230" "8A" "8FF00F7F" "E220" "4C0000")
    return bytes(c), pre, ret

def run(rom, kind, target, nframes=500, start=260, encounter=5):
    code, pre, ret = driver(kind, target)
    e = H.Snes9x(rom); e.load_ra_state(D.STATE); w = e.wram; e.pad = 0x0400
    for f in range(start):
        if f < 60: w[0x4f0] = encounter & 0xFF; w[0x4f1] = encounter >> 8; w[0xadc] = 0; w[0x56] = 1; w[0x16a8] = 0; w[0x16a9] = 0xff
        if f == D.PAD_RELEASE: e.pad = 0
        e.run(1)
    st = bytearray(e.serialize()); b = D.blocks(bytes(st)); ram = bytearray(b["RAM"][:0x20000])
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", b["REG"])
    ram[0x1F00] = 0x40; ram[0x10000:0x10000 + len(code)] = code
    for i in range(0x10FF0, 0x10FF4): ram[i] = 0
    o = bytes(st).find(b"RAM:") + 11; st[o:o + 0x20000] = ram
    o = bytes(st).find(b"REG:") + 11; st[o:o + 16] = struct.pack(">BBHHHHHHH", 0x7F, db, 0x24, 0, d, s, 0, 0, 0)
    e.unserialize(bytes(st))
    for f in range(nframes): e.run(1)
    def decode(w):
        n = (w[0x10FF0] | w[0x10FF1] << 8) // 8; out = []
        for i in range(n):
            o = 0x11000 + 8 * i
            def t(k): return ((w[o + k + 3] & 1) << 8 | w[o + k + 2]) * 1364 + ((w[o + k + 1] & 1) << 8 | w[o + k]) * 4
            out.append((t(4) - t(0)) % 357368)
        return out
    real = decode(w)
    sim = BattleSim(rom); sim.load_wram(bytes(ram)); m = sim.m
    for r, v in ((cpu.R_PB, 0x7F), (cpu.R_DB, db), (cpu.R_P, 0x24), (cpu.R_E, 0), (cpu.R_A, 0), (cpu.R_D, d), (cpu.R_S, s), (cpu.R_X, 0), (cpu.R_Y, 0), (cpu.R_PC, 0),
                 (cpu.R_NMITIMEN, 0x81), (cpu.R_MEMSEL, b["FIL"][0x420D] & 1), (cpu.R_FRAMECYC, 306900 - 8)): m.setreg(r, v)
    sim.load_hardware(b["FIL"], b["DMA"]); sim.running = True; sim.step(1)
    for f in range(nframes): sim.step(1)
    return real, decode(sim.w)

if __name__ == "__main__":
    kind, tgt = sys.argv[1], int(sys.argv[2], 16)
    if kind == "rts" and rom_byte(D.ROM, 0xC1FDD2) != 0x6B: sys.exit("no RTL at C1:FDD2")
    real, simt = run(D.ROM, kind, tgt, int(sys.argv[3]) if len(sys.argv) > 3 else 300)
    print("real n", len(real), "min", min(real), "mean", sum(real) / len(real), "max", max(real))
    print("sim  n", len(simt), "min", min(simt), "mean", sum(simt) / len(simt), "max", max(simt))
    print("real first 10", real[:10]); print("sim  first 10", simt[:10])
