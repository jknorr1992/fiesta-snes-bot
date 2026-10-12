"""Job abilities: what each character has learned (ABP -> job level -> ability) and what to put in the two free ability slots.

Data (everything8215/ff5 disassembly): job ability lists at 0x110000 + ptr(0x1152C0), 3 bytes each: ABP cost, ability (bit 7 = passive,
index into the 33 passives at 0x116200; otherwise a battle command id).  The learned abilities live in the field WRAM: 20 bytes per character
at 0x08F7 (bit position from the table at 0x10EC00, 2 bytes per ability: byte offset, bit number; passives follow the 0x4E commands).
The real battle code awards ABP and learns abilities on victory, so the simulated party state already carries them.

Each character has 4 command slots (CharStruct 0x16..0x19): Fight and Item are fixed, the other two are free: by default the job's class
command and a blank.  A passive is stored as 0x80 | passive index in a slot (user, 2026-10-12: "fill in the blank slots with the class
ability by default when you change jobs, you can switch it out without consequence")."""
from data import text

JOB_ABILITY_PTR, JOB_ABILITY_COUNT = 0x1152C0, 0x1152EA
ABILITY_BITS = 0x10EC00
PASSIVE_NAMES = 0x116200
FIELD_ABILITY_LIST, FIELD_ABILITY_COUNT = 0x08F7, 0x08F3
FREE_SLOTS = (1, 2)
FIGHT, ITEM = 5, 2

# commands the battle policy actually uses: magic commands must stay in a slot, everything else is optional
MAGIC_COMMANDS = {50, 56, 62, 68, 73, 77, 44}            # White, Black, Time, Summon, Red, Blue, MgcSwrd (first id of each level group; matched by name below)
NEEDED_COMMAND_NAMES = ("White", "Black", "Time", "Summon", "Red", "Blue", "MgcSwrd")

# static value of a passive for the simulated fights (name -> score); measured against Liquid Flame at lv15, see README
PASSIVE_SCORE = {"Barrier": 12, "Cover": 10, "Counter": 9, "Evade": 8, "Pharmacy": 7, "HP +30%": 30, "HP +20%": 20, "HP +10%": 10,
                 "MP +30%": 15, "MP +10%": 6, "Barehand": 6, "Dbl.Grip": 5, "A.Dodger": 4, "DualWield": 8}


# commands worth a free slot when the policy supports them (score vs the passives above); the rest default to 20
NOT_EQUIPPED = ("", "Item", "Fight", "Escape", "Row", "Catch", "Guard", "Kick")
COMMAND_SCORE = {"Steal": 22, "Mug": 24, "Jump": 30, "Lance": 24, "Throw": 28, "Aim": 22, "Animals": 22, "Slash": 20, "SwdSlap": 20,
                 "Focus": 22, "Chakra": 26, "Drink": 24, "Recover": 22, "Revive": 22, "Terrain": 24, "Sing": 24, "Dance": 24, "Flirt": 18,
                 "Mimic": 26, "DuCast": 40, "Mix": 40, "Gil Toss": 24}


class Abilities:
    def __init__(self, data):
        self.d, self.r = data, data.r

    def passive_name(self, i): return text(self.r[PASSIVE_NAMES + 8 * i:PASSIVE_NAMES + 8 * i + 8])

    def name(self, idx):
        """Ability index 0..110: < 0x4E command, else passive (idx - 0x4E)."""
        return self.d.command_name(idx) if idx < 0x4E else self.passive_name(idx - 0x4E)

    def job_list(self, job):
        n = self.r[JOB_ABILITY_COUNT + job]
        ptr = self.r[JOB_ABILITY_PTR + 2 * job] | self.r[JOB_ABILITY_PTR + 2 * job + 1] << 8
        out = []
        for k in range(n):
            o = 0x110000 + ptr + 3 * k
            ab = self.r[o + 2]
            out.append((self.r[o] | self.r[o + 1] << 8, ab))
        return out

    def known(self, wram, char):
        """Ability indices (0..110) the character has learned."""
        base = FIELD_ABILITY_LIST + 20 * char; out = set()
        for idx in range(111):
            y, x = self.r[ABILITY_BITS + 2 * idx], self.r[ABILITY_BITS + 2 * idx + 1]
            if y < 20 and wram[base + y] & (0x80 >> x): out.add(idx)
        return out

    def passives_known(self, wram, char):
        return [(i - 0x4E, self.passive_name(i - 0x4E)) for i in sorted(self.known(wram, char)) if i >= 0x4E]

    def loadout(self, wram, char, commands, has_shield=False, two_handed=False, supported=()):
        """Best commands[4] for the character: Fight/Item fixed, the two free slots hold (best first) the job's magic command,
        then whichever of the job's own class command / learned commands (`supported`: names the policy can really use) and the
        learned passives score highest, then leftovers."""
        cm = list(commands)
        scored = []                                     # (score, slot value)
        keep = [c for c in cm[1:3] if c and not (c & 0x80) and self.d.command_name(c) in NEEDED_COMMAND_NAMES]
        for c in keep: scored.append((100, c))
        for pid, nm in self.passives_known(wram, char):
            sc = PASSIVE_SCORE.get(nm, 0)
            if nm == "Dbl.Grip" and (has_shield or not two_handed): sc = 0
            if sc > 0: scored.append((sc, 0x80 | pid))
        taken = set(keep)
        for c in sorted(self.known(wram, char) | {x for x in cm[1:3] if x and not x & 0x80}):
            if c >= 0x4E or c in taken: continue
            nm = self.d.command_name(c)
            if nm in supported and nm not in NEEDED_COMMAND_NAMES and nm not in NOT_EQUIPPED:
                own = c in cm[1:3]                                        # the job's class command: a little preferred
                scored.append((COMMAND_SCORE.get(nm, 20) + (3 if own else 0), c)); taken.add(c)
        scored.sort(reverse=True)
        rest = [c for c in cm[1:3] if c and not (c & 0x80) and c not in taken]       # class commands the policy does not use (Row, ...)
        for s in FREE_SLOTS: cm[s] = 0
        fill = [v for _, v in scored] + rest
        for s, v in zip(FREE_SLOTS, fill): cm[s] = v
        return cm
