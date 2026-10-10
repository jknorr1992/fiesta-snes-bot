#!/usr/bin/env python3
"""Static model of the FFV event system, built from the everything8215/ff5 disassembly (src/field/field-main.asm).

ROM tables (file offsets, no header):
  event scripts      3-byte pointers at 0x83320 (1940 events), data from 0x849DC
  trigger scripts    0x18E080 (u16 pointers relative to it): groups of conditions ending FF + u16 event; the FIRST group whose
                     conditions all hold runs (ExecTriggerScript), then the script stops
  event triggers     0xE2400 (u16 pointers relative to it, per map), 4 bytes: x, y, trigger script
  entrance triggers  0xE36C0 (u16 pointers relative to it, per map), 6 bytes: x, y, dest map (u16: low 9 bits map, high bits flags),
                     dest x, dest y
  NPC properties     0xE59C0 (pointers relative), 7 bytes: script(u16, low 14 bits), graphics, x, y, flags, palette; an NPC is
                     visible while NPC flag <script> is set (WRAM $0A54...)
  NPC scripts        0xE0000 (pointers relative): condition groups like trigger scripts
  initial NPC flags  0x18E000 (128 bytes)
Event commands (all lengths include the opcode): see LEN. $cd = jump to event u16 (returns on $ff), $e0/$e1/$e3 load a map
(map = u16 & 0x3FF, x = b3 & 0x3F, y = b4), $d6 changes vehicle (world map), $a2-$a5 set/clear event flags, $ca/$cb set/clear NPC
flag, $b7 add/remove character, $bd/$e2 event battle, $f0 yes/no dialog (next 4-byte command runs on 'yes', skipped on 'no'),
$f1 random jump, $bf subcommands (00 game over, 01 load parent map, 0f epilogue = the ending).
"""
import sys

def u16(r, o): return r[o] | r[o + 1] << 8

LEN = {}
for op in range(0x00, 0xA0): LEN[op] = 1
for op in range(0xA0, 0xB7): LEN[op] = 2
LEN.update({0xA0: 3, 0xA1: 2, 0xB7: 2, 0xB8: 2, 0xB9: 2, 0xBA: 3, 0xBB: 3, 0xBC: 3, 0xBD: 3, 0xBE: 2, 0xBF: 2,
            0xC0: 2, 0xC1: 2, 0xC2: 2, 0xC3: 2, 0xC4: 2, 0xC5: 2, 0xC6: 2, 0xC7: 2, 0xC8: 3, 0xC9: 3, 0xCA: 3, 0xCB: 3,
            0xCD: 3, 0xCE: 3, 0xCF: 3, 0xD0: 3, 0xD1: 4, 0xD2: 5, 0xD3: 4, 0xD4: 4, 0xD5: 4, 0xD6: 4, 0xD7: 4, 0xD8: 3,
            0xD9: 3, 0xDA: 4, 0xDB: 1, 0xDC: 2, 0xDD: 4, 0xE0: 6, 0xE1: 6, 0xE2: 3, 0xE3: 6, 0xE4: 1, 0xE5: 1, 0xE6: 1,
            0xE7: 1, 0xE8: 3, 0xE9: 2, 0xEA: 1, 0xEB: 1, 0xF0: 3, 0xF1: 2, 0xF3: 6, 0xF4: 6, 0xFF: 1})

class Rom:
    def __init__(self, path):
        self.r = open(path, "rb").read()
        self.n_events = 1940

    def event_bytes(self, idx):
        r = self.r
        p = 0x83320 + 3 * idx
        a = int.from_bytes(r[p:p + 3], "little")
        b = int.from_bytes(r[p + 3:p + 6], "little")
        a = a - 0xC00000 if a >= 0xC00000 else a
        b = b - 0xC00000 if b >= 0xC00000 else b
        return r[a:b]

    @__import__('functools').lru_cache(maxsize=None)
    def event_ops(self, idx):
        """List of (op, args dict) in script order."""
        s = self.event_bytes(idx)
        ops, i = [], 0
        while i < len(s):
            op = s[i]
            n = LEN.get(op, 1)
            if op in (0xF3, 0xF4) and i + 3 < len(s):  # change background: 4 header bytes + (w * h) tile bytes
                n = 4 + ((s[i + 3] & 15) + 1) * ((s[i + 3] >> 4) + 1)
            elif op == 0xE2:
                n = 7  # event battle (can lose): the next 4 bytes run only if the battle was lost
            elif op == 0xF0:
                n = 7  # yes/no dialog: the next 4 bytes run only on 'yes'
            elif op == 0xF1 and i + 1 < len(s):
                n = 2 + 4 * ((s[i + 1]) + 1)
            a = s[i + 1:i + n]
            d = {}
            if op in (0xA2, 0xA4, 0xA3, 0xA5) and a:
                d["flag"] = a[0] + (256 if op in (0xA4, 0xA5) else 0)
            elif op in (0xCA, 0xCB) and len(a) >= 2:
                d["npc_flag"] = u16(a, 0)
            elif op in (0xE0, 0xE1, 0xE3) and len(a) >= 5:
                d["map"] = (a[0] | (a[1] & 3) << 8)
                d["parent"] = bool(a[1] & 0x40); d["title"] = bool(a[1] & 0x80)
                d["x"] = a[2] & 0x3F; d["y"] = a[3]; d["facing"] = a[2] >> 6; d["vehicle"] = a[4] >> 5
                d["world_x"] = a[2]  # on world maps (map < 5) the whole byte is x
            elif op == 0xD6 and len(a) >= 3:
                d["map"] = u16(a, 0) & 0x1FFF; d["vehicle"] = a[2] >> 5
            elif op == 0xD2 and len(a) >= 4:  # show vehicle: map, x, y, vvvaaagg
                d["map"] = a[0]; d["x"] = a[1]; d["y"] = a[2]; d["vehicle"] = a[3] >> 5
            elif op == 0xC2 and a:  # hide vehicle
                d["vehicle"] = a[0]
            elif op == 0xCD and len(a) >= 2:
                d["event"] = u16(a, 0)
            elif op in (0xBD, 0xE2) and a:
                d["battle"] = a[0] & 0x7F
                if op == 0xE2: d["lose"] = bytes(a[2:6]).hex()
            elif op == 0xB7 and a:
                d["char"] = a[0] & 7; d["remove"] = bool(a[0] & 0x80)
            elif op in (0xC8, 0xF0) and len(a) >= 2:
                d["dialog"] = u16(a, 0)
                if op == 0xF0: d["yes"] = bytes(a[2:6]).hex()
            elif op in (0xAA, 0xAB, 0xAC, 0xC6) and a:
                d["id"] = a[0]
            elif op in (0xAF, 0xB0) and len(a) >= 2:
                d["gp"] = u16(a, 0)
            elif op == 0xBF and a:
                d["sub"] = a[0]
            ops.append((op, d))
            i += n
        return ops

    # --- trigger-style condition scripts (event triggers, NPC scripts) ---
    @staticmethod
    def _groups(data):
        groups, conds, i = [], [], 0
        while i < len(data):
            op = data[i]
            if op == 0xFF:
                if i + 2 >= len(data) + 0 and i + 2 > len(data) - 1 + 1:
                    break
                groups.append((conds, u16(data, i + 1))); conds = []; i += 3
            elif op in (0xFE, 0xFD, 0xFC, 0xFB):
                conds.append(("set" if op in (0xFE, 0xFC) else "clear", data[i + 1] + (256 if op in (0xFC, 0xFB) else 0))); i += 2
            elif op in (0xFA, 0xF9):
                conds.append(("ram", op, data[i + 1:i + 4].hex())); i += 4
            elif op in (0xF8, 0xF5):
                conds.append(("ram", op, data[i + 1:i + 4].hex())); i += 4
            elif op == 0xF7:
                conds.append(("facing", data[i + 1])); i += 2
            elif op == 0xF6:
                conds.append(("button",)); i += 1
            elif op == 0xF0:
                break
            else:
                conds.append(("?", op)); i += 1
        return groups

    def trigger_script(self, idx):
        if idx >= 704: return []
        r = self.r
        a, b = u16(r, 0x18E080 + 2 * idx), u16(r, 0x18E080 + 2 * (idx + 1))
        return self._groups(r[0x18E080 + a:0x18E080 + b])

    def npc_script(self, idx):
        r = self.r
        a, b = u16(r, 0xE0000 + 2 * idx), u16(r, 0xE0000 + 2 * (idx + 1))
        return self._groups(r[0xE0000 + a:0xE0000 + b])

    def triggers(self, m):
        r = self.r
        a, b = u16(r, 0xE2400 + 2 * m), u16(r, 0xE2400 + 2 * (m + 1))
        return [(r[0xE2400 + i], r[0xE2400 + i + 1], u16(r, 0xE2400 + i + 2)) for i in range(a, b, 4)]

    def entrances(self, m):
        r = self.r
        a, b = u16(r, 0xE36C0 + 2 * m), u16(r, 0xE36C0 + 2 * (m + 1))
        out = []
        for i in range(a, b, 6):
            o = 0xE36C0 + i
            w = u16(r, o + 2)
            out.append({"x": r[o], "y": r[o + 1], "map": w & 0x1FF, "flags": w >> 9, "dx": r[o + 4], "dy": r[o + 5]})
        return out

    def npcs(self, m):
        r = self.r
        a, b = u16(r, 0xE59C0 + 2 * m), u16(r, 0xE59C0 + 2 * (m + 1))
        return [{"script": u16(r, 0xE59C0 + i) & 0x3FFF, "gfx": r[0xE59C0 + i + 2], "x": r[0xE59C0 + i + 3] & 0x3F,
                 "y": r[0xE59C0 + i + 4] & 0x3F} for i in range(a, b, 7)]

    def initial_npc_flags(self):
        bits = 0
        for i in range(1024):
            if self.r[0x18E000 + (i >> 3)] >> (i & 7) & 1:
                bits |= 1 << i
        return bits

if __name__ == "__main__":
    rom = Rom(sys.argv[1])
    for e in map(int, sys.argv[2:]):
        print(e, rom.event_ops(e))
