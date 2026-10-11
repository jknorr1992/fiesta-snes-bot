"""Measure how long one ExecSound call (C4:0004) really takes in snes9x: H/V counter latch before/after, NMI frame counter."""
import sys, os, struct, random
here = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [here, os.path.join(here, "..")]
import libretro_host as H, diff_battle as D

NMI_CODE = bytes([0x08, 0xC2, 0x20, 0x48, 0xAF, 0xF8, 0x0F, 0x7F, 0x1A, 0x8F, 0xF8, 0x0F, 0x7F, 0x68, 0x28, 0x40])
def sta7f(addr): return bytes([0x8F, addr & 0xFF, addr >> 8, 0x7F])
def lda_long(a): return bytes([0xAF, a & 0xFF, (a >> 8) & 0xFF, a >> 16])
def read_time(base):
    c = lda_long(0x002137)                       # latch
    for i, reg in enumerate((0x213C, 0x213C, 0x213D, 0x213D)): c += lda_long(reg) + sta7f(base + i)
    c += lda_long(0x7F0FF8) + sta7f(base + 4) + lda_long(0x7F0FF9) + sta7f(base + 5)
    return c
def build(trials):
    c = bytearray([0xC2, 0x10, 0xE2, 0x20])
    c += bytes([0xA9, 0x00, 0x8F, 0x0C, 0x42, 0x00])        # HDMA off
    c += bytes([0xA9, 0x81, 0x8F, 0x00, 0x42, 0x00])        # NMI on
    for i, (cmd, arg, delay) in enumerate(trials):
        c += bytes([0xA2, delay & 0xFF, delay >> 8, 0xCA, 0xD0, 0xFD])        # LDX #delay ; DEX ; BNE -3
        c += bytes([0xA9, cmd, 0x8F, 0x00, 0x1D, 0x7E, 0xA9, arg, 0x8F, 0x01, 0x1D, 0x7E])
        c += read_time(0x1000 + 16 * i)
        c += bytes([0x22, 0x04, 0x00, 0xC4])
        c += read_time(0x1008 + 16 * i)
    c += bytes([0xA9, 0x01]) + sta7f(0x0FF0)
    here_ = len(c); c += bytes([0x4C, here_ & 0xFF, here_ >> 8])
    return bytes(c)

def measure(trials):
    code = build(trials)
    e = H.Snes9x(D.ROM); e.load_ra_state(D.STATE); e.run(3); w = e.wram
    st = bytearray(e.serialize())
    o = bytes(st).find(b"RAM:") + 11; ram = bytearray(st[o:o + 0x20000])
    ram[0x1F00:0x1F00 + len(NMI_CODE)] = NMI_CODE; ram[0x10000:0x10000 + len(code)] = code
    for a in range(0x10FF0, 0x10FFA): ram[a] = 0
    for a in range(0x11000, 0x11000 + 16 * len(trials)): ram[a] = 0
    st[o:o + 0x20000] = ram
    o = bytes(st).find(b"REG:") + 11
    st[o:o + 16] = struct.pack(">BBHHHHHHH", 0x7F, 0x7F, 0x0034 | 0x30, 0, 0, 0x1FFF, 0, 0, 0)
    e.unserialize(bytes(st))
    for f in range(4000):
        e.run(1)
        if w[0x10FF0]: break
    res = []
    for i in range(len(trials)):
        def t(base):
            hl, hh, vl, vh, f0, f1 = (w[0x11000 + base + k] for k in range(6))
            hh &= 1; vh &= 1; h = hl | hh << 8; v = vl | vh << 8; fc = f0 | f1 << 8
            return fc * 357368 + ((v - 225) % 262) * 1364 + h * 4
        res.append(t(16 * i + 8) - t(16 * i))
    return res

if __name__ == "__main__":
    random.seed(1)
    for cmd, arg in ((0, 0), (2, 1), (2, 14), (0x71, 0), (0xFC, 0), (0x7F, 0), (0x70, 59), (0x75, 1)):
        trials = [(cmd, arg, random.randint(1, 20000)) for _ in range(24)]
        r = measure(trials)
        print(f"cmd {cmd:#04x} arg {arg:3d}: n={len(r)} min {min(r)} max {max(r)} mean {sum(r)/len(r):.0f} (frames {sum(r)/len(r)/357368:.3f}); first 8: {r[:8]}")
