/* SPC700 core + timers + CPU<->SPC ports, for the battle simulator.  Only what the FF5 sound driver needs: no audio output;
 * DSP registers are a plain 128-byte file.  Cycle counts follow the standard SPC700 timing table (snes9x uses the same).
 * Included into cpu65816.c (single translation unit). */
typedef struct {
    uint8_t  ram[65536];
    uint8_t  dsp[128];
    uint16_t pc; uint8_t a, x, y, sp, psw;       /* psw: N V P B H I Z C = 80 40 20 10 08 04 02 01 */
    uint8_t  ctl, dspaddr, test;                  /* $F1, $F2, $F0 */
    uint8_t  to_spc[4], from_spc[4];              /* CPU->SPC ($2140-3 / $F4-7) and back */
    uint8_t  tgt[3], cnt[3], stage[3], en[3];     /* timers: target, 4-bit out counter, internal stage counter, enabled */
    uint64_t cyc;                                 /* SPC cycles executed */
    uint8_t  stopped;
    uint64_t acc;                                 /* leftover (master cycles * 15664) not yet converted to SPC cycles */
    uint64_t synced_master;                       /* master cycle count the SPC has been run up to */
} Spc;

static const uint8_t SPC_IPL[64] = {
 0xCD,0xEF,0xBD,0xE8,0x00,0xC6,0x1D,0xD0,0xFC,0x8F,0xAA,0xF4,0x8F,0xBB,0xF5,0x78,0xCC,0xF4,0xD0,0xFB,0x2F,0x19,0xEB,0xF4,0xD0,0xFC,0x7E,0xF4,0xD0,0x0B,0xE4,0xF5,
 0xCB,0xF4,0xD7,0x00,0xFC,0xD0,0xF3,0xAB,0x01,0x10,0xEF,0x7E,0xF4,0x10,0xEB,0xBA,0xF6,0xDA,0x00,0xBA,0xF4,0xC4,0xF4,0xDD,0x5D,0xD0,0xDB,0x1F,0x00,0x00,0xC0,0xFF };

static void spc_reset(Spc *p) {
    memset(p, 0, sizeof *p);
    p->ctl = 0x80; p->pc = 0xFFC0; p->sp = 0xEF;
    p->tgt[0] = p->tgt[1] = p->tgt[2] = 0;
}

static inline uint8_t spc_rd(Spc *p, uint16_t a) {
    if (a >= 0xF0 && a < 0x100) {
        switch (a) {
        case 0xF0: return p->test; case 0xF1: return 0;
        case 0xF2: return p->dspaddr; case 0xF3: return p->dsp[p->dspaddr & 0x7F];
        case 0xF4: case 0xF5: case 0xF6: case 0xF7: return p->to_spc[a - 0xF4];
        case 0xFA: case 0xFB: case 0xFC: return 0;
        case 0xFD: case 0xFE: case 0xFF: { int t = a - 0xFD; uint8_t v = p->cnt[t]; p->cnt[t] = 0; return v; }
        default: return p->ram[a];
        }
    }
    if (a >= 0xFFC0 && (p->ctl & 0x80)) return SPC_IPL[a - 0xFFC0];
    return p->ram[a];
}
static inline void spc_wr(Spc *p, uint16_t a, uint8_t v) {
    p->ram[a] = v;
    if (a >= 0xF0 && a < 0x100) {
        switch (a) {
        case 0xF0: p->test = v; break;
        case 0xF1:
            for (int t = 0; t < 3; t++) {
                int on = (v >> t) & 1;
                if (on && !p->en[t]) { p->stage[t] = 0; p->cnt[t] = 0; }
                p->en[t] = on;
            }
            if (v & 0x10) { p->to_spc[0] = p->to_spc[1] = 0; }
            if (v & 0x20) { p->to_spc[2] = p->to_spc[3] = 0; }
            p->ctl = v; break;
        case 0xF2: p->dspaddr = v; break;
        case 0xF3: if (!(p->dspaddr & 0x80)) p->dsp[p->dspaddr & 0x7F] = v; break;
        case 0xF4: case 0xF5: case 0xF6: case 0xF7: p->from_spc[a - 0xF4] = v; break;
        case 0xFA: case 0xFB: case 0xFC: p->tgt[a - 0xFA] = v; break;
        }
    }
}

/* timers advance with the global SPC cycle counter: timers 0/1 tick every 128 cycles, timer 2 every 16 */
static void spc_timers(Spc *p, uint64_t old, uint64_t now) {
    for (int t = 0; t < 3; t++) {
        int sh = t == 2 ? 4 : 7;
        uint64_t ticks = (now >> sh) - (old >> sh);
        if (!ticks || !p->en[t]) continue;
        while (ticks--) {
            if (++p->stage[t] == p->tgt[t]) { p->stage[t] = 0; p->cnt[t] = (p->cnt[t] + 1) & 15; }     /* 8-bit stage: target 0 = 256 */
        }
    }
}

#define SF_N 0x80
#define SF_V 0x40
#define SF_P 0x20
#define SF_B 0x10
#define SF_H 0x08
#define SF_I 0x04
#define SF_Z 0x02
#define SF_C 0x01
#define SNZ(v) do { p->psw = (p->psw & ~(SF_N | SF_Z)) | ((v) & 0x80) | (((v) & 0xFF) == 0 ? SF_Z : 0); } while (0)

static inline uint16_t spc_dpaddr(Spc *p, uint8_t d) { return (p->psw & SF_P ? 0x100 : 0) | d; }
static inline uint8_t  spc_f8(Spc *p) { return spc_rd(p, p->pc++); }
static inline uint16_t spc_f16(Spc *p) { uint8_t l = spc_f8(p); return l | (spc_f8(p) << 8); }
static inline void spc_push(Spc *p, uint8_t v) { spc_wr(p, 0x100 | p->sp, v); p->sp--; }
static inline uint8_t spc_pop(Spc *p) { p->sp++; return spc_rd(p, 0x100 | p->sp); }

static inline uint8_t spc_adc(Spc *p, uint8_t a, uint8_t b) {
    unsigned c = p->psw & SF_C, r = a + b + c;
    p->psw &= ~(SF_N | SF_V | SF_H | SF_Z | SF_C);
    if (((a & 15) + (b & 15) + c) > 15) p->psw |= SF_H;
    if (~(a ^ b) & (a ^ r) & 0x80) p->psw |= SF_V;
    if (r > 255) p->psw |= SF_C;
    r &= 255; p->psw |= (r & 0x80) | (r == 0 ? SF_Z : 0);
    return r;
}
static inline uint8_t spc_sbc(Spc *p, uint8_t a, uint8_t b) {
    unsigned c = p->psw & SF_C; int r = a - b - (1 - c);
    p->psw &= ~(SF_N | SF_V | SF_H | SF_Z | SF_C);
    if (((a & 15) - (b & 15) - (1 - c)) >= 0) p->psw |= SF_H;
    if ((a ^ b) & (a ^ r) & 0x80) p->psw |= SF_V;
    if (r >= 0) p->psw |= SF_C;
    r &= 255; p->psw |= (r & 0x80) | (r == 0 ? SF_Z : 0);
    return r;
}
static inline void spc_cmp(Spc *p, uint8_t a, uint8_t b) {
    int r = a - b; p->psw &= ~(SF_N | SF_Z | SF_C);
    if (r >= 0) p->psw |= SF_C;
    r &= 255; p->psw |= (r & 0x80) | (r == 0 ? SF_Z : 0);
}

/* execute one instruction; returns cycles */
static int spc_step(Spc *p) {
    uint8_t op = spc_f8(p);
    uint8_t t8, d; uint16_t ad, t16; int cy = 2, r;
#define A p->a
#define X p->x
#define Y p->y
#define DPA(d) spc_dpaddr(p, d)
#define BRANCH(cond) do { int8_t o = (int8_t)spc_f8(p); cy = 2; if (cond) { p->pc += o; cy = 4; } } while (0)
    /* ALU row helpers: operand fetch by addressing mode, then a statement on t8 */
#define ALU(opbase, STMT) \
    case opbase+0x04: t8 = spc_rd(p, DPA(spc_f8(p))); cy = 3; STMT; break; \
    case opbase+0x05: t8 = spc_rd(p, spc_f16(p)); cy = 4; STMT; break; \
    case opbase+0x06: t8 = spc_rd(p, DPA(X)); cy = 3; STMT; break; \
    case opbase+0x07: d = spc_f8(p) + X; ad = spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8); t8 = spc_rd(p, ad); cy = 6; STMT; break; \
    case opbase+0x08: t8 = spc_f8(p); cy = 2; STMT; break; \
    case opbase+0x14: t8 = spc_rd(p, DPA((uint8_t)(spc_f8(p) + X))); cy = 4; STMT; break; \
    case opbase+0x15: t8 = spc_rd(p, spc_f16(p) + X); cy = 5; STMT; break; \
    case opbase+0x16: t8 = spc_rd(p, spc_f16(p) + Y); cy = 5; STMT; break; \
    case opbase+0x17: d = spc_f8(p); ad = (spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8)) + Y; t8 = spc_rd(p, ad); cy = 6; STMT; break;
    /* memory-destination forms: dp,dp / (X),(Y) / dp,#imm */
#define ALU2(opbase, FN) \
    case opbase+0x09: { uint8_t s_ = spc_rd(p, DPA(spc_f8(p))); uint16_t da = DPA(spc_f8(p)); uint8_t dv = spc_rd(p, da); dv = FN(dv, s_); if (opbase != 0x60) spc_wr(p, da, dv); cy = 6; } break; \
    case opbase+0x18: { uint8_t s_ = spc_f8(p); uint16_t da = DPA(spc_f8(p)); uint8_t dv = spc_rd(p, da); dv = FN(dv, s_); if (opbase != 0x60) spc_wr(p, da, dv); cy = (opbase == 0x60) ? 5 : 5; } break; \
    case opbase+0x19: { uint8_t s_ = spc_rd(p, DPA(Y)); uint16_t da = DPA(X); uint8_t dv = spc_rd(p, da); dv = FN(dv, s_); if (opbase != 0x60) spc_wr(p, da, dv); cy = 5; } break;
#define FN_OR(a, b)  ({ uint8_t r_ = (a) | (b); SNZ(r_); r_; })
#define FN_AND(a, b) ({ uint8_t r_ = (a) & (b); SNZ(r_); r_; })
#define FN_EOR(a, b) ({ uint8_t r_ = (a) ^ (b); SNZ(r_); r_; })
#define FN_ADC(a, b) spc_adc(p, a, b)
#define FN_SBC(a, b) spc_sbc(p, a, b)
#define FN_CMP(a, b) ({ spc_cmp(p, a, b); (uint8_t)(a); })
    switch (op) {
    case 0x00: cy = 2; break;
    /* TCALLn */
    case 0x01: case 0x11: case 0x21: case 0x31: case 0x41: case 0x51: case 0x61: case 0x71:
    case 0x81: case 0x91: case 0xA1: case 0xB1: case 0xC1: case 0xD1: case 0xE1: case 0xF1:
        ad = 0xFFDE - 2 * (op >> 4); spc_push(p, p->pc >> 8); spc_push(p, p->pc & 0xFF); p->pc = spc_rd(p, ad) | (spc_rd(p, ad + 1) << 8); cy = 8; break;
    /* SET1 / CLR1 / BBS / BBC */
    case 0x02: case 0x22: case 0x42: case 0x62: case 0x82: case 0xA2: case 0xC2: case 0xE2: { ad = DPA(spc_f8(p)); spc_wr(p, ad, spc_rd(p, ad) | (1 << (op >> 5))); cy = 4; } break;
    case 0x12: case 0x32: case 0x52: case 0x72: case 0x92: case 0xB2: case 0xD2: case 0xF2: { ad = DPA(spc_f8(p)); spc_wr(p, ad, spc_rd(p, ad) & ~(1 << (op >> 5))); cy = 4; } break;
    case 0x03: case 0x23: case 0x43: case 0x63: case 0x83: case 0xA3: case 0xC3: case 0xE3: { t8 = spc_rd(p, DPA(spc_f8(p))); int8_t o = (int8_t)spc_f8(p); cy = 5; if (t8 & (1 << (op >> 5))) { p->pc += o; cy = 7; } } break;
    case 0x13: case 0x33: case 0x53: case 0x73: case 0x93: case 0xB3: case 0xD3: case 0xF3: { t8 = spc_rd(p, DPA(spc_f8(p))); int8_t o = (int8_t)spc_f8(p); cy = 5; if (!(t8 & (1 << (op >> 5)))) { p->pc += o; cy = 7; } } break;
    ALU(0x00, { A = FN_OR(A, t8); })
    ALU(0x20, { A = FN_AND(A, t8); })
    ALU(0x40, { A = FN_EOR(A, t8); })
    ALU(0x60, { spc_cmp(p, A, t8); })
    ALU(0x80, { A = spc_adc(p, A, t8); })
    ALU(0xA0, { A = spc_sbc(p, A, t8); })
    ALU2(0x00, FN_OR) ALU2(0x20, FN_AND) ALU2(0x40, FN_EOR) ALU2(0x60, FN_CMP) ALU2(0x80, FN_ADC) ALU2(0xA0, FN_SBC)
    /* ALU(...) above defines 0x?4..0x?8 and 0x?4+0x10 forms; MOV A,... are separate below */
    case 0x0A: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); if ((t8 >> bit) & 1) p->psw |= SF_C; cy = 5; } break;   /* OR1 C,m.b */
    case 0x2A: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); if (!((t8 >> bit) & 1)) p->psw |= SF_C; cy = 5; } break;  /* OR1 C,/m.b */
    case 0x4A: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); if (!((t8 >> bit) & 1)) p->psw &= ~SF_C; cy = 4; } break; /* AND1 C,m.b */
    case 0x6A: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); if ((t8 >> bit) & 1) p->psw &= ~SF_C; cy = 4; } break;  /* AND1 C,/m.b */
    case 0x8A: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); if ((t8 >> bit) & 1) p->psw ^= SF_C; cy = 5; } break;   /* EOR1 */
    case 0xAA: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; t8 = spc_rd(p, t16 & 0x1FFF); p->psw = (p->psw & ~SF_C) | ((t8 >> bit) & 1); cy = 4; } break; /* MOV1 C,m.b */
    case 0xCA: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; ad = t16 & 0x1FFF; t8 = spc_rd(p, ad); t8 = (t8 & ~(1 << bit)) | ((p->psw & SF_C) << bit); spc_wr(p, ad, t8); cy = 6; } break;
    case 0xEA: { t16 = spc_f16(p); uint8_t bit = t16 >> 13; ad = t16 & 0x1FFF; t8 = spc_rd(p, ad) ^ (1 << bit); spc_wr(p, ad, t8); cy = 5; } break;     /* NOT1 */
    /* shifts / rotates */
#define SHIFT(opA, opdp, opdpx, opabs, FN) \
    case opA: A = FN(p, A); cy = 2; break; \
    case opdp: ad = DPA(spc_f8(p)); spc_wr(p, ad, FN(p, spc_rd(p, ad))); cy = 4; break; \
    case opdpx: ad = DPA((uint8_t)(spc_f8(p) + X)); spc_wr(p, ad, FN(p, spc_rd(p, ad))); cy = 5; break; \
    case opabs: ad = spc_f16(p); spc_wr(p, ad, FN(p, spc_rd(p, ad))); cy = 5; break;
#define F_ASL(p, v) ({ uint8_t o_ = (v); p->psw = (p->psw & ~SF_C) | (o_ >> 7); o_ <<= 1; SNZ(o_); o_; })
#define F_LSR(p, v) ({ uint8_t o_ = (v); p->psw = (p->psw & ~SF_C) | (o_ & 1); o_ >>= 1; SNZ(o_); o_; })
#define F_ROL(p, v) ({ uint8_t o_ = (v), c_ = p->psw & SF_C; p->psw = (p->psw & ~SF_C) | (o_ >> 7); o_ = (o_ << 1) | c_; SNZ(o_); o_; })
#define F_ROR(p, v) ({ uint8_t o_ = (v), c_ = p->psw & SF_C; p->psw = (p->psw & ~SF_C) | (o_ & 1); o_ = (o_ >> 1) | (c_ << 7); SNZ(o_); o_; })
#define F_INC(p, v) ({ uint8_t o_ = (v) + 1; SNZ(o_); o_; })
#define F_DEC(p, v) ({ uint8_t o_ = (v) - 1; SNZ(o_); o_; })
    SHIFT(0x1C, 0x0B, 0x1B, 0x0C, F_ASL)
    SHIFT(0x5C, 0x4B, 0x5B, 0x4C, F_LSR)
    SHIFT(0x3C, 0x2B, 0x3B, 0x2C, F_ROL)
    SHIFT(0x7C, 0x6B, 0x7B, 0x6C, F_ROR)
    SHIFT(0xBC, 0xAB, 0xBB, 0xAC, F_INC)
    SHIFT(0x9C, 0x8B, 0x9B, 0x8C, F_DEC)
    case 0x3D: X++; SNZ(X); cy = 2; break;   case 0xFC: Y++; SNZ(Y); cy = 2; break;
    case 0x1D: X--; SNZ(X); cy = 2; break;   case 0xDC: Y--; SNZ(Y); cy = 2; break;
    /* word ops */
    case 0x1A: { ad = DPA(spc_f8(p)); uint16_t v = spc_rd(p, ad) | (spc_rd(p, DPA((uint8_t)(ad + 1))) << 8); v--; spc_wr(p, ad, v & 0xFF); spc_wr(p, (ad & 0x100) | ((ad + 1) & 0xFF), v >> 8); p->psw = (p->psw & ~(SF_N | SF_Z)) | ((v >> 8) & 0x80) | (v == 0 ? SF_Z : 0); cy = 6; } break;
    case 0x3A: { ad = DPA(spc_f8(p)); uint16_t v = spc_rd(p, ad) | (spc_rd(p, (ad & 0x100) | ((ad + 1) & 0xFF)) << 8); v++; spc_wr(p, ad, v & 0xFF); spc_wr(p, (ad & 0x100) | ((ad + 1) & 0xFF), v >> 8); p->psw = (p->psw & ~(SF_N | SF_Z)) | ((v >> 8) & 0x80) | (v == 0 ? SF_Z : 0); cy = 6; } break;
    case 0x5A: { ad = DPA(spc_f8(p)); uint16_t v = spc_rd(p, ad) | (spc_rd(p, (ad & 0x100) | ((ad + 1) & 0xFF)) << 8); uint16_t ya = A | (Y << 8); int r2 = ya - v; p->psw &= ~(SF_N | SF_Z | SF_C); if (r2 >= 0) p->psw |= SF_C; r2 &= 0xFFFF; p->psw |= ((r2 >> 8) & 0x80) | (r2 == 0 ? SF_Z : 0); cy = 4; } break;
    case 0x7A: { ad = DPA(spc_f8(p)); uint16_t v = spc_rd(p, ad) | (spc_rd(p, (ad & 0x100) | ((ad + 1) & 0xFF)) << 8); uint16_t ya = A | (Y << 8); p->psw &= ~SF_C; uint8_t lo = spc_adc(p, A, v & 0xFF); uint8_t hi = spc_adc(p, Y, v >> 8); A = lo; Y = hi; uint16_t res = lo | (hi << 8); p->psw = (p->psw & ~SF_Z) | (res == 0 ? SF_Z : 0); (void)ya; cy = 5; } break;
    case 0x9A: { ad = DPA(spc_f8(p)); uint16_t v = spc_rd(p, ad) | (spc_rd(p, (ad & 0x100) | ((ad + 1) & 0xFF)) << 8); p->psw |= SF_C; uint8_t lo = spc_sbc(p, A, v & 0xFF); uint8_t hi = spc_sbc(p, Y, v >> 8); A = lo; Y = hi; uint16_t res = lo | (hi << 8); p->psw = (p->psw & ~SF_Z) | (res == 0 ? SF_Z : 0); cy = 5; } break;
    case 0xBA: { ad = DPA(spc_f8(p)); A = spc_rd(p, ad); Y = spc_rd(p, (ad & 0x100) | ((ad + 1) & 0xFF)); uint16_t v = A | (Y << 8); p->psw = (p->psw & ~(SF_N | SF_Z)) | ((v >> 8) & 0x80) | (v == 0 ? SF_Z : 0); cy = 5; } break;
    case 0xDA: { ad = DPA(spc_f8(p)); spc_rd(p, ad); spc_wr(p, ad, A); spc_wr(p, (ad & 0x100) | ((ad + 1) & 0xFF), Y); cy = 5; } break;
    /* MOV to A */
    case 0xE4: A = spc_rd(p, DPA(spc_f8(p))); SNZ(A); cy = 3; break;
    case 0xE5: A = spc_rd(p, spc_f16(p)); SNZ(A); cy = 4; break;
    case 0xE6: A = spc_rd(p, DPA(X)); SNZ(A); cy = 3; break;
    case 0xE7: d = spc_f8(p) + X; ad = spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8); A = spc_rd(p, ad); SNZ(A); cy = 6; break;
    case 0xE8: A = spc_f8(p); SNZ(A); cy = 2; break;
    case 0xF4: A = spc_rd(p, DPA((uint8_t)(spc_f8(p) + X))); SNZ(A); cy = 4; break;
    case 0xF5: A = spc_rd(p, spc_f16(p) + X); SNZ(A); cy = 5; break;
    case 0xF6: A = spc_rd(p, spc_f16(p) + Y); SNZ(A); cy = 5; break;
    case 0xF7: d = spc_f8(p); ad = (spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8)) + Y; A = spc_rd(p, ad); SNZ(A); cy = 6; break;
    case 0xBF: A = spc_rd(p, DPA(X)); X++; SNZ(A); cy = 4; break;
    /* MOV to X / Y */
    case 0xCD: X = spc_f8(p); SNZ(X); cy = 2; break;
    case 0xF8: X = spc_rd(p, DPA(spc_f8(p))); SNZ(X); cy = 3; break;
    case 0xF9: X = spc_rd(p, DPA((uint8_t)(spc_f8(p) + Y))); SNZ(X); cy = 4; break;
    case 0xE9: X = spc_rd(p, spc_f16(p)); SNZ(X); cy = 4; break;
    case 0x8D: Y = spc_f8(p); SNZ(Y); cy = 2; break;
    case 0xEB: Y = spc_rd(p, DPA(spc_f8(p))); SNZ(Y); cy = 3; break;
    case 0xFB: Y = spc_rd(p, DPA((uint8_t)(spc_f8(p) + X))); SNZ(Y); cy = 4; break;
    case 0xEC: Y = spc_rd(p, spc_f16(p)); SNZ(Y); cy = 4; break;
    /* MOV register transfers */
    case 0x5D: X = A; SNZ(X); cy = 2; break;   case 0xFD: Y = A; SNZ(Y); cy = 2; break;
    case 0x7D: A = X; SNZ(A); cy = 2; break;   case 0xDD: A = Y; SNZ(A); cy = 2; break;
    case 0x9D: X = p->sp; SNZ(X); cy = 2; break;   case 0xBD: p->sp = X; cy = 2; break;
    /* stores */
    case 0xC4: spc_wr(p, DPA(spc_f8(p)), A); cy = 4; break;
    case 0xC5: ad = spc_f16(p); spc_wr(p, ad, A); cy = 5; break;
    case 0xC6: spc_wr(p, DPA(X), A); cy = 4; break;
    case 0xC7: d = spc_f8(p) + X; ad = spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8); spc_wr(p, ad, A); cy = 7; break;
    case 0xD4: spc_wr(p, DPA((uint8_t)(spc_f8(p) + X)), A); cy = 5; break;
    case 0xD5: ad = spc_f16(p) + X; spc_wr(p, ad, A); cy = 6; break;
    case 0xD6: ad = spc_f16(p) + Y; spc_wr(p, ad, A); cy = 6; break;
    case 0xD7: d = spc_f8(p); ad = (spc_rd(p, DPA(d)) | (spc_rd(p, DPA((uint8_t)(d + 1))) << 8)) + Y; spc_wr(p, ad, A); cy = 7; break;
    case 0xAF: spc_wr(p, DPA(X), A); X++; cy = 4; break;
    case 0xD8: spc_wr(p, DPA(spc_f8(p)), X); cy = 4; break;
    case 0xD9: spc_wr(p, DPA((uint8_t)(spc_f8(p) + Y)), X); cy = 5; break;
    case 0xC9: ad = spc_f16(p); spc_wr(p, ad, X); cy = 5; break;
    case 0xCB: spc_wr(p, DPA(spc_f8(p)), Y); cy = 4; break;
    case 0xDB: spc_wr(p, DPA((uint8_t)(spc_f8(p) + X)), Y); cy = 5; break;
    case 0xCC: ad = spc_f16(p); spc_wr(p, ad, Y); cy = 5; break;
    case 0x8F: { uint8_t v = spc_f8(p); ad = DPA(spc_f8(p)); spc_rd(p, ad); spc_wr(p, ad, v); cy = 5; } break;     /* MOV dp,#imm */
    case 0xFA: { t8 = spc_rd(p, DPA(spc_f8(p))); spc_wr(p, DPA(spc_f8(p)), t8); cy = 5; } break;                  /* MOV dp,dp */
    /* compares with X / Y */
    case 0xC8: spc_cmp(p, X, spc_f8(p)); cy = 2; break;
    case 0x3E: spc_cmp(p, X, spc_rd(p, DPA(spc_f8(p)))); cy = 3; break;
    case 0x1E: spc_cmp(p, X, spc_rd(p, spc_f16(p))); cy = 4; break;
    case 0xAD: spc_cmp(p, Y, spc_f8(p)); cy = 2; break;
    case 0x7E: spc_cmp(p, Y, spc_rd(p, DPA(spc_f8(p)))); cy = 3; break;
    case 0x5E: spc_cmp(p, Y, spc_rd(p, spc_f16(p))); cy = 4; break;
    /* flow */
    case 0x10: BRANCH(!(p->psw & SF_N)); break;   case 0x30: BRANCH(p->psw & SF_N); break;
    case 0x50: BRANCH(!(p->psw & SF_V)); break;   case 0x70: BRANCH(p->psw & SF_V); break;
    case 0x90: BRANCH(!(p->psw & SF_C)); break;   case 0xB0: BRANCH(p->psw & SF_C); break;
    case 0xD0: BRANCH(!(p->psw & SF_Z)); break;   case 0xF0: BRANCH(p->psw & SF_Z); break;
    case 0x2F: { int8_t o = (int8_t)spc_f8(p); p->pc += o; cy = 4; } break;
    case 0x2E: { t8 = spc_rd(p, DPA(spc_f8(p))); int8_t o = (int8_t)spc_f8(p); cy = 5; if (A != t8) { p->pc += o; cy = 7; } } break;
    case 0xDE: { t8 = spc_rd(p, DPA((uint8_t)(spc_f8(p) + X))); int8_t o = (int8_t)spc_f8(p); cy = 6; if (A != t8) { p->pc += o; cy = 8; } } break;
    case 0x6E: { ad = DPA(spc_f8(p)); t8 = spc_rd(p, ad) - 1; spc_wr(p, ad, t8); int8_t o = (int8_t)spc_f8(p); cy = 5; if (t8) { p->pc += o; cy = 7; } } break;
    case 0xFE: { Y--; int8_t o = (int8_t)spc_f8(p); cy = 4; if (Y) { p->pc += o; cy = 6; } } break;
    case 0x5F: p->pc = spc_f16(p); cy = 3; break;
    case 0x1F: ad = spc_f16(p) + X; p->pc = spc_rd(p, ad) | (spc_rd(p, ad + 1) << 8); cy = 6; break;
    case 0x3F: ad = spc_f16(p); spc_push(p, p->pc >> 8); spc_push(p, p->pc & 0xFF); p->pc = ad; cy = 8; break;
    case 0x4F: d = spc_f8(p); spc_push(p, p->pc >> 8); spc_push(p, p->pc & 0xFF); p->pc = 0xFF00 | d; cy = 6; break;
    case 0x6F: { uint8_t lo = spc_pop(p); uint8_t hi = spc_pop(p); p->pc = lo | (hi << 8); cy = 5; } break;
    case 0x7F: { p->psw = spc_pop(p); uint8_t lo = spc_pop(p); uint8_t hi = spc_pop(p); p->pc = lo | (hi << 8); cy = 6; } break;
    case 0x0F: spc_push(p, p->pc >> 8); spc_push(p, p->pc & 0xFF); spc_push(p, p->psw); p->psw = (p->psw & ~SF_I) | SF_B; p->pc = spc_rd(p, 0xFFDE) | (spc_rd(p, 0xFFDF) << 8); cy = 8; break;
    /* stack */
    case 0x2D: spc_push(p, A); cy = 4; break;  case 0x4D: spc_push(p, X); cy = 4; break;  case 0x6D: spc_push(p, Y); cy = 4; break;  case 0x0D: spc_push(p, p->psw); cy = 4; break;
    case 0xAE: A = spc_pop(p); cy = 4; break;  case 0xCE: X = spc_pop(p); cy = 4; break;  case 0xEE: Y = spc_pop(p); cy = 4; break;  case 0x8E: p->psw = spc_pop(p); cy = 4; break;
    /* flags */
    case 0x20: p->psw &= ~SF_P; cy = 2; break;   case 0x40: p->psw |= SF_P; cy = 2; break;
    case 0x60: p->psw &= ~SF_C; cy = 2; break;   case 0x80: p->psw |= SF_C; cy = 2; break;
    case 0xED: p->psw ^= SF_C; cy = 3; break;    case 0xE0: p->psw &= ~(SF_V | SF_H); cy = 2; break;
    case 0xA0: p->psw |= SF_I; cy = 3; break;    case 0xC0: p->psw &= ~SF_I; cy = 3; break;
    /* misc */
    case 0x0E: ad = spc_f16(p); t8 = spc_rd(p, ad); { uint8_t c = A - t8; SNZ(c); } spc_wr(p, ad, t8 | A); cy = 6; break;     /* TSET1 */
    case 0x4E: ad = spc_f16(p); t8 = spc_rd(p, ad); { uint8_t c = A - t8; SNZ(c); } spc_wr(p, ad, t8 & ~A); cy = 6; break;    /* TCLR1 */
    case 0x9F: A = (A >> 4) | (A << 4); SNZ(A); cy = 5; break;
    case 0xCF: { uint16_t v = (uint16_t)Y * A; A = v & 0xFF; Y = v >> 8; p->psw = (p->psw & ~(SF_N | SF_Z)) | (Y & 0x80) | (Y == 0 ? SF_Z : 0); cy = 9; } break;
    case 0x9E: {
        uint16_t ya = A | (Y << 8); p->psw &= ~(SF_V | SF_H);
        if ((Y & 15) >= (X & 15)) p->psw |= SF_H;
        if (Y >= X) p->psw |= SF_V;
        if (Y < (X << 1)) { A = ya / X; Y = ya % X; }
        else { A = 255 - (ya - (X << 9)) / (256 - X); Y = X + (ya - (X << 9)) % (256 - X); }
        SNZ(A); cy = 12;
    } break;
    case 0xDF: if ((p->psw & SF_C) || A > 0x99) { A += 0x60; p->psw |= SF_C; } if ((p->psw & SF_H) || (A & 15) > 9) A += 6; SNZ(A); cy = 3; break;
    case 0xBE: if (!(p->psw & SF_C) || A > 0x99) { A -= 0x60; p->psw &= ~SF_C; } if (!(p->psw & SF_H) || (A & 15) > 9) A -= 6; SNZ(A); cy = 3; break;
    case 0xEF: case 0xFF: p->stopped = 1; p->pc--; cy = 3; break;       /* SLEEP / STOP: halt */
    default: p->stopped = 1; cy = 2; break;
    }
    return cy;
}
#undef A
#undef X
#undef Y

/* run the SPC up to `target` SPC cycles */
static void spc_run(Spc *p, uint64_t target) {
    while (p->cyc < target && !p->stopped) {
        uint64_t old = p->cyc;
        p->cyc += spc_step(p);
        spc_timers(p, old, p->cyc);
    }
    if (p->stopped) p->cyc = target;
}
