"""HDMA cost per scanline: snes9x vs BattleSim for game-like indirect tables (7F tables, ROM untouched)."""
import sys, os
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import microbench as M

def ind_table(off, count, n_entries, ptr=0xA000):
    t = {}; o = off
    for _ in range(n_entries):
        t[o] = count; t[o + 1] = ptr & 0xFF; t[o + 2] = ptr >> 8; o += 3
    t[o] = 0
    return t
def dir_table(off, count, n_entries, nbytes):
    t = {}; o = off
    for _ in range(n_entries):
        t[o] = count; o += 1 + (nbytes if count & 0x80 == 0 else nbytes * (count & 0x7F))
    t[o] = 0
    return t

def run(label, chans, tabs, n=60000):
    code = M.asm_loop(M.rep(bytes([0xE8])), n, hdma=M.hdma_setup(chans))
    fs, ss = M.bench(code, extra_wram={0x10000 + a: v for a, v in tabs.items()}, nmax=500)
    if fs is None or ss is None: print(f"{label:44s} no completion {fs} {ss}"); return
    print(f"{label:44s} snes9x {fs:4d} sim {ss:4d}   extra cycles/frame real-sim {(fs - ss) * 357368 / ss:8.0f}  ({(fs-ss)*357368/ss/225:5.1f}/line)", flush=True)

if __name__ == "__main__":
    for cnt, ne, nm in ((0x87, 32, "0x87 x32"), (0x81, 224, "0x81 x224"), (0x07, 32, "0x07 x32"), (0x01, 224, "0x01 x224"), (0xFF, 2, "0xFF x2")):
        for mode in (0, 1, 2, 3):
            t = ind_table(0x8000, cnt, ne)
            run(f"1ch indirect mode{mode} {nm}", [(1, mode, 0x8000, True)], t)
    t = {**ind_table(0x8000, 0x87, 32), **ind_table(0x8400, 0x87, 32), **ind_table(0x8800, 0x87, 32)}
    run("3ch indirect (1:m1, 5:m2, 6:m2) 0x87", [(1, 1, 0x8000, True), (5, 2, 0x8400, True), (6, 2, 0x8800, True)], t)
