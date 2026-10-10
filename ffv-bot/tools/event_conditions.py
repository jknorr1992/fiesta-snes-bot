#!/usr/bin/env python3
"""Decode FFV trigger scripts (conditions that gate field events) from the ROM.
Layout (everything8215/ff5 disassembly): event triggers (4 bytes: x, y, trigger-script index) at 0xE2400 + u16 pointer (the pointer table at 0xE2400 itself, relative offsets), trigger scripts at 0x18E080 + u16 pointer (704 items). A script is a list of condition
groups; each group ends with 0xFF + u16 event index, which runs only if every condition before it holds.
Conditions: FE f flag f(0-255) set | FD clear | FC f flag 256+f set | FB clear | FA addr,cmp,val | F9 | F8 | F7 dir | F6 A-button.
Usage: event_conditions.py <rom.sfc> [map_id ...]
"""
import sys

def u16(r, o): return r[o] | r[o + 1] << 8

def trigger_script(rom, idx):
    if idx >= 704:
        return []
    a, b = u16(rom, 0x18E080 + 2 * idx), u16(rom, 0x18E080 + 2 * (idx + 1))
    data = rom[0x18E080 + a:0x18E080 + b]
    groups, conds, i = [], [], 0
    while i < len(data):
        op = data[i]
        if op == 0xFF:
            if i + 2 >= len(data):
                break
            groups.append((conds, data[i + 1] | data[i + 2] << 8)); conds = []; i += 3
        elif op in (0xFE, 0xFD, 0xFC, 0xFB):
            f = data[i + 1] + (256 if op in (0xFC, 0xFB) else 0)
            conds.append(("flag_set" if op in (0xFE, 0xFC) else "flag_clear", f)); i += 2
        elif op in (0xFA, 0xF9, 0xF8, 0xF5):
            n = 4 if op in (0xFA, 0xF9) else 4
            conds.append(("ram", op, data[i + 1:i + n + 1].hex())); i += n + 1 if op in (0xF8, 0xF5) else n
        elif op == 0xF7:
            conds.append(("facing", data[i + 1])); i += 2
        elif op == 0xF6:
            conds.append(("button_a",)); i += 1
        else:
            conds.append(("?", op)); i += 1
    return groups

def triggers(rom, m):
    a, b = u16(rom, 0xE2400 + 2 * m), u16(rom, 0xE2400 + 2 * (m + 1))  # pointers are relative to the table start
    return [(rom[0xE2400 + i], rom[0xE2400 + i + 1], u16(rom, 0xE2400 + i + 2)) for i in range(a, b, 4)]

if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    for m in (int(x) for x in sys.argv[2:]):
        print("map", m)
        for x, y, t in triggers(rom, m):
            print("  trigger at (%d,%d) script %d:" % (x, y, t))
            for conds, ev in trigger_script(rom, t):
                print("     if", conds or "always", "-> event", ev)
