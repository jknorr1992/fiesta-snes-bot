#!/usr/bin/env python3
"""English map titles of the patched ROM. Titles are byte strings in a block starting at the 4-byte 0xA2 filler before 'Crystal Room';
the (shared) Japanese pointer table at 0x107000 gives their offsets, and the map property byte 2 (table 0xE9C00, 26 bytes per map)
is the title index. The English charset is ASCII-like: 0x60-0x79 'A'-'Z', 0x7A-0x93 'a'-'z', 0x54-0x5D '0'-'9', 0xFF space."""
import struct, sys

def enc(s):
    out = bytearray()
    for ch in s:
        if ch.isupper(): out.append(0x60 + ord(ch) - 65)
        elif ch.islower(): out.append(0x7A + ord(ch) - 97)
        elif ch.isdigit(): out.append(0x54 + ord(ch) - 48)
        else: out.append(0xFF)
    return bytes(out)

def dec(b):
    s = ""
    for c in b:
        if 0x60 <= c <= 0x79: s += chr(65 + c - 0x60)
        elif 0x7A <= c <= 0x93: s += chr(97 + c - 0x7A)
        elif 0x54 <= c <= 0x5D: s += str(c - 0x54)
        elif c == 0xFF: s += " "
        elif c == 0xC5: s += ">"
        elif c in (0x99, 0xA0): s += "'"
        elif c == 0x96: s += "-"
        elif c == 0: s += ""
        else: s += "?"
    return s.strip()

def load(rom):
    i = rom.find(enc("Crystal Room"))
    base = i - 4
    ptr = [struct.unpack_from("<H", rom, 0x107000 + 2 * k)[0] for k in range(165)]
    titles = [dec(rom[base + ptr[k]:base + ptr[k + 1]]) for k in range(163)]
    names = {}
    for m in range(511):
        t = rom[0xE9C00 + 26 * m + 2]
        names[m] = titles[t] if 0 < t < 163 else ""
    return titles, names

if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    titles, names = load(rom)
    for k, t in enumerate(titles): print(k, t)
