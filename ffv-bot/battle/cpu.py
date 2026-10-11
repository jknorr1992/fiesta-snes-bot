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
L.sim_hook_add.argtypes = [vp, u32, ctypes.c_int]; L.sim_hooks_clear.argtypes = [vp]
L.sim_call_long.argtypes = [vp, u32, u32]; L.sim_set_stop_pc.argtypes = [vp, u32]
L.sim_set_joy.argtypes = [vp, ctypes.c_int, ctypes.c_uint16]; L.sim_skip_hook.argtypes = [vp]
L.sim_peek.restype = ctypes.c_uint8; L.sim_peek.argtypes = [vp, u32]
L.sim_ring.argtypes = [vp, ctypes.POINTER(u32)]
L.sim_size.restype = ctypes.c_size_t

_rom_buf = None
def set_rom(data):
    global _rom_buf
    _rom_buf = ctypes.create_string_buffer(bytes(data), len(data))   # keep alive
    L.rom_set(_rom_buf, len(data))

R_A, R_X, R_Y, R_S, R_D, R_PC, R_DB, R_PB, R_P, R_E, R_INSTR, R_FRAMES, R_FRAMECYC, R_INSTR_HI, R_NMITIMEN, R_STOPPED = range(16)
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
    def clone(self): return Machine(L.sim_clone(self.p))
    def copy_from(self, o): L.sim_copy(self.p, o.p)
    def reg(self, i): return L.sim_reg(self.p, i)
    def setreg(self, i, v): L.sim_setreg(self.p, i, v)
    def rb(self, a): return self.wram[a & 0x1FFFF]
    def rw(self, a): w = self.wram; return w[a] | (w[a + 1] << 8)
    def run(self, max_instr=2**62, max_frames=0): return L.sim_run(self.p, max_instr, max_frames)
    def hook(self, addr, kind=0): return L.sim_hook_add(self.p, addr, kind)
    def clear_hooks(self): L.sim_hooks_clear(self.p)
    def call_long(self, addr, ret): L.sim_call_long(self.p, addr, ret)
    def skip_hook(self): L.sim_skip_hook(self.p)
    def set_stop_pc(self, pc): L.sim_set_stop_pc(self.p, pc)
    def joy(self, pad, v): L.sim_set_joy(self.p, pad, v)
    def ring(self):
        a = (u32 * 256)(); L.sim_ring(self.p, a); return list(a)
    @property
    def pc24(self): return (self.reg(R_PB) << 16) | self.reg(R_PC)
