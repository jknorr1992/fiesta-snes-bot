#!/usr/bin/env python3
"""Write a copy of the FF5r Clean ROM with the Ancient Library crash fixed (never overwrites the original).

The patched ROM has 0xA9 at file offset 0x24C75 (SNES C2:4C75) where the battle code needs 0xE8 (INX) in the monster
blue-magic "learning" loop; as shipped it runs `LDA #$E0 / PHP / BRK` and the BRK vector ($0000) executes WRAM as code.
Usage: fix_rom.py <in.sfc> [out.sfc]   (default out: <in>-bluefix.sfc)"""
import sys
FIXES = [(0x24C75, 0xA9, 0xE8)]
def fixed(rom):
    """Return a copy of ROM bytes with FIXES applied (raises on an unknown ROM)."""
    d = bytearray(rom)
    for off, old, new in FIXES:
        if d[off] not in (old, new): raise ValueError(f"{off:#x} is {d[off]:#04x}, expected {old:#04x}")
        d[off] = new
    return bytes(d)

def main():
    src = sys.argv[1]; dst = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + "-bluefix.sfc"
    d = bytearray(open(src, "rb").read())
    for off, old, new in FIXES:
        if d[off] == new: print(f"{off:#x} already {new:#04x}")
        elif d[off] != old: sys.exit(f"{off:#x} is {d[off]:#04x}, expected {old:#04x}: unknown ROM, not patching")
        else: d[off] = new; print(f"{off:#x}: {old:#04x} -> {new:#04x}")
    open(dst, "wb").write(d); print("wrote", dst)
if __name__ == "__main__": main()
