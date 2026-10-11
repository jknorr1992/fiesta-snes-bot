"""ctypes wrapper around cpu65816.c (a 65C816 interpreter with just enough SNES hardware to run the FFV battle program
from the ROM).  The shared library is (re)built on demand next to this file."""
import ctypes, os, subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "cpu65816.c")
_LIB = os.path.join(_HERE, "_build", "libcpu65816.so")

def _build():
    os.makedirs(os.path.dirname(_LIB), exist_ok=True)
    if not os.path.exists(_LIB) or os.path.getmtime(_LIB) < os.path.getmtime(_SRC):
        subprocess.check_call(["gcc", "-O2", "-shared", "-fPIC", "-o", _LIB, _SRC])
    return ctypes.CDLL(_LIB)

L = _build()
vp, u32, u64 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint64
L.sim_new.restype = vp; L.sim_clone.restype = vp; L.sim_clone.argtypes = [vp]
L.sim_free.argtypes = [vp]; L.sim_copy.argtypes = [vp, vp]
L.sim_wram.restype = ctypes.POINTER(ctypes.c_uint8); L.sim_wram.argtypes = [vp]
L.sim_run.restype = ctypes.c_int; L.sim_run.argtypes = [vp, u64, u32]
L.sim_reg.restype = u32; L.sim_reg.argtypes = [vp, ctypes.c_int]
L.sim_setreg.argtypes = [vp, ctypes.c_int, u32]
L.sim_hook_add.argtypes = [vp, u32, ctypes.c_int, u32, u32]; L.sim_hooks_clear.argtypes = [vp]
L.sim_call_long.argtypes = [vp, u32, u32]; L.sim_set_stop_pc.argtypes = [vp, u32]
L.sim_set_joy.argtypes = [vp, ctypes.c_int, ctypes.c_uint16]; L.sim_skip_hook.argtypes = [vp]
L.sim_peek.restype = ctypes.c_uint8; L.sim_peek.argtypes = [vp, u32]
L.sim_ring.argtypes = [vp, ctypes.POINTER(u32)]
L.sim_size.restype = ctypes.c_size_t
L.sim_spc_enable.argtypes = [vp, ctypes.c_int]
L.sim_spc_ram.restype = ctypes.POINTER(ctypes.c_uint8); L.sim_spc_ram.argtypes = [vp]
L.sim_spc_info.argtypes = [vp, ctypes.POINTER(u32)]
L.sim_prof.argtypes = [vp, ctypes.c_int, ctypes.POINTER(u64)]
L.sim_trace.argtypes = [vp, u32, u32]; L.sim_trace_get.restype = u32; L.sim_trace_get.argtypes = [vp, ctypes.POINTER(u64)]
L.sim_io_write.argtypes = [vp, u32, ctypes.c_uint8]
L.sim_dma_stats.argtypes = [vp, ctypes.POINTER(u32), ctypes.c_int]
L.sim_spc_load.argtypes = [vp, ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(u32)]

_rom_buf = None
def set_rom(data):
    global _rom_buf
    _rom_buf = ctypes.create_string_buffer(bytes(data), len(data))   # keep alive
    L.rom_set(_rom_buf, len(data))

R_A, R_X, R_Y, R_S, R_D, R_PC, R_DB, R_PB, R_P, R_E, R_INSTR, R_FRAMES, R_FRAMECYC, R_INSTR_HI, R_NMITIMEN, R_STOPPED, R_CYCSCALE, R_NB, R_EXACT, R_MEMSEL, R_CYC_LO, R_CYC_HI = range(22)
RUN_RETURNED, RUN_INSTR_LIMIT, RUN_FRAME_LIMIT, RUN_STP, RUN_BADOP = 1, 2, 3, 4, 5
RUN_HOOK = 100

class Machine:
    def __init__(self, _ptr=None):
        self.p = _ptr or L.sim_new()
        self._wram = None
    def __del__(self):
        try: L.sim_free(self.p)
        except Exception: pass
    @property
    def wram(self):
        return (ctypes.c_uint8 * 0x20000).from_address(ctypes.addressof(L.sim_wram(self.p).contents))
    def spc_enable(self, on=True): L.sim_spc_enable(self.p, 1 if on else 0)
    def spc_ram(self): return L.sim_spc_ram(self.p)
    def spc_info(self):
        o = (u32 * 8)(); L.sim_spc_info(self.p, o); return list(o)
    def spc_load(self, ram, dsp, v):
        a = (u32 * len(v))(*v); L.sim_spc_load(self.p, bytes(ram), bytes(dsp), a)
    def prof(self, on=1):
        o = (u64 * 512)(); L.sim_prof(self.p, on, o); return list(o[:256]), list(o[256:])
    def trace(self, every=64, cap=1 << 20): L.sim_trace(self.p, every, cap); self._trcap = cap
    def trace_get(self):
        buf = (u64 * self._trcap)(); n = L.sim_trace_get(self.p, buf); return [(buf[i] >> 24, buf[i] & 0xFFFFFF) for i in range(n)]
    def io_write(self, addr, v): L.sim_io_write(self.p, addr, v & 0xFF)
    def dma_stats(self, reset=1):
        o = (u32 * 512)(); L.sim_dma_stats(self.p, o, reset); return {i: (o[256 + i], o[i]) for i in range(256) if o[256 + i]}   # breg -> (calls, bytes)
    def clone(self): return Machine(L.sim_clone(self.p))
    def copy_from(self, o): L.sim_copy(self.p, o.p)
    def reg(self, i): return L.sim_reg(self.p, i)
    def setreg(self, i, v): L.sim_setreg(self.p, i, v)
    def rb(self, a): return self.wram[a & 0x1FFFF]
    def rw(self, a): w = self.wram; return w[a] | (w[a + 1] << 8)
    def run(self, max_instr=2**62, max_frames=0): return L.sim_run(self.p, max_instr, max_frames)
    def hook(self, addr, kind=0, a1=0, a2=0): return L.sim_hook_add(self.p, addr, kind, a1, a2)
    def clear_hooks(self): L.sim_hooks_clear(self.p)
    def call_long(self, addr, ret): L.sim_call_long(self.p, addr, ret)
    def skip_hook(self): L.sim_skip_hook(self.p)
    def set_stop_pc(self, pc): L.sim_set_stop_pc(self.p, pc)
    def joy(self, pad, v): L.sim_set_joy(self.p, pad, v)
    def ring(self):
        a = (u32 * 256)(); L.sim_ring(self.p, a); return list(a)
    @property
    def pc24(self): return (self.reg(R_PB) << 16) | self.reg(R_PC)

def _cyc_total(self): return self.reg(R_CYC_LO) | (self.reg(R_CYC_HI) << 32)
Machine.cyc_total = property(_cyc_total)
