"""Import the SPC700 state out of a snes9x save state ('SND' block: 64 KB SPC RAM, then snes9x's own register/timer/DSP records).
Layout of the 1024-byte tail (little-endian int32 fields): +0x04 PSW, +0x0C PC, +0x10 SP, +0x14 A, +0x18 X, +0x1C Y(0),
+0x54/+0x68/+0x7C = timer 0/1/2 target, followed by the prescaler phase (cycles into the 128/16 cycle period), the 8-bit stage
counter and the 4-bit output counter; the 128 DSP registers are at +0xA4.  Fields were identified by comparing against the
driver source (ff5-spc.asm) and checked by running the sim's SPC from them (validate/diff_battle.py --spc)."""
import struct

def parse(snd):
    ram = snd[:65536]; t = snd[65536:]
    d = struct.unpack("<%di" % (len(t) // 4), t[:len(t) // 4 * 4])
    dsp = t[0xA4:0xA4 + 128]
    tg = [d[21], d[26], d[31]]; st0 = d[22]
    stage = [d[23], d[28], d[33]]; cnt = [d[24], d[29], d[34]]
    # ports: the CPU->SPC latches are idle (0) between commands; the SPC->CPU latches mirror $F4-$F7
    frm = ram[0xF4] | ram[0xF5] << 8 | ram[0xF6] << 16 | ram[0xF7] << 24
    v = [d[3], d[5], d[6], d[7], d[4], d[1], ram[0xF1], ram[0xF2], ram[0xF0]] + tg + cnt + stage + [st0, 0, frm]
    return bytes(ram), bytes(dsp), v
