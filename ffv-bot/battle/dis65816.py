"""Tiny 65C816 disassembler for debugging (tracks REP/SEP linearly; pass m/x flags for the start state)."""
import sys
_OPS = """00 BRK s;01 ORA (dp,X);02 COP #;03 ORA sr,S;04 TSB dp;05 ORA dp;06 ASL dp;07 ORA [dp];08 PHP;09 ORA #M;0A ASL A;0B PHD;0C TSB abs;0D ORA abs;0E ASL abs;0F ORA long;
10 BPL r;11 ORA (dp),Y;12 ORA (dp);13 ORA (sr,S),Y;14 TRB dp;15 ORA dp,X;16 ASL dp,X;17 ORA [dp],Y;18 CLC;19 ORA abs,Y;1A INC A;1B TCS;1C TRB abs;1D ORA abs,X;1E ASL abs,X;1F ORA long,X;
20 JSR abs;21 AND (dp,X);22 JSL long;23 AND sr,S;24 BIT dp;25 AND dp;26 ROL dp;27 AND [dp];28 PLP;29 AND #M;2A ROL A;2B PLD;2C BIT abs;2D AND abs;2E ROL abs;2F AND long;
30 BMI r;31 AND (dp),Y;32 AND (dp);33 AND (sr,S),Y;34 BIT dp,X;35 AND dp,X;36 ROL dp,X;37 AND [dp],Y;38 SEC;39 AND abs,Y;3A DEC A;3B TSC;3C BIT abs,X;3D AND abs,X;3E ROL abs,X;3F AND long,X;
40 RTI;41 EOR (dp,X);42 WDM #;43 EOR sr,S;44 MVP mv;45 EOR dp;46 LSR dp;47 EOR [dp];48 PHA;49 EOR #M;4A LSR A;4B PHK;4C JMP abs;4D EOR abs;4E LSR abs;4F EOR long;
50 BVC r;51 EOR (dp),Y;52 EOR (dp);53 EOR (sr,S),Y;54 MVN mv;55 EOR dp,X;56 LSR dp,X;57 EOR [dp],Y;58 CLI;59 EOR abs,Y;5A PHY;5B TCD;5C JML long;5D EOR abs,X;5E LSR abs,X;5F EOR long,X;
60 RTS;61 ADC (dp,X);62 PER rl;63 ADC sr,S;64 STZ dp;65 ADC dp;66 ROR dp;67 ADC [dp];68 PLA;69 ADC #M;6A ROR A;6B RTL;6C JMP (abs);6D ADC abs;6E ROR abs;6F ADC long;
70 BVS r;71 ADC (dp),Y;72 ADC (dp);73 ADC (sr,S),Y;74 STZ dp,X;75 ADC dp,X;76 ROR dp,X;77 ADC [dp],Y;78 SEI;79 ADC abs,Y;7A PLY;7B TDC;7C JMP (abs,X);7D ADC abs,X;7E ROR abs,X;7F ADC long,X;
80 BRA r;81 STA (dp,X);82 BRL rl;83 STA sr,S;84 STY dp;85 STA dp;86 STX dp;87 STA [dp];88 DEY;89 BIT #M;8A TXA;8B PHB;8C STY abs;8D STA abs;8E STX abs;8F STA long;
90 BCC r;91 STA (dp),Y;92 STA (dp);93 STA (sr,S),Y;94 STY dp,X;95 STA dp,X;96 STX dp,Y;97 STA [dp],Y;98 TYA;99 STA abs,Y;9A TXS;9B TXY;9C STZ abs;9D STA abs,X;9E STZ abs,X;9F STA long,X;
A0 LDY #X;A1 LDA (dp,X);A2 LDX #X;A3 LDA sr,S;A4 LDY dp;A5 LDA dp;A6 LDX dp;A7 LDA [dp];A8 TAY;A9 LDA #M;AA TAX;AB PLB;AC LDY abs;AD LDA abs;AE LDX abs;AF LDA long;
B0 BCS r;B1 LDA (dp),Y;B2 LDA (dp);B3 LDA (sr,S),Y;B4 LDY dp,X;B5 LDA dp,X;B6 LDX dp,Y;B7 LDA [dp],Y;B8 CLV;B9 LDA abs,Y;BA TSX;BB TYX;BC LDY abs,X;BD LDA abs,X;BE LDX abs,Y;BF LDA long,X;
C0 CPY #X;C1 CMP (dp,X);C2 REP #;C3 CMP sr,S;C4 CPY dp;C5 CMP dp;C6 DEC dp;C7 CMP [dp];C8 INY;C9 CMP #M;CA DEX;CB WAI;CC CPY abs;CD CMP abs;CE DEC abs;CF CMP long;
D0 BNE r;D1 CMP (dp),Y;D2 CMP (dp);D3 CMP (sr,S),Y;D4 PEI (dp);D5 CMP dp,X;D6 DEC dp,X;D7 CMP [dp],Y;D8 CLD;D9 CMP abs,Y;DA PHX;DB STP;DC JML [abs];DD CMP abs,X;DE DEC abs,X;DF CMP long,X;
E0 CPX #X;E1 SBC (dp,X);E2 SEP #;E3 SBC sr,S;E4 CPX dp;E5 SBC dp;E6 INC dp;E7 SBC [dp];E8 INX;E9 SBC #M;EA NOP;EB XBA;EC CPX abs;ED SBC abs;EE INC abs;EF SBC long;
F0 BEQ r;F1 SBC (dp),Y;F2 SBC (dp);F3 SBC (sr,S),Y;F4 PEA abs;F5 SBC dp,X;F6 INC dp,X;F7 SBC [dp],Y;F8 SED;F9 SBC abs,Y;FA PLX;FB XCE;FC JSR (abs,X);FD SBC abs,X;FE INC abs,X;FF SBC long,X"""
OPS = {}
for item in _OPS.replace("\n", "").split(";"):
    item = item.strip()
    if item:
        code, rest = item.split(" ", 1); OPS[int(code, 16)] = rest

def dis(rom, off, n, base=0xC00000, m=1, x=1):
    out, o, end = [], off, off + n
    while o < end:
        op = rom[o]; t = OPS[op]; mn, _, mode = t.partition(" ")
        size = 1
        if mode.endswith("#M"): size = 2 if m == 0 else 1; size += 1 if m == 0 else 0
        arg = ""
        def u(k): return int.from_bytes(rom[o + 1:o + 1 + k], "little")
        if mode in ("", "A", "s"): size = 1; arg = ""
        elif mode == "#M": size = 3 if not m else 2; arg = f"#${u(size-1):0{2*(size-1)}X}"
        elif mode == "#X": size = 3 if not x else 2; arg = f"#${u(size-1):0{2*(size-1)}X}"
        elif mode == "#": size = 2; arg = f"#${u(1):02X}"
        elif mode == "r": size = 2; d = rom[o+1]; d -= 256 if d > 127 else 0; arg = f"${(base+o-off+size+d)&0xFFFFFF:06X}" if False else f"{d:+d}"
        elif mode == "rl": size = 3; arg = f"{int.from_bytes(rom[o+1:o+3],'little',signed=True):+d}"
        elif mode == "mv": size = 3; arg = f"${rom[o+2]:02X},${rom[o+1]:02X}"
        elif "long" in mode: size = 4; arg = f"${u(3):06X}" + (",X" if mode.endswith(",X") else "")
        elif "abs" in mode: size = 3; arg = mode.replace("abs", f"${u(2):04X}")
        elif "sr" in mode: size = 2; arg = mode.replace("sr", f"${u(1):02X}")
        elif "dp" in mode: size = 2; arg = mode.replace("dp", f"${u(1):02X}")
        else: size = 1
        out.append((o, rom[o:o+size].hex(" "), f"{mn} {arg}".strip()))
        if mn == "REP": m = 0 if rom[o+1] & 0x20 else m; x = 0 if rom[o+1] & 0x10 else x
        if mn == "SEP": m = 1 if rom[o+1] & 0x20 else m; x = 1 if rom[o+1] & 0x10 else x
        o += size
    return out

if __name__ == "__main__":
    rom = open(sys.argv[1], "rb").read(); off = int(sys.argv[2], 16); n = int(sys.argv[3], 0)
    m = int(sys.argv[4]) if len(sys.argv) > 4 else 1; x = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    for o, b, t in dis(rom, off, n, m=m, x=x): print(f"{o:06X}  {b:<12} {t}")
