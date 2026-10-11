"""FFV battle simulator: runs the game's own battle program (C2:0000, plus the battle-graphics code in C1/D9 that drives the
menu and animation timing) straight from the ROM on a 65C816 interpreter (cpu65816.c).  Nothing is re-implemented, so damage
formulas, AI scripts, ATB timing and the RNG are those of the ROM that is loaded (FF5r Clean).

PPU/APU are not emulated (their registers are sinks; DMA to them is skipped); sound calls (C4:0004) return immediately.

    sim = BattleSim()
    sim.load_wram(wram_bytes)            # field state: party at $0500, inventory at $0640/$0740, flags, gil, ...
    sim.start(encounter=0x1BB, background=0)
    while sim.running:
        sim.step()                       # one frame
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu

DEFAULT_ROM = "/mnt/project-files/FFV/Final Fantasy V (Japan) (patched)(1).sfc"
EXEC_BATTLE = 0xC20000        # ExecBattle_ext (JSL target)
EXEC_SOUND = 0xC40004         # ExecSound_ext: HLE'd to an immediate RTL
SENTINEL = 0x7FFFF0           # return address the battle "returns" to

# SNES joypad bits (as read from $4218)
B, Y, SELECT, START, UP, DOWN, LEFT, RIGHT, A, X, L, R = (0x8000, 0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100,
                                                          0x0080, 0x0040, 0x0020, 0x0010)

# WRAM addresses (battle_ram.inc of the everything8215/ff5 disassembly)
ENCOUNTER = 0x04F0      # u16 index into the battle-property table (D0:3000, 16 bytes each)
TERRAIN = 0x04F2        # battle background
FIELD_CHAR = 0x0500     # 4 x 0x50 bytes
FIELD_ITEMS, FIELD_QTY = 0x0640, 0x0740
GIL = 0x0947
CONFIG1 = 0x0970
FIELD_DATA = 0x09B4     # escapes, event flags (battle return code at $09C4 bit0 = game over)
RNG_SEED = 0x0AF9
ITEMS_WON = 0x013B
CHAR = 0x2000           # battle CharStruct, 0x80 bytes each: 4 party then 8 monsters
MENU_C1 = 0x41B0        # MenuDataC1: open, action flag, command, char, monster targets, party targets, selected item
DISPLAY_CUR = 0x41CC

class Combatant:
    """View over one battle CharStruct (index 0-3 party, 4-11 monsters)."""
    def __init__(self, w, i): self.w, self.i, self.b = w, i, CHAR + 0x80 * i
    def u8(self, o): return self.w[self.b + o]
    def u16(self, o): return self.w[self.b + o] | (self.w[self.b + o + 1] << 8)
    row = property(lambda s: s.u8(0)); job = property(lambda s: s.u8(1)); level = property(lambda s: s.u8(2))
    hp = property(lambda s: s.u16(6)); max_hp = property(lambda s: s.u16(8))
    mp = property(lambda s: s.u16(0xA)); max_mp = property(lambda s: s.u16(0xC))
    status = property(lambda s: bytes(s.w[s.b + 0x1A:s.b + 0x1E]))
    @property
    def present(self): return self.i < 4 and not (self.row & 0x40) or self.i >= 4 and self.max_hp > 0
    @property
    def dead(self): return bool(self.u8(0x1A) & 0xC0)
    def __repr__(self): return f"<{self.i} lv{self.level} hp {self.hp}/{self.max_hp} mp {self.mp} st {self.status.hex()}>"

class BattleSim:
    def __init__(self, rom=None):
        data = rom if isinstance(rom, (bytes, bytearray)) else open(rom or DEFAULT_ROM, "rb").read()
        cpu.set_rom(data)
        self.rom = data
        self.m = cpu.Machine()
        self.m.hook(EXEC_SOUND, 1)
        self.running = False
        self.pad_queue = []        # list of (buttons, frames) consumed one frame at a time
        self.frame = 0

    # ---- memory helpers
    @property
    def w(self): return self.m.wram
    def rb(self, a): return self.m.wram[a]
    def rw(self, a): return self.m.wram[a] | (self.m.wram[a + 1] << 8)
    def wb(self, a, v): self.m.wram[a] = v & 0xFF
    def ww(self, a, v): self.m.wram[a] = v & 0xFF; self.m.wram[a + 1] = (v >> 8) & 0xFF
    def read(self, a, n): return bytes(self.m.wram[a:a + n])
    def write(self, a, data):
        for i, b in enumerate(data): self.m.wram[a + i] = b
    def load_wram(self, data):
        assert len(data) >= 0x20000
        ctypes_copy = (cpu.ctypes.c_uint8 * 0x20000).from_buffer_copy(bytes(data[:0x20000]))
        cpu.ctypes.memmove(cpu.ctypes.addressof(self.m.wram), cpu.ctypes.addressof(ctypes_copy), 0x20000)
    def dump_wram(self): return bytes(self.m.wram)

    # ---- battle lifecycle
    def start(self, encounter=None, background=None):
        """Call ExecBattle on the current WRAM.  The battle program initialises everything else from the field state."""
        m = self.m
        if encounter is not None: self.ww(ENCOUNTER, encounter)
        if background is not None: self.wb(TERRAIN, background)
        m.setreg(cpu.R_S, 0x1FFF); m.setreg(cpu.R_P, 0x24); m.setreg(cpu.R_D, 0); m.setreg(cpu.R_DB, 0x7E)
        m.setreg(cpu.R_E, 0)
        m.joy(0, 0); self.pad_queue = []
        m.call_long(EXEC_BATTLE, SENTINEL)
        self.running = True; self.frame = 0

    def _run(self, frames):
        rc = self.m.run(2 ** 62, frames)
        if rc == cpu.RUN_RETURNED: self.running = False
        elif rc in (cpu.RUN_STP, cpu.RUN_BADOP):
            self.running = False
            raise RuntimeError(f"CPU halted rc={rc} at {self.m.pc24:06X}: ring={[hex(x) for x in self.m.ring()[-8:]]}")
        elif rc >= cpu.RUN_HOOK: raise RuntimeError(f"unhandled hook {rc - cpu.RUN_HOOK}")
        return rc

    def step(self, frames=1):
        """Advance by `frames` video frames (queued pad input is applied one frame at a time)."""
        for _ in range(frames):
            if not self.running: return
            if self.pad_queue:
                b, n = self.pad_queue[0]
                self.m.joy(0, b)
                if n <= 1: self.pad_queue.pop(0)
                else: self.pad_queue[0] = (b, n - 1)
            else:
                self.m.joy(0, 0)
            self._run(1); self.frame += 1

    def press(self, buttons, hold=3, release=3):
        """Queue a button tap: held for `hold` frames then released for `release` frames."""
        self.pad_queue.append((buttons, hold)); self.pad_queue.append((0, release))

    @property
    def idle_pad(self): return not self.pad_queue

    # ---- readout
    def combatant(self, i): return Combatant(self.m.wram, i)
    @property
    def party(self): return [self.combatant(i) for i in range(4)]
    @property
    def monsters(self): return [self.combatant(i) for i in range(4, 12) if self.combatant(i).max_hp > 0 and self.combatant(i).u8(0x2) is not None]
    @property
    def menu_char(self):
        """Index of the party member whose command menu is open (None when no menu)."""
        return self.rb(DISPLAY_CUR) if self.rb(MENU_C1) == 1 and self.rb(DISPLAY_CUR) < 4 else None
    @property
    def return_code(self): return self.rb(FIELD_DATA + 0x10)       # $09C4 bit0: party wiped; bit1: escaped
