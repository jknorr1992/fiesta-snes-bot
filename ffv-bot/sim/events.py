"""Exact FFV event-script decoder, derived from everything8215/ff5 src/field/field-main.asm (ExecEvent, EventCmdTbl, IncEventPtrN).

Event scripts are at file offset 0x83320 (3-byte pointers, 1940 events + end marker), data from 0x849DC.
Every length below includes the opcode byte (each handler ends in IncEventPtrN / AddEventPtr(n)).
"""
import functools

def u16(r, o): return r[o] | r[o + 1] << 8

def _lens():
    L = {}
    for op in range(0x00, 0x80): L[op] = 1          # 00-04 camera/party step, 05-6f party action, 70-7f wait/misc
    for op in range(0x80, 0xA0): L[op] = 2          # object + action (move/hide/show/jump/...)
    for op in range(0xA0, 0xC0): L[op] = 2          # most 0xA0-0xBF
    L.update({0xBA: 3, 0xBB: 3, 0xBC: 3, 0xBD: 3})  # status / event battle
    L.update({0xC0: 2, 0xC1: 2, 0xC2: 2, 0xC3: 2, 0xC4: 2, 0xC5: 2, 0xC6: 2})
    L.update({0xC7: 2, 0xC8: 3, 0xC9: 3, 0xCA: 3, 0xCB: 3, 0xCD: 3, 0xCE: 3, 0xCF: 3})
    L.update({0xD0: 3, 0xD1: 4, 0xD2: 5, 0xD3: 4, 0xD4: 4, 0xD5: 4, 0xD6: 4, 0xD7: 4, 0xD8: 3, 0xD9: 3, 0xDA: 4,
              0xDB: 1, 0xDC: 2, 0xDD: 4})
    L.update({0xE0: 6, 0xE1: 6, 0xE2: 3, 0xE3: 6, 0xE4: 1, 0xE5: 1, 0xE6: 1, 0xE7: 1, 0xE8: 3, 0xE9: 2, 0xEA: 1, 0xEB: 1})
    L.update({0xF0: 3, 0xF1: 2, 0xFF: 1})
    return L
LEN = _lens()

class Cmd:
    __slots__ = ("off", "op", "b", "n")
    def __init__(self, off, op, b, n): self.off, self.op, self.b, self.n = off, op, b, n   # b = raw bytes incl. opcode
    def __repr__(self): return "%04x:%s" % (self.off, self.b.hex())

def cmd_len(s, i):
    op = s[i]
    if op in (0xF3, 0xF4): return 4 + ((s[i + 3] & 15) + 1) * ((s[i + 3] >> 4) + 1)
    return LEN.get(op)

class EventRom:
    """Event script access over a ROM image (bytes, headerless)."""
    N = 1940
    def __init__(self, rom): self.r = rom

    def bounds(self, idx):
        r = self.r
        a = int.from_bytes(r[0x83320 + 3 * idx:0x83320 + 3 * idx + 3], "little") & 0x3FFFFF
        b = int.from_bytes(r[0x83320 + 3 * idx + 3:0x83320 + 3 * idx + 6], "little") & 0x3FFFFF
        return a, b

    @functools.lru_cache(maxsize=None)
    def cmds(self, idx):
        a, b = self.bounds(idx)
        s = self.r[a:b]
        out, i = [], 0
        while i < len(s):
            n = cmd_len(s, i)
            if n is None:
                if idx == EventRom.N - 1: break      # last pointer has no real end bound
                raise ValueError("event %d: unknown opcode %02x at +%d" % (idx, s[i], i))
            out.append(Cmd(i, s[i], bytes(s[i:i + n]), n)); i += n
        return out

if __name__ == "__main__":
    import sys
    rom = open(sys.argv[1], "rb").read()
    er = EventRom(rom)
    bad = 0
    for e in range(er.N):
        try:
            c = er.cmds(e)
            a, b = er.bounds(e)
            ok = (not c) or c[-1].op == 0xFF
            # conditional follow-ups of f0/e2 must be 4-byte commands
            for k, x in enumerate(c[:-1]):
                if x.op in (0xF0, 0xE2) and c[k + 1].n != 4: ok = False
            if not ok: bad += 1; print("event", e, "suspect", c[-3:])
        except Exception as ex:
            bad += 1; print("event", e, ex)
    print("bad events:", bad, "of", er.N)
