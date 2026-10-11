"""Run whole battles: encounter + field state + policy -> BattleResult.

    from scenario import run_battle, FightPolicy
    res = run_battle(field, 0x5C, background=0x12, policy=FightPolicy())
    print(res.outcome, res.frames, res.gil_gained, res.items_won, res.party_after)
"""
from dataclasses import dataclass, field as dc_field
from sim import BattleSim, Command, A
from field import Field, Character, FIELD_CHAR, CHAR_SIZE, GIL
import data as D

class FightPolicy:
    """Every ready character attacks the first living monster (direct command: bypasses the real menu)."""
    mode = "direct"
    def __call__(self, sim, char):
        alive = [m.i - 4 for m in sim.monsters if not m.dead and m.hp > 0]
        return Command.fight(alive[0] if alive else 0)

class PadFightPolicy:
    """Same, through the real menu with joypad taps: Fight, then confirm the (default) first target."""
    mode = "pad"
    def on_menu(self, sim, char):
        sim.press(A); sim.press(A)

@dataclass
class BattleResult:
    outcome: str                 # victory | defeat | escaped | timer | timeout | crash
    frames: int
    gil_gained: int
    items_won: list
    party_before: list
    party_after: list            # Character structs (post-battle field state: HP, exp, level...)
    monsters_left: int
    exp_gained: list
    wram: bytes = b""
    log: list = dc_field(default_factory=list)
    crash: str = ""
    def summary(self):
        return (f"{self.outcome} in {self.frames} frames ({self.frames / 60:.0f}s): gil +{self.gil_gained}, exp +{self.exp_gained}, "
                f"items {self.items_won}, hp {[c.hp for c in self.party_after]}")

def run_battle(field, encounter, background=0, policy=None, max_frames=60000, sim=None, rom=None, keep_wram=False, victory_skip=True):
    """field: Field or raw 128 KB WRAM.  policy: callable(sim, char)->Command (direct), or an object with on_menu(sim, char)
    (pad), default FightPolicy.  Returns a BattleResult."""
    sim = sim or BattleSim(rom)
    wram = field.to_wram() if isinstance(field, Field) else bytes(field)
    sim.load_wram(wram); sim.log = []
    policy = policy or FightPolicy()
    sim.set_direct(None)
    pad = hasattr(policy, "on_menu")
    if not pad: sim.set_direct(policy)
    before = [Character(wram[FIELD_CHAR + CHAR_SIZE * i:FIELD_CHAR + CHAR_SIZE * (i + 1)]) for i in range(4)]
    gil0 = int.from_bytes(wram[GIL:GIL + 3], "little")
    sim.start(encounter, background)
    crash = ""
    try:
        while sim.running and sim.frame < max_frames:
            if pad and sim.menu_char is not None and sim.idle_pad:
                policy.on_menu(sim, sim.menu_char)
            elif sim.battle_over and sim.idle_pad:
                sim.press(A, 2, 12)            # dismiss the result windows
            sim.step()
            if sim.crashed():
                crash = f"executing from RAM at {sim.m.pc24:06X}"; break
    except RuntimeError as e:
        crash = str(e)
    w = sim.dump_wram()
    after = [Character(w[FIELD_CHAR + CHAR_SIZE * i:FIELD_CHAR + CHAR_SIZE * (i + 1)]) for i in range(4)]
    outcome = "crash" if crash else (sim.outcome if not sim.running else "timeout")
    if outcome == "unknown": outcome = "victory" if sim.battle_over == 0x80 else outcome
    return BattleResult(outcome, sim.frame, int.from_bytes(w[GIL:GIL + 3], "little") - gil0,
                        [i for i in w[0x13B:0x143] if i], before, after, len(sim.monsters),
                        [a.exp - b.exp for a, b in zip(after, before)], w if keep_wram else b"", list(sim.log), crash)
