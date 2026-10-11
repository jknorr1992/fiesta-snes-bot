"""FFV battle simulator: runs the game's own battle program (C2:0000, plus the battle-graphics code in C1/D9 that drives the
menu and animation timing) straight from the ROM on a 65C816 interpreter (cpu65816.c).  Nothing is re-implemented, so damage
formulas, AI scripts, ATB timing, the RNG and the FF5r hack's own battle changes are those of the ROM that is loaded.

PPU/APU are not emulated (their registers are sinks, DMA to them is skipped); sound calls (C4:0004) return immediately; the
battle NMI's spin-wait (D9:7CAC) is skipped ahead to the next NMI (verified to give identical frame counts and outcomes).

    sim = BattleSim()
    sim.load_wram(wram_bytes)            # field state: party at $0500, inventory at $0640/$0740, flags, gil, ...
    sim.start(encounter=0x5C, background=0)
    while sim.running:
        if sim.menu_char is not None and sim.idle_pad: sim.press(A); sim.press(A)     # "Fight" on the first target
        sim.step()                       # one frame
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cpu
from data import BATTLE_OVER

DEFAULT_ROM = os.environ.get("FFV_ROM", "/mnt/project-files/FFV/Final Fantasy 5 (Japan) (patched)(2).sfc")
CYC_SCALE = 88       # +8.6 % cycles (internal cycles, DRAM refresh), calibrated vs snes9x on ROM (patched)(2); see validate/diff_battle.py
EXEC_BATTLE = 0xC20000        # ExecBattle_ext (JSL target)
EXEC_SOUND = 0xC40004         # ExecSound_ext: HLE'd to an immediate RTL
VBLANK_WAIT = 0xD97CAC        # spin-wait for the battle NMI (idle-skipped)
OPEN_MENU = 0xC139F9          # battle graphics function $00 "open menu" (direct-command hook)
SENTINEL = 0x7FFFF0           # return address the battle "returns" to

# SNES joypad bits (as read from $4218)
B, Y, SELECT, START, UP, DOWN, LEFT, RIGHT, A, X, L, R = (0x8000, 0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100,
                                                          0x0080, 0x0040, 0x0020, 0x0010)

# WRAM addresses (battle_ram.inc of the everything8215/ff5 disassembly)
ENCOUNTER = 0x04F0      # u16 index into the battle-property table (D0:3000, 16 bytes each)
TERRAIN = 0x04F2        # battle background
FIELD_CHAR = 0x0500     # 4 x 0x50 bytes
GIL = 0x0947
FIELD_DATA = 0x09B4
ITEMS_WON = 0x013B      # 8 bytes
CHAR = 0x2000           # battle CharStruct, 0x80 bytes each: 4 party then 8 monsters
ACTIVE = 0x3EC2         # ActiveParticipants (12 bytes)
BATTLE_OVER_ADDR = 0x7BDE
INVENTORY_ITEMS = 0x2734
MENU_C1 = 0x41B0        # MenuDataC1: open, action flag, command, char, monster targets, party targets, selected item
DISPLAY_CUR = 0x41CC

# battle command ids that matter for direct commands (JobCmdTbl: Knight 06 05 00 02)
CMD_ITEM, CMD_FIGHT, CMD_GUARD = 0x02, 0x05, 0x06

class Combatant:
    """View over one battle CharStruct (index 0-3 party, 4-11 monsters)."""
    def __init__(self, w, i): self.w, self.i, self.b = w, i, CHAR + 0x80 * i
    def u8(self, o): return self.w[self.b + o]
    def u16(self, o): return self.w[self.b + o] | (self.w[self.b + o + 1] << 8)
    row = property(lambda s: s.u8(0)); job = property(lambda s: s.u8(1)); level = property(lambda s: s.u8(2))
    hp = property(lambda s: s.u16(6)); max_hp = property(lambda s: s.u16(8))
    mp = property(lambda s: s.u16(0xA)); max_mp = property(lambda s: s.u16(0xC))
    status = property(lambda s: bytes(s.w[s.b + 0x1A:s.b + 0x1E]))
    active = property(lambda s: bool(s.w[ACTIVE + s.i]))
    @property
    def dead(self): return bool(self.u8(0x1A) & 0xC0)
    def __repr__(self): return f"<{self.i} lv{self.level} hp {self.hp}/{self.max_hp} mp {self.mp} st {self.status.hex()}>"

class Command:
    """What a character's menu leaves in MenuData when the player confirms (see ProcessMenuCommandData)."""
    def __init__(self, command, action=0x80, monsters=0, party=0, selected=0, second=None):
        self.command, self.action, self.monsters, self.party, self.selected, self.second = command, action, monsters, party, selected, second
    @staticmethod
    def monster_mask(*slots):      # monster slot 0..7
        m = 0
        for s in slots: m |= 0x80 >> s
        return m
    @staticmethod
    def party_mask(*chars):        # party member 0..3 (high nibble)
        m = 0
        for c in chars: m |= 0x80 >> c
        return m
    @classmethod
    def fight(cls, *monster_slots): return cls(CMD_FIGHT, 0x80, cls.monster_mask(*monster_slots))
    @classmethod
    def guard(cls): return cls(CMD_GUARD, 0x80)
    @classmethod
    def item(cls, inventory_slot, monsters=(), party=()):
        return cls(CMD_ITEM, 0x40, cls.monster_mask(*monsters), cls.party_mask(*party), inventory_slot)
    def __repr__(self): return f"Command(cmd={self.command:#x}, flag={self.action:#x}, mon={self.monsters:#x}, party={self.party:#x}, sel={self.selected})"

class BattleSim:
    def __init__(self, rom=None):
        data = rom if isinstance(rom, (bytes, bytearray)) else open(rom or DEFAULT_ROM, "rb").read()
        cpu.set_rom(data)
        self.rom = bytes(data)
        self.m = cpu.Machine()
        self.m.setreg(cpu.R_CYCSCALE, CYC_SCALE)   # extra cycles the interpreter does not model; calibrated against snes9x (validate/diff_battle.py)
        self.m.hook(EXEC_SOUND, 1)
        self.m.hook(VBLANK_WAIT, 3, 0xA5, 0xDB9A)   # `lda $a5 / ora $db9a / bne`: skipped ahead to the next NMI
        self.running = False
        self.pad_queue = []        # (buttons, frames) consumed one frame at a time
        self.frame = 0
        self._open_hook = -1
        self.direct = None         # callable(sim, char) -> Command | None: bypass the real menu (see set_direct)
        self._close_menu = False
        self.log = []

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
        buf = (cpu.ctypes.c_uint8 * 0x20000).from_buffer_copy(bytes(data[:0x20000]))
        cpu.ctypes.memmove(cpu.ctypes.addressof(self.m.wram), cpu.ctypes.addressof(buf), 0x20000)
    def dump_wram(self): return bytes(self.m.wram)

    # ---- machine state snapshots (CPU + WRAM + timing); cheap (130 KB)
    def snapshot(self): return (self.m.clone(), self.frame, self.running, list(self.pad_queue), self._close_menu)
    def restore(self, snap):
        m, self.frame, self.running, pq, self._close_menu = snap
        self.m.copy_from(m); self.pad_queue = list(pq)

    # ---- battle lifecycle
    def start(self, encounter=None, background=None):
        """Call ExecBattle on the current WRAM.  The battle program initialises everything else from the field state."""
        m = self.m
        if encounter is not None: self.ww(ENCOUNTER, encounter)
        if background is not None: self.wb(TERRAIN, background)
        m.setreg(cpu.R_S, 0x1FFF); m.setreg(cpu.R_P, 0x24); m.setreg(cpu.R_D, 0); m.setreg(cpu.R_DB, 0x7E)
        m.setreg(cpu.R_E, 0)
        m.joy(0, 0); self.pad_queue = []; self._close_menu = False
        m.call_long(EXEC_BATTLE, SENTINEL)
        self.running = True; self.frame = 0

    def set_direct(self, fn):
        """fn(sim, char) -> Command|None.  When set, the battle's "open menu" routine is replaced: the command is written the way the
        menu would leave it and the menu is closed one frame later (the real menu's cursor/animation handling is skipped, so
        action timing can differ by a few frames from the pad path).  None from fn = fall back to the real menu."""
        self.direct = fn
        if fn: self._open_hook = self.m.hook(OPEN_MENU, 0)
        else:
            self.m.clear_hooks(); self.m.hook(EXEC_SOUND, 1); self.m.hook(VBLANK_WAIT, 3, 0xA5, 0xDB9A)

    def _rts(self):
        s = self.m.reg(cpu.R_S); w = self.w
        ret = (w[(s + 1) & 0xFFFF] | (w[(s + 2) & 0xFFFF] << 8)) + 1
        self.m.setreg(cpu.R_S, (s + 2) & 0xFFFF); self.m.setreg(cpu.R_PC, ret & 0xFFFF)

    def _handle_open_menu(self):
        char = self.rb(0x70)
        cmd = self.direct(self, char) if (self.direct and self.rb(0x71) == 0 and char < 4) else None
        if cmd is None:
            self.m.skip_hook(); return                      # run the real routine
        self.log.append((self.frame, "direct", char, repr(cmd)))
        w = self.w
        w[0x41B1], w[0x41B2], w[0x41B3] = cmd.action, cmd.command, char
        w[0x41B4], w[0x41B5], w[0x41B6] = cmd.monsters, cmd.party, cmd.selected
        if cmd.second:
            c2 = cmd.second
            w[0x41B8], w[0x41B9], w[0x41BA], w[0x41BB], w[0x41BC], w[0x41BD] = c2.action, c2.command, char, c2.monsters, c2.party, c2.selected
        w[0x41B0] = 1; w[0x41B7] = 1; w[0xCD41] = 1; w[0xCD42] = char; w[0x010D] = char
        self._close_menu = True
        self._rts()

    def _run(self, frames):
        while True:
            rc = self.m.run(2 ** 62, frames)
            if rc == cpu.RUN_RETURNED: self.running = False
            elif rc in (cpu.RUN_STP, cpu.RUN_BADOP):
                self.running = False
                raise RuntimeError(f"CPU halted rc={rc} at {self.m.pc24:06X}: ring={[hex(x) for x in self.m.ring()[-8:]]}")
            elif self.direct and rc == cpu.RUN_HOOK + self._open_hook and self.m.pc24 == OPEN_MENU:
                self._handle_open_menu(); continue
            elif rc >= cpu.RUN_HOOK: raise RuntimeError(f"unhandled hook {rc - cpu.RUN_HOOK}")
            return rc

    def step(self, frames=1):
        """Advance by `frames` video frames (queued pad input is applied one frame at a time)."""
        for _ in range(frames):
            if not self.running: return
            if self._close_menu:
                w = self.w; w[0x41B0] = 0; w[0x41B7] = 0; w[0xCD41] = 0; w[0xCD42] = 0xFF; self._close_menu = False
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
    def monsters(self): return [self.combatant(i) for i in range(4, 12) if self.rb(ACTIVE + i)]
    @property
    def menu_char(self):
        """Index of the party member whose command menu is open (None when no menu)."""
        c = self.rb(DISPLAY_CUR)
        return c if self.rb(MENU_C1) == 1 and c < 4 and not self._close_menu else None
    @property
    def battle_over(self): return self.rb(BATTLE_OVER_ADDR)      # 0x80 victory, 0x40 party KO, 0x01 escaped, 0x20 timer
    @property
    def outcome(self): return BATTLE_OVER.get(self.battle_over, "running" if self.running else "unknown")
    @property
    def inventory_slots(self):
        """{slot: item id} of the battle inventory (for Command.item)."""
        return {i: self.rb(INVENTORY_ITEMS + i) for i in range(256) if self.rb(INVENTORY_ITEMS + i)}
    def crashed(self):
        """True when the CPU is executing from low RAM (e.g. a BRK through the $0000 vector)."""
        pc = self.m.pc24
        return pc < 0x1F00 or 0x1F10 <= pc < 0x2000
