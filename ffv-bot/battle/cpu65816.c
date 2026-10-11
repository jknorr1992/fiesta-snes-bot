/* Minimal 65C816 interpreter + just enough SNES (WRAM, ROM map, DMA, math regs, NMI/vblank timing, joypad) to run the
 * FFV battle program straight from the ROM.  No PPU/APU: their registers are write-only sinks, DMA to them is skipped.
 * Build: gcc -O2 -shared -fPIC -o libcpu65816.so cpu65816.c */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define FRAME_CYC   357368u        /* master cycles per frame */
#define VBLANK_CYC  (225u * 1364u) /* frame cycle where vblank (and NMI) starts */
#define MAXHOOK 64

#include "spc700.h"

typedef struct Sim {
    uint8_t  wram[0x20000];
    /* registers */
    uint16_t A, X, Y, S, D, PC;
    uint8_t  DB, PB;
    uint8_t  fN, fV, fM, fX, fD, fI, fZ, fC, fE;
    /* machine */
    uint64_t instr, cyc_total;
    uint64_t pcyc[256], pcnt[256]; uint8_t prof_on;
    uint32_t dma_bytes[256], dma_calls[256];   /* per B-bus register: bytes / channel-runs of general DMA (statistics only) */
    uint64_t *tr; uint32_t tr_n, tr_cap, tr_every;   /* optional PC trace: (cyc_total << 24 | pc24) every tr_every instructions; buffer shared by clones */
    uint32_t frame_cyc, frames, cyc_scale, cyc_rem, nb, refresh_line;
    uint8_t  memsel, xcross, exact, hdma_en, hdma_on[8], hdma_lc[8], hdma_rep[8], hdma_xfer[8];
    uint32_t hdma_a2a[8], hdma_ind[8];
    uint32_t hooklog[256][3]; uint32_t hooklogn;
    uint8_t  hv_flip; uint16_t hv_h, hv_v;
    uint8_t  nmitimen, rdnmi, in_nmi_unused, stopped, waiting, nmi_pending;
    uint32_t wmadd;
    uint16_t wrmpy_res, wrdiv_q, wrdiv_r, dividend;
    uint8_t  wrmpya;
    uint16_t joy[4];
    uint8_t  apu_port[4], apu_in[4], spc_on;
    uint64_t spc_base;
    Spc      spc;
    uint8_t  dma[8][16];
    uint32_t stop_pc;               /* stop when PC24 hits this */
    uint32_t hook_addr[MAXHOOK];    /* PC24 -> stop with code 100+i  (host handles) */
    uint8_t  hook_kind[MAXHOOK];    /* 0 = stop and let host handle; 1 = HLE RTL; 2 = HLE RTS; 3 = idle-wait (see below) */
    uint32_t hook_arg1[MAXHOOK], hook_arg2[MAXHOOK];
    int      nhook;
    uint32_t ring[256]; uint32_t ringpos;
    uint8_t  io_log_on, skip_hook;
    uint64_t io_ppu_writes;
} Sim;

#include "iotab.h"
static const uint8_t *ROM; static uint32_t ROMSZ;
static uint8_t *HOOKMAP;   /* per ROM byte: hook index + 1 */

void rom_set(const uint8_t *p, uint32_t n) { ROM = p; ROMSZ = n; free(HOOKMAP); HOOKMAP = calloc(n + 1, 1); }

static inline int rom_off(uint32_t a, uint32_t *o) {
    uint32_t b = a >> 16, w = a & 0xFFFF;
    if (b >= 0xC0 || (b >= 0x40 && b < 0x7E)) { *o = ((b & 0x3F) << 16) | w; return *o < ROMSZ; }
    if ((b < 0x40 || (b >= 0x80 && b < 0xC0)) && w >= 0x8000) { *o = ((b & 0x3F) << 16) | w; return *o < ROMSZ; }
    return 0;
}

/* bring the SPC up to the CPU's current master cycle (SPC runs 15664/328125 cycles per master cycle, as in snes9x) */
static void spc_sync(Sim *s) {
    uint64_t target = s->cyc_total * 15664ull / 328125ull + s->spc_base;
    spc_run(&s->spc, target);
}

static uint8_t io_read(Sim *s, uint32_t w) {
    switch (w) {
    case 0x2134: case 0x2135: case 0x2136: return 0;
    case 0x2137: s->hv_h = (s->frame_cyc % 1364) / 4; s->hv_v = s->frame_cyc / 1364; return 0;      /* H/V counter latch (used by the timing validators) */
    case 0x213C: s->hv_flip ^= 1; return s->hv_flip ? s->hv_h & 0xFF : s->hv_h >> 8;
    case 0x213D: s->hv_flip ^= 2; return (s->hv_flip & 2) ? s->hv_v & 0xFF : s->hv_v >> 8;
    case 0x2180: { uint8_t v = s->wram[s->wmadd & 0x1FFFF]; s->wmadd = (s->wmadd + 1) & 0x1FFFF; return v; }
    case 0x2140: case 0x2141: case 0x2142: case 0x2143: if (s->spc_on) { spc_sync(s); return s->spc.from_spc[w & 3]; } return s->apu_in[w & 3];
    case 0x4210: { uint8_t v = (s->rdnmi ? 0x80 : 0) | 0x02; s->rdnmi = 0; return v; }
    case 0x4211: return 0;
    case 0x4212: {                                  /* as snes9x: auto-joypad busy during the first 3 vblank lines, H-blank flag from H=1096 */
        uint8_t v = 0;
        if (s->frame_cyc >= VBLANK_CYC) v |= 0x80;
        if (s->frame_cyc >= VBLANK_CYC && s->frame_cyc < VBLANK_CYC + 3 * 1364) v |= 0x01;
        if (s->frame_cyc % 1364 >= 1096) v |= 0x40;
        return v;
    }
    case 0x4214: return s->wrdiv_q & 0xFF;  case 0x4215: return s->wrdiv_q >> 8;
    case 0x4216: return s->wrdiv_r & 0xFF;  case 0x4217: return s->wrdiv_r >> 8;
    case 0x4218: case 0x421A: case 0x421C: case 0x421E: return s->joy[(w - 0x4218) >> 1] & 0xFF;
    case 0x4219: case 0x421B: case 0x421D: case 0x421F: return s->joy[(w - 0x4218) >> 1] >> 8;
    case 0x4016: case 0x4017: return 0;
    }
    if (w >= 0x4300 && w < 0x4380) return s->dma[(w >> 4) & 7][w & 15];
    return 0;
}

static void dma_run(Sim *s, uint8_t mask);
static void io_write(Sim *s, uint32_t w, uint8_t v) {
    if (w >= 0x2100 && w < 0x2180 && (w < 0x2140 || w > 0x2143)) { s->io_ppu_writes++; return; }
    switch (w) {
    case 0x2180: s->wram[s->wmadd & 0x1FFFF] = v; s->wmadd = (s->wmadd + 1) & 0x1FFFF; return;
    case 0x2181: s->wmadd = (s->wmadd & 0x1FF00) | v; return;
    case 0x2182: s->wmadd = (s->wmadd & 0x100FF) | (v << 8); return;
    case 0x2183: s->wmadd = (s->wmadd & 0x0FFFF) | ((v & 1) << 16); return;
    case 0x2140: case 0x2141: case 0x2142: case 0x2143: if (s->spc_on) { spc_sync(s); s->spc.to_spc[w & 3] = v; return; } s->apu_port[w & 3] = v; s->apu_in[w & 3] = v; return;
    case 0x4200: s->nmitimen = v; return;
    case 0x4202: s->wrmpya = v; return;
    case 0x4203: s->wrmpy_res = (uint16_t)s->wrmpya * v; s->wrdiv_r = s->wrmpy_res; return;
    case 0x4204: s->dividend = (s->dividend & 0xFF00) | v; return;
    case 0x4205: s->dividend = (s->dividend & 0x00FF) | (v << 8); return;
    case 0x4206:
        if (v) { s->wrdiv_q = s->dividend / v; s->wrdiv_r = s->dividend % v; } else { s->wrdiv_q = 0xFFFF; s->wrdiv_r = s->dividend; }
        return;
    case 0x420B: dma_run(s, v); return;
    case 0x420C: s->hdma_en = v; return;
    case 0x420D: s->memsel = v & 1; return;
    }
    if (w >= 0x4300 && w < 0x4380) s->dma[(w >> 4) & 7][w & 15] = v;
}

/* master cycles for one bus access at 24-bit address a */
static inline uint32_t bus_cost(Sim *s, uint32_t a) {
    uint32_t b = (a >> 16) & 0xFF, w = a & 0xFFFF;
    if (!s->exact) return 8;
    if (b == 0x7E || b == 0x7F) return 8;
    if (b >= 0xC0) return s->memsel ? 6 : 8;     /* HiROM banks C0-FF are ROM at every address (no I/O window); fast when MEMSEL is set */
    if (b >= 0x40 && b < 0x7E) return 8;         /* 40-7D: ROM, always slow */
    if (w < 0x2000) return 8;                    /* system banks 00-3F / 80-BF */
    if (w < 0x4000) return 6;
    if (w < 0x4200) return 12;
    if (w < 0x6000) return 6;
    if (w < 0x8000) return 8;
    return (b >= 0x80 && s->memsel) ? 6 : 8;
}
static void hdma_line(Sim *s, int charge);
static void sync_lines(Sim *s, int charge);
static inline void resync_refresh(Sim *s) { sync_lines(s, 0); }     /* after a clock jump: advance per-line state without charging time */
static inline void tick(Sim *s, uint32_t c) { s->cyc_total += c; s->frame_cyc += c; }
static inline uint8_t rd8(Sim *s, uint32_t a) {
    uint32_t b = (a >> 16) & 0xFF, w = a & 0xFFFF, o;
    s->nb++; tick(s, bus_cost(s, a));
    if (b == 0x7E) return s->wram[w];
    if (b == 0x7F) return s->wram[0x10000 + w];
    if ((b < 0x40 || (b >= 0x80 && b < 0xC0)) && w < 0x8000) {
        if (w < 0x2000) return s->wram[w];
        if (w >= 0x2000 && w < 0x6000) return io_read(s, w);
        return 0;
    }
    if (rom_off(a & 0xFFFFFF, &o)) return ROM[o];
    return 0;
}
static inline void wr8(Sim *s, uint32_t a, uint8_t v) {
    uint32_t b = (a >> 16) & 0xFF, w = a & 0xFFFF;
    s->nb++; tick(s, bus_cost(s, a));
    if (b == 0x7E) { s->wram[w] = v; return; }
    if (b == 0x7F) { s->wram[0x10000 + w] = v; return; }
    if ((b < 0x40 || (b >= 0x80 && b < 0xC0)) && w < 0x8000) {
        if (w < 0x2000) s->wram[w] = v;
        else if (w < 0x6000) io_write(s, w, v);
    }
}
static inline uint16_t rd16(Sim *s, uint32_t a) { uint16_t l = rd8(s, a); return l | (rd8(s, (a + 1) & 0xFFFFFF) << 8); }
static inline void wr16(Sim *s, uint32_t a, uint16_t v) { wr8(s, a, v & 0xFF); wr8(s, (a + 1) & 0xFFFFFF, v >> 8); }
/* bank-0 16-bit-wrapping accessors (direct page / stack) */
static inline uint16_t rd16_b0(Sim *s, uint16_t a) { uint16_t l = rd8(s, a); return l | (rd8(s, (uint16_t)(a + 1)) << 8); }
static inline uint32_t rd24_b0(Sim *s, uint16_t a) { uint32_t l = rd16_b0(s, a); return l | (rd8(s, (uint16_t)(a + 2)) << 16); }

static void dma_run(Sim *s, uint8_t mask) {
    static const uint8_t off[8][4] = {{0,0,0,0},{0,1,0,1},{0,0,0,0},{0,0,1,1},{0,1,2,3},{0,1,0,1},{0,0,0,0},{0,0,1,1}};
    static const int len[8] = {1,2,2,4,4,4,2,4};
    for (int c = 0; c < 8; c++) {
        if (!(mask & (1 << c))) continue;
        uint8_t *d = s->dma[c];
        uint8_t ctl = d[0], breg = d[1];
        uint32_t src = d[2] | (d[3] << 8) | (d[4] << 16);
        uint32_t cnt = d[5] | (d[6] << 8); if (!cnt) cnt = 0x10000;
        int mode = ctl & 7, fixed = ctl & 8, dec = ctl & 16, tob = !(ctl & 0x80);
        int i = 0;
        uint32_t cnt0 = cnt, fc0 = s->frame_cyc; uint64_t ct0 = s->cyc_total;
        s->dma_bytes[breg] += cnt; s->dma_calls[breg]++;
        while (cnt) {
            uint8_t br = breg + off[mode][i % len[mode]];
            if (tob) {
                if (br == 0x80) { uint8_t v = rd8(s, src); s->wram[s->wmadd & 0x1FFFF] = v; s->wmadd = (s->wmadd + 1) & 0x1FFFF; }
                /* PPU/APU/other targets: dropped */
            } else {
                if (br == 0x80) { wr8(s, src, s->wram[s->wmadd & 0x1FFFF]); s->wmadd = (s->wmadd + 1) & 0x1FFFF; }
            }
            if (!fixed) src = (src & 0xFF0000) | ((src + (dec ? -1 : 1)) & 0xFFFF);
            i++; cnt--;
        }
        d[2] = src & 0xFF; d[3] = (src >> 8) & 0xFF; d[5] = 0; d[6] = 0;
        s->frame_cyc = fc0; s->cyc_total = ct0;         /* DMA runs at 8 master cycles per byte regardless of the bus speed */
        tick(s, 8 * cnt0 + (s->exact ? 8 : 0));
    }
    if (s->exact) tick(s, 16);                         /* DMA start / CPU resync overhead */
}


/* ---- HDMA: modelled only for its CPU cost (the table walk is real, the PPU writes are dropped) ---- */
static uint8_t peek8(Sim *s, uint32_t a) { uint64_t ct = s->cyc_total; uint32_t fc = s->frame_cyc, nb = s->nb; uint8_t v = rd8(s, a); s->cyc_total = ct; s->frame_cyc = fc; s->nb = nb; return v; }
static const int HDMA_LEN[8] = {1,2,2,4,4,4,2,4};
static void hdma_load_count(Sim *s, int c, int charge) {
    uint8_t *d = s->dma[c]; int indirect = d[0] & 0x40;
    uint8_t cnt = peek8(s, s->hdma_a2a[c]); s->hdma_a2a[c] = (s->hdma_a2a[c] & 0xFF0000) | ((s->hdma_a2a[c] + 1) & 0xFFFF);
    if (charge) tick(s, 8);
    if (!cnt) { s->hdma_on[c] = 0; return; }
    s->hdma_lc[c] = cnt & 0x7F; s->hdma_rep[c] = cnt & 0x80; s->hdma_xfer[c] = 1;
    if (indirect) {
        uint32_t a = s->hdma_a2a[c]; uint32_t lo = peek8(s, a), hi = peek8(s, (a & 0xFF0000) | ((a + 1) & 0xFFFF));
        s->hdma_ind[c] = ((uint32_t)d[7] << 16) | lo | (hi << 8);
        s->hdma_a2a[c] = (a & 0xFF0000) | ((a + 2) & 0xFFFF);
        if (charge) tick(s, 16);
    }
}
static void hdma_frame_init(Sim *s) {
    int any = 0;
    for (int c = 0; c < 8; c++) {
        s->hdma_on[c] = 0;
        if (!(s->hdma_en & (1 << c))) continue;
        uint8_t *d = s->dma[c];
        s->hdma_a2a[c] = d[2] | (d[3] << 8) | (d[4] << 16);
        s->hdma_on[c] = 1; any = 1;
        hdma_load_count(s, c, 1);
        tick(s, 8);
    }
    if (any) tick(s, 18);
}
static void hdma_line(Sim *s, int charge) {
    int any = 0;
    for (int c = 0; c < 8; c++) {
        if (!s->hdma_on[c]) continue;
        any = 1;
        uint8_t *d = s->dma[c]; int indirect = d[0] & 0x40, n = HDMA_LEN[d[0] & 7];
        if (charge) tick(s, 8);
        if (s->hdma_xfer[c]) {
            if (charge) tick(s, 8 * n);
            if (indirect) s->hdma_ind[c] = (s->hdma_ind[c] & 0xFF0000) | ((s->hdma_ind[c] + n) & 0xFFFF);
            else s->hdma_a2a[c] = (s->hdma_a2a[c] & 0xFF0000) | ((s->hdma_a2a[c] + n) & 0xFFFF);
        }
        s->hdma_xfer[c] = s->hdma_rep[c] ? 1 : 0;
        if (--s->hdma_lc[c] == 0) hdma_load_count(s, c, charge);
    }
    if (any && charge) tick(s, 18);
}

/* per-scanline events: DRAM refresh (40 cycles at H=538, CPU stalled) and HDMA (lines 0-224) */
static void sync_lines(Sim *s, int charge) {
    while (s->refresh_line < 262 && s->frame_cyc >= s->refresh_line * 1364u + 538u) {
        if (charge) tick(s, 40);
        if (s->refresh_line < 225) hdma_line(s, charge);
        s->refresh_line++;
    }
}

/* ---------------------------------------------------------------- CPU */
#define NZ8(v)  do { s->fN = ((v) >> 7) & 1; s->fZ = ((v) & 0xFF) == 0; } while (0)
#define NZ16(v) do { s->fN = ((v) >> 15) & 1; s->fZ = ((v) & 0xFFFF) == 0; } while (0)
#define NZM(v)  do { if (s->fM) NZ8(v); else NZ16(v); } while (0)
#define NZX(v)  do { if (s->fX) NZ8(v); else NZ16(v); } while (0)

static inline uint8_t  getP(Sim *s) {
    return (s->fN << 7) | (s->fV << 6) | (s->fM << 5) | (s->fX << 4) | (s->fD << 3) | (s->fI << 2) | (s->fZ << 1) | s->fC;
}
static inline void setP(Sim *s, uint8_t p) {
    s->fN = p >> 7; s->fV = (p >> 6) & 1; s->fM = (p >> 5) & 1; s->fX = (p >> 4) & 1;
    s->fD = (p >> 3) & 1; s->fI = (p >> 2) & 1; s->fZ = (p >> 1) & 1; s->fC = p & 1;
    if (s->fE) { s->fM = 1; s->fX = 1; }
    if (s->fX) { s->X &= 0xFF; s->Y &= 0xFF; }
}
static inline void push8(Sim *s, uint8_t v) { wr8(s, s->S, v); s->S--; }
static inline void push16(Sim *s, uint16_t v) { push8(s, v >> 8); push8(s, v & 0xFF); }
static inline uint8_t pull8(Sim *s) { s->S++; return rd8(s, s->S); }
static inline uint16_t pull16(Sim *s) { uint16_t l = pull8(s); return l | (pull8(s) << 8); }
static inline uint8_t fetch8(Sim *s) { uint8_t v = rd8(s, ((uint32_t)s->PB << 16) | s->PC); s->PC++; return v; }
static inline uint16_t fetch16(Sim *s) { uint16_t l = fetch8(s); return l | (fetch8(s) << 8); }
static inline uint32_t fetch24(Sim *s) { uint32_t l = fetch16(s); return l | (fetch8(s) << 16); }

#define IDX(r) (s->fX ? ((r) & 0xFF) : (r))
/* effective-address helpers (return 24-bit) */
static inline void dl(Sim *s) { if (s->exact && (s->D & 0xFF)) tick(s, 6); }
static inline uint32_t ea_dp(Sim *s)   { dl(s); return (uint16_t)(s->D + fetch8(s)); }
static inline uint32_t ea_dpx(Sim *s)  { dl(s); return (uint16_t)(s->D + fetch8(s) + IDX(s->X)); }
static inline uint32_t ea_dpy(Sim *s)  { dl(s); return (uint16_t)(s->D + fetch8(s) + IDX(s->Y)); }
static inline uint32_t ea_ind(Sim *s)  { dl(s); uint16_t p = rd16_b0(s, (uint16_t)(s->D + fetch8(s))); return ((uint32_t)s->DB << 16) | p; }
static inline uint32_t ea_indx(Sim *s) { dl(s); uint16_t p = rd16_b0(s, (uint16_t)(s->D + fetch8(s) + IDX(s->X))); return ((uint32_t)s->DB << 16) | p; }
static inline uint32_t ea_indy(Sim *s) { dl(s); uint16_t p = rd16_b0(s, (uint16_t)(s->D + fetch8(s))); s->xcross = !s->fX || ((p ^ (p + IDX(s->Y))) & 0xFF00) != 0; return (((uint32_t)s->DB << 16) + p + IDX(s->Y)) & 0xFFFFFF; }
static inline uint32_t ea_lind(Sim *s) { dl(s); return rd24_b0(s, (uint16_t)(s->D + fetch8(s))); }
static inline uint32_t ea_lindy(Sim *s){ dl(s); return (rd24_b0(s, (uint16_t)(s->D + fetch8(s))) + IDX(s->Y)) & 0xFFFFFF; }
static inline uint32_t ea_abs(Sim *s)  { return ((uint32_t)s->DB << 16) | fetch16(s); }
static inline uint32_t ea_absx(Sim *s) { uint16_t b = fetch16(s); s->xcross = !s->fX || ((b ^ (b + IDX(s->X))) & 0xFF00) != 0; return (((uint32_t)s->DB << 16) + b + IDX(s->X)) & 0xFFFFFF; }
static inline uint32_t ea_absy(Sim *s) { uint16_t b = fetch16(s); s->xcross = !s->fX || ((b ^ (b + IDX(s->Y))) & 0xFF00) != 0; return (((uint32_t)s->DB << 16) + b + IDX(s->Y)) & 0xFFFFFF; }
static inline uint32_t ea_long(Sim *s) { return fetch24(s); }
static inline uint32_t ea_longx(Sim *s){ return (fetch24(s) + IDX(s->X)) & 0xFFFFFF; }
static inline uint32_t ea_sr(Sim *s)   { return (uint16_t)(s->S + fetch8(s)); }
static inline uint32_t ea_sry(Sim *s)  { uint16_t p = rd16_b0(s, (uint16_t)(s->S + fetch8(s))); return (((uint32_t)s->DB << 16) + p + IDX(s->Y)) & 0xFFFFFF; }
/* dp-type EAs wrap at bank 0 for the second byte, abs-type cross banks: use rdm16 variants */
static inline uint16_t rdw(Sim *s, uint32_t ea, int bank0) { return bank0 ? rd16_b0(s, (uint16_t)ea) : rd16(s, ea); }
static inline void wrw(Sim *s, uint32_t ea, uint16_t v, int bank0) {
    if (bank0) { wr8(s, (uint16_t)ea, v & 0xFF); wr8(s, (uint16_t)(ea + 1), v >> 8); } else wr16(s, ea, v);
}

static void do_adc(Sim *s, uint16_t v) {
    if (s->fM) {
        uint8_t a = s->A & 0xFF, b = v & 0xFF; unsigned r;
        if (!s->fD) { r = a + b + s->fC; s->fV = (~(a ^ b) & (a ^ r) & 0x80) != 0; s->fC = r > 0xFF; }
        else {
            unsigned lo = (a & 0xF) + (b & 0xF) + s->fC; if (lo > 9) lo += 6;
            r = (a & 0xF0) + (b & 0xF0) + (lo > 0xF ? 0x10 : 0) + (lo & 0xF);
            s->fV = (~(a ^ b) & (a ^ r) & 0x80) != 0; if (r > 0x9F) r += 0x60; s->fC = r > 0xFF;
        }
        s->A = (s->A & 0xFF00) | (r & 0xFF); NZ8(r);
    } else {
        uint16_t a = s->A, b = v; uint32_t r;
        if (!s->fD) { r = a + b + s->fC; s->fV = (~(a ^ b) & (a ^ r) & 0x8000) != 0; s->fC = r > 0xFFFF; }
        else {
            uint32_t c = s->fC, res = 0;
            for (int i = 0; i < 4; i++) {
                unsigned d = ((a >> (4 * i)) & 0xF) + ((b >> (4 * i)) & 0xF) + c;
                if (d > 9) d += 6;
                c = d > 0xF; res |= (uint32_t)(d & 0xF) << (4 * i);
            }
            r = res | (c << 16);
            s->fV = (~(a ^ b) & (a ^ res) & 0x8000) != 0; s->fC = c;
        }
        s->A = r & 0xFFFF; NZ16(r);
    }
}
static void do_sbc(Sim *s, uint16_t v) {
    if (s->fM) {
        uint8_t a = s->A & 0xFF, b = v & 0xFF; unsigned r;
        if (!s->fD) { r = a + (b ^ 0xFF) + s->fC; s->fV = ((a ^ b) & (a ^ r) & 0x80) != 0; s->fC = r > 0xFF; }
        else {
            int lo = (a & 0xF) - (b & 0xF) + s->fC - 1; int hi = (a >> 4) - (b >> 4);
            if (lo < 0) { lo += 10; hi--; }
            if (hi < 0) { hi += 10; s->fC = 0; } else s->fC = 1;
            r = (hi << 4) | (lo & 0xF);
            { unsigned bin = a + (b ^ 0xFF) + 1; (void)bin; }
            s->fV = 0;
        }
        s->A = (s->A & 0xFF00) | (r & 0xFF); NZ8(r);
    } else {
        uint16_t a = s->A, b = v; uint32_t r;
        if (!s->fD) { r = a + (uint16_t)~b + s->fC; s->fV = ((a ^ b) & (a ^ r) & 0x8000) != 0; s->fC = r > 0xFFFF; }
        else {
            int c = s->fC; uint32_t res = 0;
            for (int i = 0; i < 4; i++) {
                int d = ((a >> (4 * i)) & 0xF) - ((b >> (4 * i)) & 0xF) + c - 1;
                if (d < 0) { d += 10; c = 0; } else c = 1;
                res |= (uint32_t)(d & 0xF) << (4 * i);
            }
            r = res; s->fC = c; s->fV = 0;
        }
        s->A = r & 0xFFFF; NZ16(r);
    }
}
static inline void do_cmp(Sim *s, uint16_t reg, int w8, uint16_t v) {
    if (w8) { unsigned r = (reg & 0xFF) - (v & 0xFF); s->fC = (reg & 0xFF) >= (v & 0xFF); NZ8(r); }
    else { uint32_t r = reg - v; s->fC = reg >= v; NZ16(r); }
}
static inline uint16_t do_asl(Sim *s, uint16_t v) { if (s->fM) { s->fC = (v >> 7) & 1; v = (v << 1) & 0xFF; NZ8(v);} else { s->fC = (v >> 15) & 1; v = (v << 1) & 0xFFFF; NZ16(v);} return v; }
static inline uint16_t do_lsr(Sim *s, uint16_t v) { s->fC = v & 1; v >>= 1; NZM(v); return v; }
static inline uint16_t do_rol(Sim *s, uint16_t v) { int c = s->fC; if (s->fM) { s->fC = (v >> 7) & 1; v = ((v << 1) | c) & 0xFF; NZ8(v);} else { s->fC = (v >> 15) & 1; v = ((v << 1) | c) & 0xFFFF; NZ16(v);} return v; }
static inline uint16_t do_ror(Sim *s, uint16_t v) { int c = s->fC; s->fC = v & 1; v = (v >> 1) | (c << (s->fM ? 7 : 15)); NZM(v); return v; }
static inline uint16_t do_inc(Sim *s, uint16_t v) { v++; if (s->fM) v &= 0xFF; NZM(v); return v; }
static inline uint16_t do_dec(Sim *s, uint16_t v) { v--; if (s->fM) v &= 0xFF; NZM(v); return v; }

static void interrupt(Sim *s, uint16_t vec) {
    if (s->fE) { push8(s, s->PB); } else push8(s, s->PB);
    push16(s, s->PC); push8(s, getP(s));
    s->fI = 1; s->fD = 0; s->PB = 0;
    s->PC = rd16(s, vec);
}

/* run one instruction (assumes hooks/stop handled by caller) */
static int step(Sim *s) {
    uint8_t op = fetch8(s);
    uint32_t ea; uint16_t v; int b0;
    tick(s, s->exact ? IOT[op] * 6u : 6u);
    s->instr++;
#define RDM(ea,bank0)  (s->fM ? (uint16_t)rd8(s, ea) : rdw(s, ea, bank0))
#define WRM(ea,val,bank0) do { if (s->fM) wr8(s, ea, (val) & 0xFF); else wrw(s, ea, val, bank0); } while (0)
#define IMMM()  (s->fM ? (uint16_t)fetch8(s) : fetch16(s))
#define IMMX()  (s->fX ? (uint16_t)fetch8(s) : fetch16(s))
#define SETA(val) do { if (s->fM) s->A = (s->A & 0xFF00) | ((val) & 0xFF); else s->A = (val); } while (0)
#define XC() do { if (s->exact && s->xcross) tick(s, 6); } while (0)
#define G1(base, STMT) \
    case base+0x09: v = IMMM(); STMT; break; \
    case base+0x05: ea = ea_dp(s);   v = RDM(ea,1); STMT; break; \
    case base+0x15: ea = ea_dpx(s);  v = RDM(ea,1); STMT; break; \
    case base+0x0D: ea = ea_abs(s);  v = RDM(ea,0); STMT; break; \
    case base+0x1D: ea = ea_absx(s); XC(); v = RDM(ea,0); STMT; break; \
    case base+0x19: ea = ea_absy(s); XC(); v = RDM(ea,0); STMT; break; \
    case base+0x01: ea = ea_indx(s); v = RDM(ea,0); STMT; break; \
    case base+0x11: ea = ea_indy(s); XC(); v = RDM(ea,0); STMT; break; \
    case base+0x12: ea = ea_ind(s);  v = RDM(ea,0); STMT; break; \
    case base+0x03: ea = ea_sr(s);   v = RDM(ea,1); STMT; break; \
    case base+0x13: ea = ea_sry(s);  v = RDM(ea,0); STMT; break; \
    case base+0x07: ea = ea_lind(s); v = RDM(ea,0); STMT; break; \
    case base+0x17: ea = ea_lindy(s);v = RDM(ea,0); STMT; break; \
    case base+0x0F: ea = ea_long(s); v = RDM(ea,0); STMT; break; \
    case base+0x1F: ea = ea_longx(s);v = RDM(ea,0); STMT; break;
#define G1ST(base, VAL) \
    case base+0x05: ea = ea_dp(s);   WRM(ea,VAL,1); break; \
    case base+0x15: ea = ea_dpx(s);  WRM(ea,VAL,1); break; \
    case base+0x0D: ea = ea_abs(s);  WRM(ea,VAL,0); break; \
    case base+0x1D: ea = ea_absx(s); WRM(ea,VAL,0); break; \
    case base+0x19: ea = ea_absy(s); WRM(ea,VAL,0); break; \
    case base+0x01: ea = ea_indx(s); WRM(ea,VAL,0); break; \
    case base+0x11: ea = ea_indy(s); WRM(ea,VAL,0); break; \
    case base+0x12: ea = ea_ind(s);  WRM(ea,VAL,0); break; \
    case base+0x03: ea = ea_sr(s);   WRM(ea,VAL,1); break; \
    case base+0x13: ea = ea_sry(s);  WRM(ea,VAL,0); break; \
    case base+0x07: ea = ea_lind(s); WRM(ea,VAL,0); break; \
    case base+0x17: ea = ea_lindy(s);WRM(ea,VAL,0); break; \
    case base+0x0F: ea = ea_long(s); WRM(ea,VAL,0); break; \
    case base+0x1F: ea = ea_longx(s);WRM(ea,VAL,0); break;
/* read-modify-write: acc form + dp, dp,x, abs, abs,x */
#define RMW(base, FN) \
    case base+0x0A-0x0A+0: break;
#define RMWOP(opA, opdp, opdpx, opabs, opabsx, FN) \
    case opA: SETA(FN(s, s->fM ? (s->A & 0xFF) : s->A)); break; \
    case opdp:   ea = ea_dp(s);   v = RDM(ea,1); v = FN(s, v); WRM(ea,v,1); break; \
    case opdpx:  ea = ea_dpx(s);  v = RDM(ea,1); v = FN(s, v); WRM(ea,v,1); break; \
    case opabs:  ea = ea_abs(s);  v = RDM(ea,0); v = FN(s, v); WRM(ea,v,0); break; \
    case opabsx: ea = ea_absx(s); v = RDM(ea,0); v = FN(s, v); WRM(ea,v,0); break;
#define BR(cond) do { int8_t d = (int8_t)fetch8(s); if (cond) { s->PC += d; tick(s, 6); } } while (0)

    switch (op) {
    G1(0x00, { SETA((s->A | v)); NZM(s->A); })              /* ORA */
    G1(0x20, { SETA((s->A & v)); NZM(s->A); })              /* AND */
    G1(0x40, { SETA((s->A ^ v)); NZM(s->A); })              /* EOR */
    G1(0x60, { do_adc(s, v); })                              /* ADC */
    G1(0xA0, { SETA(v); NZM(v); })                           /* LDA */
    G1(0xC0, { do_cmp(s, s->A, s->fM, v); })                 /* CMP */
    G1(0xE0, { do_sbc(s, v); })                              /* SBC */
    G1ST(0x80, s->A)                                         /* STA */
    RMWOP(0x0A, 0x06, 0x16, 0x0E, 0x1E, do_asl)
    RMWOP(0x2A, 0x26, 0x36, 0x2E, 0x3E, do_rol)
    RMWOP(0x4A, 0x46, 0x56, 0x4E, 0x5E, do_lsr)
    RMWOP(0x6A, 0x66, 0x76, 0x6E, 0x7E, do_ror)
    RMWOP(0x3A, 0xC6, 0xD6, 0xCE, 0xDE, do_dec)
    RMWOP(0x1A, 0xE6, 0xF6, 0xEE, 0xFE, do_inc)
    case 0x04: ea = ea_dp(s);  v = RDM(ea,1); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; WRM(ea, v | s->A, 1); break; /* TSB */
    case 0x0C: ea = ea_abs(s); v = RDM(ea,0); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; WRM(ea, v | s->A, 0); break;
    case 0x14: ea = ea_dp(s);  v = RDM(ea,1); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; WRM(ea, v & ~s->A, 1); break; /* TRB */
    case 0x1C: ea = ea_abs(s); v = RDM(ea,0); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; WRM(ea, v & ~s->A, 0); break;
#define BITOP(code, EAEXPR, B0) case code: ea = EAEXPR; v = RDM(ea,B0); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; \
        if (s->fM) { s->fN = (v >> 7) & 1; s->fV = (v >> 6) & 1; } else { s->fN = (v >> 15) & 1; s->fV = (v >> 14) & 1; } break;
    BITOP(0x24, ea_dp(s), 1) BITOP(0x34, ea_dpx(s), 1) BITOP(0x2C, ea_abs(s), 0) BITOP(0x3C, ea_absx(s), 0)
    case 0x89: v = IMMM(); s->fZ = (v & s->A & (s->fM?0xFF:0xFFFF)) == 0; break;
    /* CPX / CPY */
    case 0xE0: v = IMMX(); do_cmp(s, s->X, s->fX, v); break;
    case 0xE4: ea = ea_dp(s);  v = s->fX ? rd8(s,ea) : rd16_b0(s,ea); do_cmp(s, s->X, s->fX, v); break;
    case 0xEC: ea = ea_abs(s); v = s->fX ? rd8(s,ea) : rd16(s,ea);   do_cmp(s, s->X, s->fX, v); break;
    case 0xC0: v = IMMX(); do_cmp(s, s->Y, s->fX, v); break;
    case 0xC4: ea = ea_dp(s);  v = s->fX ? rd8(s,ea) : rd16_b0(s,ea); do_cmp(s, s->Y, s->fX, v); break;
    case 0xCC: ea = ea_abs(s); v = s->fX ? rd8(s,ea) : rd16(s,ea);   do_cmp(s, s->Y, s->fX, v); break;
    /* LDX / LDY */
#define RDX(ea,b0) (s->fX ? (uint16_t)rd8(s, ea) : rdw(s, ea, b0))
    case 0xA2: v = IMMX(); s->X = v; NZX(v); break;
    case 0xA6: ea = ea_dp(s);  v = RDX(ea,1); s->X = v; NZX(v); break;
    case 0xB6: ea = ea_dpy(s); v = RDX(ea,1); s->X = v; NZX(v); break;
    case 0xAE: ea = ea_abs(s); v = RDX(ea,0); s->X = v; NZX(v); break;
    case 0xBE: ea = ea_absy(s); XC(); v = RDX(ea,0); s->X = v; NZX(v); break;
    case 0xA0: v = IMMX(); s->Y = v; NZX(v); break;
    case 0xA4: ea = ea_dp(s);  v = RDX(ea,1); s->Y = v; NZX(v); break;
    case 0xB4: ea = ea_dpx(s); v = RDX(ea,1); s->Y = v; NZX(v); break;
    case 0xAC: ea = ea_abs(s); v = RDX(ea,0); s->Y = v; NZX(v); break;
    case 0xBC: ea = ea_absx(s); XC(); v = RDX(ea,0); s->Y = v; NZX(v); break;
#define WRX(ea,val,b0) do { if (s->fX) wr8(s, ea, (val) & 0xFF); else wrw(s, ea, val, b0); } while (0)
    case 0x86: ea = ea_dp(s);  WRX(ea, s->X, 1); break;
    case 0x96: ea = ea_dpy(s); WRX(ea, s->X, 1); break;
    case 0x8E: ea = ea_abs(s); WRX(ea, s->X, 0); break;
    case 0x84: ea = ea_dp(s);  WRX(ea, s->Y, 1); break;
    case 0x94: ea = ea_dpx(s); WRX(ea, s->Y, 1); break;
    case 0x8C: ea = ea_abs(s); WRX(ea, s->Y, 0); break;
    case 0x64: ea = ea_dp(s);  WRM(ea, 0, 1); break;
    case 0x74: ea = ea_dpx(s); WRM(ea, 0, 1); break;
    case 0x9C: ea = ea_abs(s); WRM(ea, 0, 0); break;
    case 0x9E: ea = ea_absx(s);WRM(ea, 0, 0); break;
    /* branches */
    case 0x10: BR(!s->fN); break;  case 0x30: BR(s->fN); break;
    case 0x50: BR(!s->fV); break;  case 0x70: BR(s->fV); break;
    case 0x80: BR(1); break;
    case 0x90: BR(!s->fC); break;  case 0xB0: BR(s->fC); break;
    case 0xD0: BR(!s->fZ); break;  case 0xF0: BR(s->fZ); break;
    case 0x82: { int16_t d = (int16_t)fetch16(s); s->PC += d; } break;
    /* jumps */
    case 0x4C: s->PC = fetch16(s); break;
    case 0x5C: { uint32_t t = fetch24(s); s->PC = t & 0xFFFF; s->PB = t >> 16; } break;
    case 0x6C: { uint16_t p = fetch16(s); s->PC = rd16_b0(s, p); } break;
    case 0x7C: { uint16_t p = fetch16(s) + IDX(s->X); s->PC = rd16(s, ((uint32_t)s->PB << 16) | p); } break;
    case 0xDC: { uint16_t p = fetch16(s); uint32_t t = rd24_b0(s, p); s->PC = t & 0xFFFF; s->PB = t >> 16; } break;
    case 0x20: { uint16_t t = fetch16(s); push16(s, s->PC - 1); s->PC = t; } break;
    case 0x22: { uint32_t t = fetch24(s); push8(s, s->PB); push16(s, s->PC - 1); s->PC = t & 0xFFFF; s->PB = t >> 16; } break;
    case 0xFC: { uint16_t p = fetch16(s) + IDX(s->X); uint16_t t = rd16(s, ((uint32_t)s->PB << 16) | p); push16(s, s->PC - 1); s->PC = t; } break;
    case 0x60: s->PC = pull16(s) + 1; break;
    case 0x6B: s->PC = pull16(s) + 1; s->PB = pull8(s); break;
    case 0x40: { uint8_t p = pull8(s); s->PC = pull16(s); s->PB = pull8(s); setP(s, p); } break;
    case 0x00: fetch8(s); interrupt(s, 0xFFE6); break;
    case 0x02: fetch8(s); interrupt(s, 0xFFE4); break;
    /* stack */
    case 0x48: if (s->fM) push8(s, s->A); else push16(s, s->A); break;
    case 0x68: if (s->fM) { v = pull8(s); SETA(v); NZ8(v); } else { v = pull16(s); s->A = v; NZ16(v); } break;
    case 0xDA: if (s->fX) push8(s, s->X); else push16(s, s->X); break;
    case 0xFA: if (s->fX) { v = pull8(s); s->X = v; NZ8(v); } else { v = pull16(s); s->X = v; NZ16(v); } break;
    case 0x5A: if (s->fX) push8(s, s->Y); else push16(s, s->Y); break;
    case 0x7A: if (s->fX) { v = pull8(s); s->Y = v; NZ8(v); } else { v = pull16(s); s->Y = v; NZ16(v); } break;
    case 0x08: push8(s, getP(s)); break;
    case 0x28: setP(s, pull8(s)); break;
    case 0x8B: push8(s, s->DB); break;
    case 0xAB: s->DB = pull8(s); NZ8(s->DB); break;
    case 0x0B: push16(s, s->D); break;
    case 0x2B: s->D = pull16(s); NZ16(s->D); break;
    case 0x4B: push8(s, s->PB); break;
    case 0xF4: v = fetch16(s); push16(s, v); break;
    case 0xD4: v = rd16_b0(s, (uint16_t)(s->D + fetch8(s))); push16(s, v); break;
    case 0x62: { int16_t d = (int16_t)fetch16(s); push16(s, s->PC + d); } break;
    /* flags */
    case 0x18: s->fC = 0; break; case 0x38: s->fC = 1; break;
    case 0x58: s->fI = 0; break; case 0x78: s->fI = 1; break;
    case 0xB8: s->fV = 0; break;
    case 0xD8: s->fD = 0; break; case 0xF8: s->fD = 1; break;
    case 0xC2: v = fetch8(s); setP(s, getP(s) & ~v); break;
    case 0xE2: v = fetch8(s); setP(s, getP(s) | v); break;
    case 0xFB: { int e = s->fE; s->fE = s->fC; s->fC = e; if (s->fE) { s->fM = 1; s->fX = 1; s->X &= 0xFF; s->Y &= 0xFF; s->S = 0x100 | (s->S & 0xFF); } } break;
    /* transfers */
    case 0xAA: s->X = s->fX ? (s->A & 0xFF) : s->A; NZX(s->X); break;
    case 0xA8: s->Y = s->fX ? (s->A & 0xFF) : s->A; NZX(s->Y); break;
    case 0x8A: v = s->fX ? (s->X & 0xFF) : s->X; SETA(v); NZM(v); break;
    case 0x98: v = s->fX ? (s->Y & 0xFF) : s->Y; SETA(v); NZM(v); break;
    case 0xBA: s->X = s->fX ? (s->S & 0xFF) : s->S; NZX(s->X); break;
    case 0x9A: s->S = s->fE ? (0x100 | (s->X & 0xFF)) : (s->fX ? (s->X & 0xFF) : s->X); break;
    case 0x9B: s->Y = s->X; NZX(s->Y); break;
    case 0xBB: s->X = s->Y; NZX(s->X); break;
    case 0x5B: s->D = s->A; NZ16(s->D); break;
    case 0x7B: s->A = s->D; NZ16(s->A); break;
    case 0x1B: s->S = s->fE ? (0x100 | (s->A & 0xFF)) : s->A; break;
    case 0x3B: s->A = s->S; NZ16(s->A); break;
    case 0xEB: s->A = (s->A >> 8) | (s->A << 8); NZ8(s->A); break;
    case 0xE8: s->X++; if (s->fX) s->X &= 0xFF; NZX(s->X); break;
    case 0xC8: s->Y++; if (s->fX) s->Y &= 0xFF; NZX(s->Y); break;
    case 0xCA: s->X--; if (s->fX) s->X &= 0xFF; NZX(s->X); break;
    case 0x88: s->Y--; if (s->fX) s->Y &= 0xFF; NZX(s->Y); break;
    case 0xEA: break;
    case 0x42: fetch8(s); break;
    case 0xCB: s->waiting = 1; break;
    case 0xDB: s->stopped = 1; break;
    case 0x44: case 0x54: {   /* MVP / MVN */
        uint8_t dst = fetch8(s), src = fetch8(s);
        s->DB = dst;
        for (;;) {
            uint8_t x = rd8(s, ((uint32_t)src << 16) | (s->fX ? (s->X & 0xFF) : s->X));
            wr8(s, ((uint32_t)dst << 16) | (s->fX ? (s->Y & 0xFF) : s->Y), x);
            if (op == 0x54) { s->X++; s->Y++; } else { s->X--; s->Y--; }
            if (s->fX) { s->X &= 0xFF; s->Y &= 0xFF; }
            s->A--;
            if (s->A == 0xFFFF) break;
            if (s->exact) tick(s, 3 * bus_cost(s, ((uint32_t)s->PB << 16) | s->PC) + 12);   /* the next byte re-fetches opcode+operands (7 cycles per byte) */
        }
    } break;
    default: return -1;
    }
    (void)b0;
    return 0;
}

/* ------------------------------------------------------------- host API */
Sim *sim_new(void) { Sim *s = calloc(1, sizeof(Sim)); s->stop_pc = 0xFFFFFFFFu; s->fE = 0; s->fM = 1; s->fX = 1; s->fI = 1; return s; }
void sim_free(Sim *s) { free(s); }
/* power-on state of the timing-relevant hardware (frame phase, refresh, HDMA, DMA, interrupt flags, arithmetic unit) so that a reused Sim
   starts every battle exactly as a fresh one would; WRAM, hooks, ROM, the SPC700 and statistics are left alone */
void sim_reset_hw(Sim *s) {
    s->frame_cyc = 0; s->cyc_rem = 0; s->refresh_line = 0; s->xcross = 0; s->nb = 0;
    s->memsel = 0; s->hdma_en = 0; memset(s->hdma_on, 0, sizeof s->hdma_on); memset(s->hdma_lc, 0, sizeof s->hdma_lc);
    memset(s->hdma_rep, 0, sizeof s->hdma_rep); memset(s->hdma_xfer, 0, sizeof s->hdma_xfer); memset(s->hdma_a2a, 0, sizeof s->hdma_a2a); memset(s->hdma_ind, 0, sizeof s->hdma_ind);
    s->hv_flip = 0; s->hv_h = s->hv_v = 0; s->nmitimen = 0; s->rdnmi = 0; s->in_nmi_unused = 0; s->stopped = 0; s->waiting = 0; s->nmi_pending = 0;
    s->wmadd = 0; s->wrmpy_res = s->wrdiv_q = s->wrdiv_r = s->dividend = 0; s->wrmpya = 0; memset(s->joy, 0, sizeof s->joy);
    memset(s->apu_port, 0, sizeof s->apu_port); memset(s->apu_in, 0, sizeof s->apu_in); memset(s->dma, 0, sizeof s->dma);
}
void sim_copy(Sim *dst, const Sim *src) { memcpy(dst, src, sizeof(Sim)); }
Sim *sim_clone(const Sim *src) { Sim *s = malloc(sizeof(Sim)); memcpy(s, src, sizeof(Sim)); s->tr = 0; return s; }
size_t sim_size(void) { return sizeof(Sim); }
uint8_t *sim_wram(Sim *s) { return s->wram; }

/* hook kinds: 0 stop (host handles), 1 HLE RTL, 2 HLE RTS */
int sim_hook_add(Sim *s, uint32_t addr, int kind, uint32_t a1, uint32_t a2) {
    uint32_t o; if (s->nhook >= MAXHOOK || !rom_off(addr, &o)) return -1;
    s->hook_addr[s->nhook] = addr; s->hook_kind[s->nhook] = kind; s->hook_arg1[s->nhook] = a1; s->hook_arg2[s->nhook] = a2; HOOKMAP[o] = s->nhook + 1; return s->nhook++;
}
void sim_hooks_clear(Sim *s) {
    for (int i = 0; i < s->nhook; i++) { uint32_t o; if (rom_off(s->hook_addr[i], &o)) HOOKMAP[o] = 0; }
    s->nhook = 0;
}

/* regs get/set: idx 0..: A X Y S D PC DB PB P E */
uint32_t sim_reg(Sim *s, int i) {
    switch (i) { case 0: return s->A; case 1: return s->X; case 2: return s->Y; case 3: return s->S; case 4: return s->D;
    case 5: return s->PC; case 6: return s->DB; case 7: return s->PB; case 8: return getP(s); case 9: return s->fE;
    case 10: return (uint32_t)s->instr; case 11: return s->frames; case 12: return s->frame_cyc; case 13: return (uint32_t)(s->instr >> 32);
    case 14: return s->nmitimen; case 15: return s->stopped; case 17: return s->nb; case 18: return s->exact; case 19: return s->memsel; case 20: return (uint32_t)s->cyc_total; case 21: return (uint32_t)(s->cyc_total >> 32); }
    return 0;
}
void sim_setreg(Sim *s, int i, uint32_t v) {
    switch (i) { case 0: s->A = v; break; case 1: s->X = v; break; case 2: s->Y = v; break; case 3: s->S = v; break; case 4: s->D = v; break;
    case 5: s->PC = v; break; case 6: s->DB = v; break; case 7: s->PB = v; break; case 8: setP(s, v); break; case 9: s->fE = v; break;
    case 14: s->nmitimen = v; break; case 15: s->stopped = v; break; case 12: s->frame_cyc = v; resync_refresh(s); break; case 16: s->cyc_scale = v; break; case 17: s->nb = v; break; case 18: s->exact = v; break; case 19: s->memsel = v; break; }
}
uint32_t sim_hooklog(Sim *s, uint32_t *out) { for (uint32_t i = 0; i < s->hooklogn; i++) { out[3*i] = s->hooklog[i][0]; out[3*i+1] = s->hooklog[i][1]; out[3*i+2] = s->hooklog[i][2]; } return s->hooklogn; }
void sim_spc_enable(Sim *s, int on) {
    s->spc_on = on;
    if (on) { spc_reset(&s->spc); s->spc_base = s->spc.cyc - s->cyc_total * 15664ull / 328125ull; s->spc.cyc = 0; s->spc_base = 0 - s->cyc_total * 15664ull / 328125ull; }
}
/* import a running SPC state: v = pc,a,x,y,sp,psw, ctl,dspaddr,test, tgt[3], cnt[3], stage[3], cyc phase (mod 128), to_spc (packed), from_spc (packed) */
void sim_spc_load(Sim *s, const uint8_t *ram, const uint8_t *dsp, const uint32_t *v) {
    Spc *p = &s->spc;
    spc_reset(p); memcpy(p->ram, ram, 65536); memcpy(p->dsp, dsp, 128);
    p->pc = v[0]; p->a = v[1]; p->x = v[2]; p->y = v[3]; p->sp = v[4]; p->psw = v[5];
    p->ctl = v[6] & 0xFF; p->dspaddr = v[7]; p->test = v[8];
    for (int t = 0; t < 3; t++) { p->tgt[t] = v[9 + t]; p->cnt[t] = v[12 + t]; p->stage[t] = v[15 + t]; p->en[t] = (p->ctl >> t) & 1; }
    p->cyc = (1ull << 20) + (v[18] & 127);
    for (int i = 0; i < 4; i++) { p->to_spc[i] = (v[19] >> (8 * i)) & 0xFF; p->from_spc[i] = (v[20] >> (8 * i)) & 0xFF; }
    s->spc_on = 1; s->spc_base = p->cyc - s->cyc_total * 15664ull / 328125ull;
}
uint8_t *sim_spc_ram(Sim *s) { return s->spc.ram; }
void sim_spc_info(Sim *s, uint32_t *o) { o[0] = s->spc.pc; o[1] = s->spc.a | s->spc.x << 8 | s->spc.y << 16 | s->spc.sp << 24; o[2] = s->spc.psw; o[3] = (uint32_t)s->spc.cyc; o[4] = s->spc.stopped; o[5] = s->spc.to_spc[0] | s->spc.to_spc[1] << 8 | s->spc.to_spc[2] << 16 | s->spc.to_spc[3] << 24; o[6] = s->spc.from_spc[0] | s->spc.from_spc[1] << 8 | s->spc.from_spc[2] << 16 | s->spc.from_spc[3] << 24; }
void sim_prof(Sim *s, int on, uint64_t *out) { s->prof_on = on; if (out) { for (int i = 0; i < 256; i++) { out[i] = s->pcyc[i]; out[256 + i] = s->pcnt[i]; } } if (on == 2) { memset(s->pcyc, 0, sizeof s->pcyc); memset(s->pcnt, 0, sizeof s->pcnt); s->prof_on = 1; } }
void sim_trace(Sim *s, uint32_t every, uint32_t cap) { free(s->tr); s->tr = every ? malloc(8ull * cap) : 0; s->tr_n = 0; s->tr_cap = cap; s->tr_every = every ? every : 1; }
uint32_t sim_trace_get(Sim *s, uint64_t *out) { for (uint32_t i = 0; i < s->tr_n; i++) out[i] = s->tr[i]; uint32_t n = s->tr_n; s->tr_n = 0; return n; }
void sim_io_write(Sim *s, uint32_t addr, uint8_t v) { io_write(s, addr & 0xFFFF, v); }   /* host-side register poke (no timing) */
void sim_dma_stats(Sim *s, uint32_t *out, int reset) { for (int i = 0; i < 256; i++) { out[i] = s->dma_bytes[i]; out[256 + i] = s->dma_calls[i]; } if (reset) { memset(s->dma_bytes, 0, sizeof s->dma_bytes); memset(s->dma_calls, 0, sizeof s->dma_calls); } }
int sim_step1(Sim *s) { return step(s); }
void sim_set_joy(Sim *s, int pad, uint16_t v) { s->joy[pad & 3] = v; }
void sim_skip_hook(Sim *s) { s->skip_hook = 1; }
void sim_set_stop_pc(Sim *s, uint32_t pc) { s->stop_pc = pc; }
void sim_ring(Sim *s, uint32_t *out) { for (int i = 0; i < 256; i++) out[i] = s->ring[(s->ringpos + i) & 255]; }
uint8_t sim_peek(Sim *s, uint32_t a) { uint64_t c = s->cyc_total; uint32_t f = s->frame_cyc; uint8_t v = rd8(s, a); s->cyc_total = c; s->frame_cyc = f; return v; }

/* push a JSL-style return to stop_pc and jump to addr. */
void sim_call_long(Sim *s, uint32_t addr, uint32_t ret_pc24) {
    s->stop_pc = ret_pc24;
    push8(s, ret_pc24 >> 16); push16(s, (ret_pc24 - 1) & 0xFFFF);
    s->PB = addr >> 16; s->PC = addr & 0xFFFF;
}

/* Run until: stop_pc reached (1), hook (100+i; PC left AT the hook address), instr limit (2), frame limit (3),
 * STP (4), bad opcode (5).  max_frames==0: no frame limit. */
int sim_run(Sim *s, uint64_t max_instr, uint32_t max_frames) {
    uint64_t n = 0; uint32_t f0 = s->frames;
    int resume_hook = s->skip_hook; s->skip_hook = 0;
    for (;;) {
        if (s->stopped) return 4;
        if (s->nmi_pending) { s->nmi_pending = 0; interrupt(s, 0xFFEA); resume_hook = 0; }   /* NMI left pending by the previous frame-end return */
        uint32_t pc24 = ((uint32_t)s->PB << 16) | s->PC;
        if (pc24 == s->stop_pc) return 1;
        if (!resume_hook) {
            uint32_t o;
            if (HOOKMAP && rom_off(pc24, &o) && HOOKMAP[o]) {
                int h = HOOKMAP[o] - 1;
                if (s->hook_kind[h] == 1) { if (s->hooklogn < 256) { s->hooklog[s->hooklogn][0] = s->frames; s->hooklog[s->hooklogn][1] = s->wram[0x1D00] | (s->wram[0x1D01] << 8) | (s->wram[0x1D02] << 16); s->hooklog[s->hooklogn][2] = s->frame_cyc; s->hooklogn++; } s->PC = pull16(s) + 1; s->PB = pull8(s); tick(s, s->hook_arg1[h]); continue; }
                if (s->hook_kind[h] == 2) { s->PC = pull16(s) + 1; continue; }
                if (s->hook_kind[h] == 3) {
                    /* spin-wait loop head: if the wait condition (WRAM[arg1] | WRAM[arg2]) holds, nothing can change until the next
                     * NMI, so jump the clock there instead of executing the spin.  The loop then runs its normal exit test. */
                    if ((s->wram[s->hook_arg1[h] & 0x1FFFF] | s->wram[s->hook_arg2[h] & 0x1FFFF]) && (s->nmitimen & 0x80)) {
                        uint32_t need = s->frame_cyc < VBLANK_CYC ? VBLANK_CYC - s->frame_cyc : FRAME_CYC - s->frame_cyc + VBLANK_CYC;
                        s->frame_cyc += need; s->cyc_total += need; resync_refresh(s);
                        goto after_step;
                    }
                    resume_hook = 1; continue;       /* condition false: run the loop's own exit test */
                }
                return 100 + h;
            }
        }
        resume_hook = 0;
        if (n >= max_instr) return 2;
        if (0) { after_step:; resume_hook = 1; goto frame_timing; }
        s->ring[s->ringpos++ & 255] = pc24;
        if (s->waiting) {
            /* WAI: jump to the next vblank */
            uint32_t need = s->frame_cyc < VBLANK_CYC ? VBLANK_CYC - s->frame_cyc : FRAME_CYC - s->frame_cyc + VBLANK_CYC;
            s->frame_cyc += need; s->cyc_total += need; s->waiting = 0; resync_refresh(s);
        } else {
            uint32_t fc0 = s->frame_cyc;
            uint64_t ct0 = s->cyc_total; uint8_t op0 = s->prof_on ? peek8(s, ((uint32_t)s->PB << 16) | s->PC) : 0;
            if (step(s) < 0) { s->PC--; return 5; }
            if (s->prof_on) { s->pcyc[op0] += s->cyc_total - ct0; s->pcnt[op0]++; }
            if (s->tr && (s->instr % s->tr_every) == 0 && s->tr_n < s->tr_cap) s->tr[s->tr_n++] = (s->cyc_total << 24) | ((uint32_t)s->PB << 16) | s->PC;
            n++;
            if (s->cyc_scale) {   /* crude model of what the interpreter does not charge (internal cycles, DRAM refresh) */
                uint32_t x = (s->frame_cyc - fc0) * s->cyc_scale + s->cyc_rem;
                s->cyc_rem = x & 1023; s->frame_cyc += x >> 10; s->cyc_total += x >> 10;
            }
        }
        frame_timing:
        /* frame timing */
        if (s->exact) {   /* DRAM refresh: the CPU is stalled for 40 master cycles at H=538 of every scanline */
            sync_lines(s, 1);
        }
        if (s->frame_cyc >= FRAME_CYC) { s->frame_cyc -= FRAME_CYC; s->nmi_pending = 0; s->rdnmi = 0; s->in_nmi_unused = 0; s->refresh_line = 0; if (s->exact) { hdma_frame_init(s); sync_lines(s, 1); } }
        if (!s->in_nmi_unused && s->frame_cyc >= VBLANK_CYC) {
            s->in_nmi_unused = 1; s->rdnmi = 1; s->frames++;      /* a frame ends where vblank starts, like the libretro core's retro_run */
            if (s->nmitimen & 0x80) s->nmi_pending = 1;
        }
        if (max_frames && s->frames - f0 >= max_frames) return 3;     /* frame ends at vblank, before the NMI is taken */
        if (s->nmi_pending) {
            s->nmi_pending = 0;
            interrupt(s, 0xFFEA);
        }
    }
}
