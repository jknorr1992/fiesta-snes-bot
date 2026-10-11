"""Cycle-cost microbenchmarks: run the same WRAM-resident code in snes9x and in BattleSim, compare frames to completion.
Code runs from 7F:0000, NMI trampoline replaced by RTI.  Usage: microbench.py"""
import sys, os, struct, re
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim

def asm_loop(body, n, setup=b"", hdma=b""):
    """setup; LDX #n (16-bit X); loop: body; DEX; BNE loop; LDA #1; STA $7F0FF0; JMP self.  Register state: M=1, X=0 (16-bit index)."""
    code = bytearray()
    code += bytes([0xC2, 0x10, 0xE2, 0x20])                # REP #$10 ; SEP #$20
    code += bytes([0xA9, 0x00, 0x8F, 0x0C, 0x42, 0x00])   # LDA #0 ; STA $00:420C  (HDMA off)
    code += hdma
    code += setup
    code += bytes([0xA2, n & 0xFF, n >> 8])               # LDX #n
    loop = len(code)
    code += body
    code += bytes([0xCA])                                  # DEX
    off = loop - (len(code) + 2)
    assert off >= -128, "body too long"
    code += bytes([0xD0, off & 0xFF])
    code += bytes([0xA9, 0x01, 0x8F, 0xF0, 0x0F, 0x7F])   # LDA #1 ; STA $7F0FF0
    here_ = len(code)
    code += bytes([0x4C, here_ & 0xFF, here_ >> 8])        # JMP here (bank 7F)
    return bytes(code)

def bench(code, nmax=400, extra_wram=None):
    e = H.Snes9x(D.ROM); e.load_ra_state(D.STATE); e.run(3); w = e.wram
    st = bytearray(e.serialize()); b = D.blocks(bytes(st))
    ram = bytearray(b["RAM"][:0x20000])
    ram[0x1F00] = 0x40                                      # NMI trampoline -> RTI
    ram[0x10000:0x10000 + len(code)] = code; ram[0x10FF0] = 0
    for a, v in (extra_wram or {}).items(): ram[a] = v
    # write back into snes9x state: RAM block and REG block
    o = bytes(st).find(b"RAM:") + 11; st[o:o + 0x20000] = ram
    o = bytes(st).find(b"REG:") + 11
    pb, db, p, a, d, s, x, y, pc = struct.unpack(">BBHHHHHHH", bytes(st[o:o + 16]))
    st[o:o + 16] = struct.pack(">BBHHHHHHH", 0x7F, 0x7F, 0x0034 | 0x20 | 0x10, 0, 0, 0x1FFF, 0, 0, 0x0000)
    e.unserialize(bytes(st))
    fs = None
    for f in range(nmax):
        e.run(1)
        if w[0x10FF0]: fs = f + 1; break
    # sim from the same state
    b2 = D.blocks(bytes(st))
    sim = BattleSim(D.ROM); sim.load_wram(bytes(ram)); m = sim.m
    for r, v in ((cpu.R_PB, 0x7F), (cpu.R_DB, 0x7F), (cpu.R_P, 0x34 | 0x30), (cpu.R_E, 0), (cpu.R_A, 0), (cpu.R_D, 0), (cpu.R_S, 0x1FFF),
                 (cpu.R_X, 0), (cpu.R_Y, 0), (cpu.R_PC, 0), (cpu.R_NMITIMEN, b2["FIL"][0x4200]), (cpu.R_FRAMECYC, 306900 - 8), (cpu.R_MEMSEL, 0)): m.setreg(r, v)
    sim.running = True; sim.step(1)
    ss = None
    for f in range(nmax):
        sim.step(1)
        if sim.w[0x10FF0]: ss = f + 1; break
    return fs, ss

def rep(b, total=100):
    return b * max(1, total // len(b))
TESTS = {
 "inx": (rep(bytes([0xE8])), 60000),
 "lda dp / sta dp": (rep(bytes([0xA5, 0x10, 0x85, 0x11])), 30000),
 "lda abs/sta abs": (rep(bytes([0xAD, 0x00, 0x30, 0x8D, 0x00, 0x31])), 20000),
 "lda long,x": (rep(bytes([0xBF, 0x00, 0x20, 0x7E])), 20000),
 "lda [dp],y sta [dp],y": (rep(bytes([0xB7, 0x20, 0x97, 0x24])), 20000),
 "lda (dp),y": (rep(bytes([0xB1, 0x20])), 30000),
 "inc abs": (rep(bytes([0xEE, 0x00, 0x30])), 20000),
 "xba/pha/pla": (rep(bytes([0xEB, 0x48, 0x68])), 25000),
}
def sta_long(addr, val):    # LDA #val ; STA $00:addr
    return bytes([0xA9, val, 0x8F, addr & 0xFF, addr >> 8, 0x00])

def hdma_setup(chans):
    """chans: list of (channel, mode, table_wram_off, indirect).  Table at 7F:off in WRAM."""
    c = b""; mask = 0
    for ch, mode, off, ind in chans:
        base = 0x4300 + ch * 0x10
        c += sta_long(base, mode | (0x40 if ind else 0)) + sta_long(base + 1, 0x26 + ch)   # B-bus reg: some PPU register (writes dropped)
        c += sta_long(base + 2, off & 0xFF) + sta_long(base + 3, off >> 8) + sta_long(base + 4, 0x7F)
        c += sta_long(base + 7, 0x7F)
        mask |= 1 << ch
    c += sta_long(0x420C, mask)
    return c

def dma_body(size, breg=0x18, mode=1, reps=1):
    one = (sta_long(0x4300, mode) + sta_long(0x4301, breg) + sta_long(0x4302, 0x00) + sta_long(0x4303, 0x20) + sta_long(0x4304, 0x7E)
           + sta_long(0x4305, size & 0xFF) + sta_long(0x4306, size >> 8) + sta_long(0x420B, 1))
    return one * reps

def run_dma():
    for size in (2, 16, 64, 256, 1024):
        for breg, mode, lab in ((0x18, 1, "vram"), (0x04, 0, "oam"), (0x22, 0, "cgram")):
            code = asm_loop(dma_body(size, breg, mode), 6000 if size < 256 else 2500)
            fs, ss = bench(code, nmax=900)
            print(f"dma {lab:6s} {size:5d} bytes  snes9x {fs}  sim {ss}")

def run_more():
    base = lambda body, n: asm_loop(body, n)
    tests = {
     "lda long ROM C1 (slow)": rep(bytes([0xAF, 0x00, 0x80, 0xC1]), 100),
     "lda long ROM 00:8000": rep(bytes([0xAF, 0x00, 0x80, 0x00]), 100),
     "sta $2100 (PPU, 6cyc)": rep(bytes([0x8F, 0x00, 0x21, 0x00]), 100),
     "lda $4212 (6cyc)": rep(bytes([0xAF, 0x12, 0x42, 0x00]), 100),
     "lda $4016 (12cyc)": rep(bytes([0xAF, 0x16, 0x40, 0x00]), 100),
     "sta $2180 wram port": rep(bytes([0x8F, 0x80, 0x21, 0x00]), 100),
     "lda 7E:2000 long": rep(bytes([0xAF, 0x00, 0x20, 0x7E]), 100),
     "mvn 16 bytes": rep(bytes([0xC2, 0x30, 0xA9, 0x0F, 0x00, 0xA2, 0x00, 0x20, 0xA0, 0x00, 0x30, 0x54, 0x7E, 0x7E, 0xE2, 0x20]), 100),
    }
    for name, body in tests.items():
        n = 20000 if "mvn" not in name else 4000
        code = asm_loop(body, n)
        fs, ss = bench(code, nmax=600)
        print(f"{name:28s} snes9x {fs}  sim {ss}")

if __name__ == "__main__" and len(sys.argv) == 1:
    # HDMA: tables at 7F:8000.. ; each: repeat-count byte 0x80|127 then data, ending 0
    tbl = {}
    for i in range(0, 2000, 1):
        tbl[0x8000 + i] = 0
    def table(off, mode_len, ind=False):
        t = {}
        o = off
        for _ in range(4):               # four groups of 127 lines? 4 x (0x80|0x7F) repeat lines -> 508 lines
            t[o] = 0xFF; o += 1
            if ind: t[o] = 0x00; t[o + 1] = 0xA0; o += 2     # pointer 7F:A000
            else: o += mode_len * 127
        t[o] = 0
        return t
    for label, chans, tabs in (
        ("hdma 1ch mode0 direct", [(1, 0, 0x8000, False)], table(0x8000, 1)),
        ("hdma 1ch mode3 direct", [(1, 3, 0x8000, False)], table(0x8000, 4)),
        ("hdma 3ch mixed", [(1, 0, 0x8000, False), (2, 2, 0x8800, False), (6, 0, 0x9000, False)], {**table(0x8000, 1), **table(0x8800, 2), **table(0x9000, 1)}),
        ("hdma 2ch indirect", [(1, 3, 0x8000, True), (2, 2, 0x8100, True)], {**table(0x8000, 4, True), **table(0x8100, 2, True)}),
    ):
        code = asm_loop(rep(bytes([0xE8])), 60000, hdma=hdma_setup(chans))
        extra = dict(tabs)
        fs, ss = bench(code, extra_wram={a + 0x10000 - 0x10000: v for a, v in extra.items()} if False else {0x10000 + a: v for a, v in extra.items()}, nmax=400)
        print(f"{label:26s} snes9x frames {fs}  sim frames {ss}")
    for name, t in TESTS.items():
        break
    run_dma()
    run_more()
    for name, t in TESTS.items():
        break
        
        if not t: continue
        body, n = t
        code = asm_loop(body, n, setup=bytes([0xA0, 0x00, 0x00]))   # LDY #0
        # need DP pointers: $20/$24 -> 7E:2000 / 7E:3000
        extra = {0x20: 0x00, 0x21: 0x20, 0x22: 0x7E, 0x24: 0x00, 0x25: 0x30, 0x26: 0x7E}
        fs, ss = bench(code, extra_wram=extra)
        print(f"{name:26s} n={n:5d} snes9x frames {fs}  sim frames {ss}")

def run_sound(cmds, n=300):
    out = {}
    for cmd, arg in cmds:
        body = (bytes([0xA9, cmd, 0x8F, 0x00, 0x1D, 0x7E, 0xA9, arg, 0x8F, 0x01, 0x1D, 0x7E,      # LDA #cmd ; STA $7E1D00 ; LDA #arg ; STA $7E1D01
                       0x22, 0x04, 0x00, 0xC4]))                                                    # JSL $C40004
        # JSL clobbers registers/flags state expectations: ExecSound restores them
        code = asm_loop(body, n)
        fs, ss = bench(code, nmax=3000)
        if fs is None: print(cmd, arg, "no completion"); continue
        extra = (fs - ss) * 357368 / n
        out[(cmd, arg)] = extra
        print(f"sound cmd {cmd:#04x} arg {arg:3d}: snes9x {fs} frames, sim(skipped) {ss}: extra {extra:.0f} master cycles/call")
    return out

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "sound":
    run_sound([(2,1),(2,22),(2,23),(2,82),(2,59),(2,14),(0x71,0),(0xFC,0),(0x7F,0),(0x70,59),(0x75,1),(0,0)])
