"""Cycle checks for the stack / call / jump / transfer opcodes opsweep skips (snes9x vs BattleSim, ROM untouched)."""
import sys, os
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import microbench as M

SUB = 0x0800       # WRAM 7F:0800 : RTS ; 7F:0810 : RTL ; 7F:0820: JMP target
def b(*x): return bytes(x)
TESTS = {
 "PHA/PLA":        (b(0x48, 0x68), 1),
 "PHX/PLX (x16)":  (b(0xDA, 0xFA), 1),
 "PHY/PLY (x16)":  (b(0x5A, 0x7A), 1),
 "PHP/PLP":        (b(0x08, 0x28), 1),
 "PHB/PLB":        (b(0x8B, 0xAB), 1),
 "PHD/PLD":        (b(0x0B, 0x2B), 1),
 "PHK/PLA":        (b(0x4B, 0x68), 1),
 "PEA/PLA/PLA":    (b(0xF4, 0x34, 0x12, 0x68, 0x68), 1),
 "PEI(30)/PLA/PLA": (b(0xD4, 0x30, 0x68, 0x68), 1),
 "PER/PLA/PLA":    (b(0x62, 0x00, 0x00, 0x68, 0x68), 1),
 "JSR abs/RTS":    (b(0x20, SUB & 0xFF, SUB >> 8), 1),
 "JSL/RTL":        (b(0x22, 0x10, 0x08, 0x7F), 1),
 "JMP abs":        (b(0x4C, 0x03, 0x00), 0),       # jumps over nothing: target is the next instruction
 "BRA +0":         (b(0x80, 0x00), 0),
 "BRL +0":         (b(0x82, 0x00, 0x00), 0),
 "BEQ taken":      (b(0xA9, 0x00, 0xF0, 0x00), 0),
 "BNE not taken":  (b(0xA9, 0x00, 0xD0, 0x00), 0),
 "REP/SEP":        (b(0xC2, 0x20, 0xE2, 0x20), 0),
 "XBA":            (b(0xEB), 0), "TAX/TXA": (b(0xAA, 0x8A), 0), "TXY/TYX": (b(0x9B, 0xBB), 0), "TCD/TDC": (b(0x5B, 0x7B), 0), "TSC": (b(0x3B), 0),
 "JMP (abs)":      (b(0x6C, 0x30, 0x08), 0),       # 7F:0830 holds a pointer set in setup (bank 0 address!)
 "STZ/INC A":      (b(0x1A, 0x3A), 0),
 "NOP":            (b(0xEA), 0),
}
def main(names):
    base = M.rep(b(0xEA), 100); fs0, ss0 = M.bench(M.asm_loop(base, 4000), nmax=800, extra_wram=extra())
    print("NOP baseline", fs0, ss0)
    for name, (body, _) in TESTS.items():
        if names and not any(n in name for n in names): continue
        reps = max(1, 60 // len(body))
        code = M.asm_loop(body * reps, 4000)
        fs, ss = M.bench(code, nmax=900, extra_wram=extra())
        if fs is None or ss is None: print(f"{name:20s} no completion {fs} {ss}"); continue
        ninst = 4000 * reps
        print(f"{name:20s} snes9x {fs:4d} sim {ss:4d}  err/iteration {(fs - ss) * 357368 / ninst:+7.1f} master cycles", "<<<" if abs((fs - ss) * 357368 / ninst) > 2 else "")
def extra():
    e = {0x10800: 0x60, 0x10810: 0x6B}     # RTS ; RTL
    return e
if __name__ == "__main__": main(sys.argv[1:])
