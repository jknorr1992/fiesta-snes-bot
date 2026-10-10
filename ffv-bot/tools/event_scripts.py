#!/usr/bin/env python3
"""Scan FFV event scripts for flag sets/clears. Script pointers: 3-byte absolute at 0x83320 (1940 items), data from 0x849DC.
Command lengths come from the IncEventPtrN calls in the everything8215/ff5 disassembly; commands that branch or are
variable are assumed 1 byte and flagged by the end-of-script check (a parse that does not end exactly on the
script's end is reported as unreliable).
Usage: event_scripts.py <rom.sfc> [event ...]
"""
import sys

LEN = {}
for op in range(0x00, 0xA0): LEN[op] = 1
for op in range(0xA0, 0xB7): LEN[op] = 2
LEN.update({0xB7: 2, 0xB8: 2, 0xB9: 2, 0xBA: 3, 0xBB: 3, 0xBC: 3, 0xBD: 3, 0xBE: 2, 0xBF: 2, 0xC0: 2, 0xC1: 2, 0xC2: 2,
            0xC3: 2, 0xC4: 2, 0xC5: 2, 0xC6: 2, 0xC8: 3, 0xC9: 3, 0xCA: 3, 0xCB: 3, 0xD0: 3, 0xD1: 4, 0xD2: 5, 0xD3: 4, 0xD4: 4,
            0xD5: 4, 0xD7: 4, 0xD8: 3, 0xD9: 3, 0xDA: 4, 0xDB: 1, 0xDC: 2, 0xDD: 4, 0xE4: 1, 0xE5: 1, 0xE6: 1, 0xE8: 3, 0xE9: 2,
            0xEA: 1, 0xEB: 1, 0xFF: 1})
SET0, CLR0, SET1, CLR1 = 0xA2, 0xA3, 0xA4, 0xA5

def script(rom, idx):
    p = 0x83320 + 3 * idx
    a = int.from_bytes(rom[p:p + 3], "little")
    b = int.from_bytes(rom[p + 3:p + 6], "little")
    return rom[a & 0x3FFFFF if a < 0x400000 else a - 0xC00000: b & 0x3FFFFF if b < 0x400000 else b - 0xC00000]

def scan(rom, idx):
    s = script(rom, idx)
    i, sets, clears = 0, [], []
    while i < len(s):
        op = s[i]
        if op in (SET0, SET1) and i + 1 < len(s):
            sets.append(s[i + 1] + (256 if op == SET1 else 0))
        elif op == 0xCA and i + 2 < len(s):  # set npc flag (bit 512 + n)
            sets.append(512 + (s[i + 1] | s[i + 2] << 8))
        elif op == 0xCB and i + 2 < len(s):  # clear npc flag
            clears.append(512 + (s[i + 1] | s[i + 2] << 8))
        elif op in (CLR0, CLR1) and i + 1 < len(s):
            clears.append(s[i + 1] + (256 if op == CLR1 else 0))
        i += LEN.get(op, 1)
    return sets, clears, i == len(s)

if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    if len(sys.argv) > 2:
        for e in map(int, sys.argv[2:]):
            print(e, scan(rom, e))
    else:
        ok = sum(scan(rom, e)[2] for e in range(1940))
        print("scripts parsed exactly to their end:", ok, "of 1940")
