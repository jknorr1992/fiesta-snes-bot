#!/usr/bin/env python3
"""Decode FFV NPCs and their interaction scripts. NPC properties: u16 pointers at 0xE59C0 (relative to 0xE59C0), 7 bytes
each (script, ?, graphics, x, y, flags, palette) at 0xE5DC2... NPC scripts: u16 pointers at 0xE0000 (relative to
0xE0000); a script is condition groups (same opcodes as trigger scripts, lengths from NPCCmdNumBytes) ending in 0xF0,
each group closed by FF + u16 event index. Usage: npc_scripts.py <rom.sfc> <map_id ...>"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from event_scripts import scan

NUM = {0xF5: 4, 0xF6: 1, 0xF7: 2, 0xF8: 4, 0xF9: 5, 0xFA: 4, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 2, 0xFF: 3}

def u16(r, o): return r[o] | r[o + 1] << 8

def npcs(rom, m):
    a, b = u16(rom, 0xE59C0 + 2 * m), u16(rom, 0xE59C0 + 2 * (m + 1))
    return [rom[0xE59C0 + i:0xE59C0 + i + 7] for i in range(a, b, 7)]

def npc_script(rom, idx):
    a, b = u16(rom, 0xE0000 + 2 * idx), u16(rom, 0xE0000 + 2 * (idx + 1))
    d = rom[0xE0000 + a:0xE0000 + b]
    groups, conds, i = [], [], 0
    while i < len(d) and d[i] != 0xF0:
        op = d[i]
        if op == 0xFF:
            groups.append((conds, d[i + 1] | d[i + 2] << 8)); conds = []
        elif op in (0xFE, 0xFD, 0xFC, 0xFB):
            conds.append(("flag_set" if op in (0xFE, 0xFC) else "flag_clear", d[i + 1] + (256 if op in (0xFC, 0xFB) else 0)))
        elif op in NUM:
            conds.append(("other", hex(op)))
        else:
            break
        i += NUM.get(op, 1)
    return groups

if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read()
    for m in map(int, sys.argv[2:]):
        print("map", m)
        for n in npcs(rom, m):
            print(" npc script %d at (%d,%d) gfx %d" % (n[0], n[3] & 0x3F, n[4] & 0x3F, n[2]))
            for conds, ev in npc_script(rom, n[0]):
                sets, clears, ok = scan(rom, ev) if ev < 1940 else ([], [], False)
                print("    if", conds or "always", "-> event", ev, "sets", sets, "clears", clears, "" if ok else "(script unreliable)")
