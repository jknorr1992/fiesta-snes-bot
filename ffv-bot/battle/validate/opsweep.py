"""Per-opcode cycle check: every non-control-flow 65816 opcode, in all four (M,X) width modes, run R times inside a WRAM loop in snes9x and in
BattleSim; reports the per-instruction master-cycle difference (after subtracting a NOP-body baseline that carries the loop overhead).
    opsweep.py [mode ...]   mode = m1x1 m1x0 m0x1 m0x0 (default all);  opsweep.py -v  prints every opcode, not just mismatches."""
import sys, os, struct
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, cpu, diff_battle as D
from sim import BattleSim
from dis65816 import OPS

R = 48
CTL_ADDR = 0x0F00
FRAME = 357368

def ctl_code(n):
    c = bytes([0x08, 0xC2, 0x30, 0x48, 0xDA, 0x5A,                  # php ; rep #$30 ; pha ; phx ; phy
               0xEE, 0xE0, 0x0F,                                    # inc $0fe0
               0xAD, 0xE0, 0x0F, 0xC9, n & 0xFF, n >> 8,            # lda $0fe0 ; cmp #n
               0xD0, 0x0A,                                          # bne skip
               0xE2, 0x20, 0xA9, 0x01, 0x8F, 0xF0, 0x0F, 0x7F,      # sep #$20 ; lda #1 ; sta $7f0ff0
               0x80, 0xFE,                                          # bra *
               0x7A, 0xFA, 0x68, 0x28, 0x60])                       # skip: ply ; plx ; pla ; plp ; rts
    return c

def operand(mode, m, x):
    """operand bytes for an addressing mode (pointers set up by setup_memory)"""
    if mode in ("", "A", "s"): return b""
    if mode == "#M": return bytes([0x01]) if m else bytes([0x01, 0x00])
    if mode == "#X": return bytes([0x01]) if x else bytes([0x01, 0x00])
    if mode in ("dp", "dp,X", "dp,Y"): return bytes([0x30])
    if mode in ("(dp,X)",): return bytes([0x20])
    if mode in ("(dp),Y", "(dp)"): return bytes([0x28])
    if mode in ("[dp]", "[dp],Y"): return bytes([0x2C])
    if mode in ("abs", "abs,X", "abs,Y"): return bytes([0x00, 0x30])
    if mode in ("long", "long,X"): return bytes([0x00, 0x30, 0x7F])
    if mode == "sr,S": return bytes([0x02])
    if mode == "(sr,S),Y": return bytes([0x02])
    return None

SKIP = {"BRK", "COP", "JMP", "JML", "JSR", "JSL", "RTS", "RTL", "RTI", "WAI", "STP", "WDM", "MVN", "MVP", "PEA", "PEI", "PER", "XCE", "TCS", "TXS", "TCD",
        "PHD", "PLD", "PHB", "PLB", "PHP", "PLP", "PHA", "PLA", "PHX", "PLX", "PHY", "PLY", "PHK", "REP", "SEP", "BRL"}
PAIRS = {"PHA": (bytes([0x48, 0x68]), 2), "PHX": (bytes([0xDA, 0xFA]), 2), "PHY": (bytes([0x5A, 0x7A]), 2), "PHP": (bytes([0x08, 0x28]), 2),
         "PHB": (bytes([0x8B, 0xAB]), 2), "PHD": (bytes([0x0B, 0x2B]), 2), "PHK": (bytes([0x4B, 0x68]), 2)}

def templates(m, x):
    out = []
    for op in range(256):
        mn, _, mode = OPS[op].partition(" ")
        if mn in ("BPL", "BMI", "BVC", "BVS", "BCC", "BCS", "BNE", "BEQ", "BRA"): continue
        if mn in SKIP: continue
        o = operand(mode, m, x)
        if o is None: continue
        out.append((f"{op:02X} {OPS[op]}", bytes([op]) + o, 1))
    for mn, (b, k) in PAIRS.items():
        if mn == "PHK": continue
        out.append((f"pair {mn}", b, 2))
    # control flow / stack specials: stubs at 7F:0E00 (rts), 7F:0E10 (rtl), 7F:0E20 (rti), 7F:0E30 (rts for jsr (abs,x))
    out += [("JSR abs + RTS", bytes([0x20, 0x00, 0x0E]), 1), ("JSL + RTL", bytes([0x22, 0x10, 0x0E, 0x7F]), 1),
            ("JMP abs", bytes([0x4C, 0x03, 0x00]), 1),   # target patched below (jump to next instruction)
            ("PEA + PLA(2)", bytes([0xF4, 0x34, 0x12, 0xAB if False else 0x68]) , 1),
            ("PHD/PLD", bytes([0x0B, 0x2B]), 2), ("PHB/PLB", bytes([0x8B, 0xAB]), 2), ("PHK/PLA", bytes([0x4B, 0x68]), 2),
            ("REP #0", bytes([0xC2, 0x00]), 1), ("SEP #0", bytes([0xE2, 0x00]), 1), ("PEI (30) / PLA", bytes([0xD4, 0x30, 0x68]), 1),
            ("PER / PLA", bytes([0x62, 0x00, 0x00, 0x68]), 1), ("TDC", bytes([0x7B]), 1), ("TSC", bytes([0x3B]), 1),
            ("JSR (abs,X) + RTS", bytes([0xFC, 0x30, 0x0E]), 1), ("JMP (abs)", bytes([0x6C, 0x40, 0x0E]), 1),
            ("MVN 4 bytes", bytes([0xC2, 0x30, 0xA9, 0x03, 0x00, 0xA2, 0x00, 0x30, 0xA0, 0x00, 0x32, 0x54, 0x7F, 0x7F]), 1)]
    # branches: taken (offset 0) and not taken
    out += [("BRA +0 taken", bytes([0x80, 0x00]), 1), ("BEQ +0 (Z=1? depends)", bytes([0xF0, 0x00]), 1), ("BNE +0", bytes([0xD0, 0x00]), 1),
            ("BCC +0", bytes([0x90, 0x00]), 1), ("BCS +0", bytes([0xB0, 0x00]), 1), ("BPL +0", bytes([0x10, 0x00]), 1), ("BMI +0", bytes([0x30, 0x00]), 1)]
    return out

def setup_code(m, x):
    c = bytearray([0xC2, 0x30, 0xA2, 0x00, 0x18, 0x9A])               # rep #$30 ; ldx #$1800 ; txs   (pointer for (sr,S),Y lives at 1802)
    c += bytes([0xA9, 0x00, 0x00, 0x5B])                                # lda #0 ; tcd
    c += bytes([0xE2, 0x20, 0xA9, 0x00, 0x8F, 0x0C, 0x42, 0x00])       # HDMA off
    c += bytes([0xE2, 0x30]) if (m and x) else (bytes([0xE2, 0x20, 0xC2, 0x10]) if m else bytes([0xC2, 0x20, 0xE2, 0x10]) if x else bytes([0xC2, 0x30]))
    c += (bytes([0xA2, 0x10, 0xA0, 0x08]) if x else bytes([0xA2, 0x10, 0x00, 0xA0, 0x08, 0x00]))   # ldx #$10 ; ldy #$08
    c += (bytes([0xA9, 0x00]) if m else bytes([0xA9, 0x00, 0x00]))
    return bytes(c)

def memory_init():
    mem = {}
    def w16(a, v): mem[a] = v & 0xFF; mem[a + 1] = v >> 8
    w16(0x20 + 0x10, 0x3000)       # (dp,X) with X=0x10 -> pointer at $30 .. we use operand $20 + X
    w16(0x28, 0x3000)              # (dp), (dp),Y
    for a, v in ((0x2C, 0x00), (0x2D, 0x30), (0x2E, 0x7F)): mem[a] = v      # [dp]
    w16(0x1802, 0x3000)            # (sr,S),Y  (S = 1800 -> sr 02 = 1802)
    w16(0x30, 0x0E30) if False else None
    w16(0x1803, 0x3000) if False else None
    return mem

def build(tmpl_bytes, k, n, m, x):
    code = bytearray(setup_code(m, x))
    for _ in range(R):
        tb = bytearray(tmpl_bytes)
        if tb[:1] == b"\x4c" and len(tb) == 3: tgt = len(code) + 3; tb[1] = tgt & 0xFF; tb[2] = tgt >> 8       # JMP abs to the next instruction
        code += tb
    code += bytes([0x20, CTL_ADDR & 0xFF, CTL_ADDR >> 8])               # jsr ctl
    # loop back: jmp to body start (after setup)
    start = len(setup_code(m, x))
    code += bytes([0x4C, start & 0xFF, start >> 8])
    return bytes(code)

class Rig:
    def __init__(self):
        self.e = H.Snes9x(D.ROM); self.e.load_ra_state(D.STATE); self.e.run(3)
        self.st0 = bytearray(self.e.serialize()); self.w = self.e.wram
        self.sim = BattleSim(D.ROM)
        self.ram0 = bytearray(D.blocks(bytes(self.st0))["RAM"][:0x20000])
    def run(self, code, n, m, x, nmax=600):
        ram = bytearray(self.ram0)
        ram[0x1F00] = 0x40
        ram[0x10000:0x10000 + len(code)] = code
        c = ctl_code(n); ram[0x10000 + CTL_ADDR:0x10000 + CTL_ADDR + len(c)] = c
        for a in (0x10FE0, 0x10FE1, 0x10FF0): ram[a] = 0
        for a, v in memory_init().items(): ram[a] = v
        for a in range(0x3000, 0x3100): ram[0x10000 + a] = 0x11
        ram[0x10E00] = 0x60; ram[0x10E10] = 0x6B; ram[0x10E30] = 0x60                  # rts / rtl stubs
        ram[0x10E40] = 0x30; ram[0x10E41] = 0x0E                                         # jmp (abs) vector -> 0e30 (rts)... see below
        ram[0x10E40:0x10E42] = bytes([0x00, 0x0E])
        ram[0x10E30 + 0x10:0x10E30 + 0x12] = bytes([0x00, 0x0E])                          # (abs,X) with X=0x10 -> vector at 0e40 -> 0e00
        st = bytearray(self.st0)
        o = bytes(st).find(b"RAM:") + 11; st[o:o + 0x20000] = ram
        o = bytes(st).find(b"REG:") + 11
        st[o:o + 16] = struct.pack(">BBHHHHHHH", 0x7F, 0x7F, 0x0034 | 0x30, 0, 0, 0x1FFF, 0, 0, 0x0000)
        self.e.unserialize(bytes(st)); w = self.w
        fs = None
        for f in range(nmax):
            self.e.run(1)
            if w[0x10FF0]: fs = f + 1; break
        sim = self.sim; sim.load_wram(bytes(ram)); mm = sim.m
        for r, v in ((cpu.R_PB, 0x7F), (cpu.R_DB, 0x7F), (cpu.R_P, 0x34 | 0x30), (cpu.R_E, 0), (cpu.R_A, 0), (cpu.R_D, 0), (cpu.R_S, 0x1FFF),
                     (cpu.R_X, 0), (cpu.R_Y, 0), (cpu.R_PC, 0), (cpu.R_NMITIMEN, 0x01), (cpu.R_FRAMECYC, 306900 - 8), (cpu.R_MEMSEL, 0)): mm.setreg(r, v)
        sim.running = True; sim.step(1)
        ss = None
        for f in range(nmax):
            sim.step(1)
            if sim.w[0x10FF0]: ss = f + 1; break
        return fs, ss

def main():
    modes = [a for a in sys.argv[1:] if a.startswith("m")] or ["m1x1", "m1x0", "m0x1", "m0x0"]
    verbose = "-v" in sys.argv
    rig = Rig()
    for mode in modes:
        m, x = int(mode[1]), int(mode[3])
        n = 3000
        base = rig.run(build(bytes([0xEA]), 1, n, m, x), n, m, x)
        print(f"== {mode}: NOP baseline snes9x {base[0]} sim {base[1]} frames")
        b_err = (base[0] - base[1]) * FRAME / n
        for name, tb, k in templates(m, x):
            fs, ss = rig.run(build(tb, k, n, m, x), n, m, x)
            if fs is None or ss is None: print(f"  {name:28s} no completion {fs} {ss}"); continue
            err = ((fs - ss) * FRAME / n - b_err) / (R * k)
            flag = "  <<<" if abs(err) > 2.5 else ""
            if verbose or flag: print(f"  {name:28s} snes9x {fs:4d} sim {ss:4d}  err/instr {err:+6.1f} master cycles{flag}")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
