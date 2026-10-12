"""Job switching the way the game's own menu does it (menu code in ROM bank C2, run on the 65816 interpreter).

Per character the field struct (0x0500 + 0x50*i) holds the CURRENT job, its level (+0x3A) and its ABP (+0x3B, 12 bits).  Every other
job's level/ABP sits in the table at 0x0843 + 0x2C*i + 2*job as `level << 12 | abp`.  Switching a job (menu-main.asm, job menu):

    1. _c2d503/_c2e464  save the current job's level/ABP into the table
    2. _c2e47d          load the new job's level/ABP from the table into the field struct, set +0x01
    3. _c2eb82          default commands (JobCmdTbl), innate passives, equipment types
    4. _c2ebbd          stat modifiers (+0x24..27) = command passives + job + character modifiers
    5. _c2e76c          equipment-derived stats, passive flags (+0x20), spell levels
    6. _c2e9ce          max HP/MP from level and the new modifiers, HP/MP +x% passives   (this one needs the SNES multiplier registers
                        that the interpreter does not model, so it is done here with the same formula)

ABP is earned by the CURRENT job only (the battle code adds it to +0x3B), so what a character learns depends on the order of its jobs;
the ability bits (0x08F7, 20 bytes per character) are per character and stay learned when the job changes.  Equipment the new job may
not wear comes off (the user: the game does not let you keep suddenly invalid gear); it returns to the bag."""
from data import GameData
import cpu

TABLE = 0x0843
PER_CHAR = 0x2C
CHAR, SIZE = 0x0500, 0x50
STACK_TOP = 0x1FFF
SENTINEL = 0xC2FFF0
# ROM routines (menu code, bank C2), see the module doc
A247, EB82, EBBD, E76C = 0xC2A247, 0xC2EB82, 0xC2EBBD, 0xC2E76C
SLOTS = ("head", "body", "accessory", "rh_shield", "lh_shield", "rh_weapon", "lh_weapon")


class JobSwitcher:
    def __init__(self, data, rom=None):
        self.d = data
        rom = rom if rom is not None else data.r
        cpu.set_rom(bytes(rom))
        self.m = cpu.Machine()

    # ---- run one `rts`-terminated menu routine on a WRAM image
    def _call(self, wram, addr, char):
        """Run `addr` after the menu's own data-pointer init (_c2a247: item property pointers at 7E:D000..), return the WRAM with only the
        character structs (0x0500..0x05A0) taken from the run."""
        m = self.m
        m.wram[:] = wram
        w = m.wram
        for a in (A247, addr):
            w[0x7E] = char; w[0x7F] = 0                   # $7e: character index (the routines derive $80 = struct offset from it)
            w[0x80] = (0x50 * char) & 0xFF; w[0x81] = 0; w[0x8E] = 0; w[0x8F] = 0
            ret = (SENTINEL - 1) & 0xFFFF
            sp = STACK_TOP
            w[sp] = ret >> 8; w[sp - 1] = ret & 0xFF          # RTS target = SENTINEL
            m.setreg(cpu.R_S, sp - 2); m.setreg(cpu.R_P, 0x04); m.setreg(cpu.R_D, 0); m.setreg(cpu.R_DB, 0x7E)
            m.setreg(cpu.R_E, 0); m.setreg(cpu.R_PB, a >> 16); m.setreg(cpu.R_PC, a & 0xFFFF)
            m.set_stop_pc(SENTINEL)
            rc = m.run(5_000_000, 0)
            if rc != cpu.RUN_RETURNED and m.pc24 != SENTINEL: raise RuntimeError(f"menu routine {a:06X} did not return (rc={rc}, pc={m.pc24:06X})")
        out = bytearray(wram); out[CHAR:CHAR + 4 * SIZE] = bytes(w[CHAR:CHAR + 4 * SIZE])
        return bytes(out)

    # ---- stored per-job state
    @staticmethod
    def stored(wram, char, job):
        o = TABLE + PER_CHAR * char + 2 * job
        v = wram[o] | wram[o + 1] << 8
        return v >> 12, v & 0xFFF

    def job_state(self, wram, char):
        """{job: (level, abp)} of every job with progress, the current job taken from the field struct."""
        b = CHAR + SIZE * char; cur = wram[b + 1] & 0x1F
        out = {j: self.stored(wram, char, j) for j in range(22)}
        out[cur] = (wram[b + 0x3A] & 0xF, (wram[b + 0x3B] | wram[b + 0x3C] << 8) & 0xFFF)
        return {j: v for j, v in out.items() if v != (0, 0)}

    # ---- the switch
    def switch(self, wram, char, job, bag=None):
        """New WRAM image with character `char` changed to `job` (id or name).  `bag` ({item id: qty}, modified in place if given)
        receives equipment the new job may not wear.  Returns (wram, removed item ids)."""
        d = self.d
        job = d.job_id(job) if isinstance(job, str) else job
        w = bytearray(wram); b = CHAR + SIZE * char
        old = w[b + 1] & 0x1F
        if old == job: return bytes(w), []
        # 1. save the current job's progress, 2. load the new job's
        lvl, abp = w[b + 0x3A] & 0xF, (w[b + 0x3B] | w[b + 0x3C] << 8) & 0xFFF
        o = TABLE + PER_CHAR * char + 2 * old; v = lvl << 12 | abp; w[o] = v & 0xFF; w[o + 1] = v >> 8
        o = TABLE + PER_CHAR * char + 2 * job; v = w[o] | w[o + 1] << 8
        w[b + 1] = job; w[b + 0x3A] = v >> 12; w[b + 0x3B] = v & 0xFF; w[b + 0x3C] = (v >> 8) & 0x0F
        # 3. commands / innate passives / equipment types
        w = bytearray(self._call(bytes(w), EB82, char))
        # equipment the new job may not wear comes off (before the stats are derived from it)
        wm, am = w[b + 0x40] | w[b + 0x41] << 8, w[b + 0x42] | w[b + 0x43] << 8
        removed = []
        for k, s in enumerate(SLOTS):
            it = w[b + 0x0E + k]
            if it and it != 0x80 and not d.item_equippable(it, wm, am):
                w[b + 0x0E + k] = 0x80 if k < 3 else 0
                removed.append(it)
        if bag is not None:
            for it in removed: bag[it] = bag.get(it, 0) + 1
        # 4./5. stat modifiers and derived stats / passive flags
        w = bytearray(self._call(bytes(w), EBBD, char))
        w = bytearray(self._call(bytes(w), E76C, char))
        # spell levels the commands allow
        cm = list(w[b + 0x16:b + 0x1A]); w[b + 0x3D:b + 0x40] = d.enable_spells(cm)
        self.fix_hp_mp(w, char)
        return bytes(w), removed

    def set_commands(self, wram, char, commands):
        """Put `commands` (4 command ids; passives as 0x80|index) into the character and rederive modifiers / flags / HP / MP."""
        w = bytearray(wram); b = CHAR + SIZE * char
        w[b + 0x16:b + 0x1A] = bytes(commands)
        w = bytearray(self._call(bytes(w), EBBD, char)); w = bytearray(self._call(bytes(w), E76C, char))
        w[b + 0x3D:b + 0x40] = self.d.enable_spells(list(w[b + 0x16:b + 0x1A]))
        self.fix_hp_mp(w, char)
        return bytes(w)

    def fix_hp_mp(self, w, char):
        """_c2e9ce: max HP/MP from the level table and the vitality / magic modifiers, then the HP/MP +x% command passives."""
        d = self.d; b = CHAR + SIZE * char
        lv = w[b + 2]
        if lv >= 2: hp, mp = d.level_hp_mp(lv, w[b + 0x26], w[b + 0x27])
        else: hp, mp = w[b + 8] | w[b + 9] << 8, w[b + 0xC] | w[b + 0xD] << 8
        for c in w[b + 0x16:b + 0x1A]:
            if c in (0x8B, 0x8C, 0x8D): hp = min(9999, hp + hp * (c - 0x8A) * 10 // 100)
            elif c in (0x8E, 0x8F): mp = min(999, mp + mp * (3 if c == 0x8F else 1) * 10 // 100)
        cur_hp = w[b + 6] | w[b + 7] << 8; cur_mp = w[b + 0xA] | w[b + 0xB] << 8
        w[b + 8], w[b + 9] = hp & 0xFF, hp >> 8; w[b + 0xC], w[b + 0xD] = mp & 0xFF, mp >> 8
        cur_hp, cur_mp = min(cur_hp, hp), min(cur_mp, mp)
        w[b + 6], w[b + 7] = cur_hp & 0xFF, cur_hp >> 8; w[b + 0xA], w[b + 0xB] = cur_mp & 0xFF, cur_mp >> 8
