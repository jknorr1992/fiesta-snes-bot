#!/usr/bin/env python3
"""Tiny stand-in for RetroArch's UDP command port, for testing without the emulator."""
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 55355))
ram = bytes((i * 7) & 0xFF for i in range(0x20000))
print("fake retroarch up", flush=True)
while True:
    data, addr = s.recvfrom(256)
    p = data.decode().split()
    if p[0] == "READ_CORE_RAM":
        a, n = int(p[1], 16), int(p[2])
        s.sendto(f"READ_CORE_RAM {a:x} {' '.join(f'{b:02x}' for b in ram[a:a+n])}\n".encode(), addr)
    elif p[0] == "READ_CORE_MEMORY":
        a, n = int(p[1], 16), int(p[2])
        # libretro WRAM view: treat addr 0x7e0000 as start of RAM
        o = a - 0x7E0000
        if 0 <= o and o + n <= len(ram):
            s.sendto(f"READ_CORE_MEMORY {a:x} {' '.join(f'{b:02x}' for b in ram[o:o+n])}\n".encode(), addr)
        else:
            s.sendto(f"READ_CORE_MEMORY {a:x} -1 no mapping\n".encode(), addr)
    elif p[0] == "VERSION":
        s.sendto(b"1.19.1\n", addr)
