"""Minimal headless libretro frontend (ctypes) for running the snes9x core, used to validate the battle simulator against a real
emulator.  Not part of the bot: it never talks to RetroArch.  Load a RetroArch .state, poke WRAM, run frames, read WRAM/serialize."""
import ctypes as C, os, struct, sys

CORE = os.environ.get("SNES9X_CORE", "/mnt/project-files/retroarch-testbed/snes9x_libretro.so")
ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
POLL_CB = C.CFUNCTYPE(None)
INPUT_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)

class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p), ("size", C.c_size_t), ("meta", C.c_char_p)]

# libretro joypad ids -> SNES pad bits (as used by sim.py: B Y SELECT START UP DOWN LEFT RIGHT A X L R)
_ID_BIT = [0x8000, 0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100, 0x0080, 0x0040, 0x0020, 0x0010]

class Snes9x:
    def __init__(self, rom_path, core=CORE):
        self.lib = C.CDLL(core)
        L = self.lib
        self.pad = 0
        self._sysdir = C.c_char_p(b"/tmp"); self._savedir = C.c_char_p(b"/tmp")
        def env(cmd, data):
            cmd &= 0xFFFF
            if cmd == 3: C.cast(data, C.POINTER(C.c_bool))[0] = True; return True        # GET_CAN_DUPE
            if cmd in (10, 11, 16, 35, 36, 8, 18): return True                           # pixel fmt, input desc, variables, ...
            if cmd == 9: C.cast(data, C.POINTER(C.c_char_p))[0] = self._sysdir.value; return True
            if cmd == 31: C.cast(data, C.POINTER(C.c_char_p))[0] = self._savedir.value; return True
            return False
        self._env = ENV_CB(env)
        self._video = VIDEO_CB(lambda d, w, h, p: None)
        self._audio = AUDIO_CB(lambda l, r: None)
        self._audio_b = AUDIO_BATCH_CB(lambda d, n: n)
        self._poll = POLL_CB(lambda: None)
        self._input = INPUT_CB(self._input_state)
        L.retro_set_environment(self._env); L.retro_set_video_refresh(self._video)
        L.retro_set_audio_sample(self._audio); L.retro_set_audio_sample_batch(self._audio_b)
        L.retro_set_input_poll(self._poll); L.retro_set_input_state(self._input)
        L.retro_init()
        data = open(rom_path, "rb").read()
        self._rom = C.create_string_buffer(data, len(data))
        gi = GameInfo(rom_path.encode(), C.cast(self._rom, C.c_void_p), len(data), None)
        assert L.retro_load_game(C.byref(gi)), "retro_load_game failed"
        L.retro_get_memory_data.restype = C.c_void_p; L.retro_get_memory_size.restype = C.c_size_t
        L.retro_serialize_size.restype = C.c_size_t
        L.retro_serialize.argtypes = [C.c_void_p, C.c_size_t]; L.retro_unserialize.argtypes = [C.c_void_p, C.c_size_t]
        self.frames = 0

    def _input_state(self, port, device, index, id_):
        if port != 0 or device != 1: return 0
        return 1 if id_ < 12 and (self.pad & _ID_BIT[id_]) else 0

    @property
    def wram(self):
        return (C.c_uint8 * 0x20000).from_address(self.lib.retro_get_memory_data(2))
    def run(self, n=1):
        for _ in range(n): self.lib.retro_run(); self.frames += 1
    def serialize(self):
        n = self.lib.retro_serialize_size(); buf = C.create_string_buffer(n)
        assert self.lib.retro_serialize(buf, n); return bytes(buf)
    def unserialize(self, data):
        buf = C.create_string_buffer(bytes(data), len(data)); assert self.lib.retro_unserialize(buf, len(data))
    def load_ra_state(self, path):
        """RetroArch .state (RASTATE chunk file, optionally RZIP-compressed) -> core state."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
        import snes9x_state
        d = snes9x_state.unrzip(open(path, "rb").read())
        o = d.find(b"#!s9xsnp"); assert o >= 0
        self.unserialize(d[o:])
