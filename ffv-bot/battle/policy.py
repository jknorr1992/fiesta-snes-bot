"""Battle policy for the campaign simulation: what a sensible player does with the commands each job has.

    SmartPolicy(data)   callable(sim, char) -> Command        (direct commands, see sim.set_direct)

  * a healer with Cure-type magic heals the most hurt living ally below `heal_below` of max HP (the weakest-first spell that fills the gap)
  * a caster with attack magic casts its strongest affordable offensive spell on the healthiest-looking... first living monster
  * anybody else, or a caster out of MP, attacks the first living monster
  * with no healer, Potions go to an ally below `potion_below`
It records whether any party member was ever seen dead (`died`), so the campaign can demand a flawless win."""
from sim import Command, CHAR, ACTIVE

MAGIC_COMMAND = 0x2C
CHAR_SPELLS = 0x2D34                 # battle RAM: CharSpells per character (650 bytes): ID[130], Level[130], MP[130], Targetting[130], Flags[130]
SPELL_SLOT = 650
HEAL_NAMES = ("Cure", "Cure2", "Cure3", "Cure4")
ATTACK_NAMES = ("Fire", "Ice", "Bolt", "Fire2", "Ice2", "Bolt2", "Fire3", "Ice3", "Bolt3")   # in rough order of strength (ids resolved per ROM)
POTIONS = ("Potion", "Hi-Potion", "X-Potion", "Elixir")

class SmartPolicy:
    mode = "direct"
    def __init__(self, data, heal_below=0.5, potion_below=0.35):
        self.d = data; self.heal_below = heal_below; self.potion_below = potion_below
        white = {data.spell_name(i): i for i in range(96) if data.spell_name(i) and data.spell_icon(i) == "white"}
        black = {data.spell_name(i): i for i in range(96) if data.spell_name(i) and data.spell_icon(i) == "black"}
        self.heal_ids = [white[n] for n in HEAL_NAMES if n in white]
        self.attack_ids = [black[n] for n in ATTACK_NAMES if n in black]
        self.life_ids = [white[n] for n in ("Life2", "Life") if n in white]
        self.pd_id = data.item_id("PhnxDown")
        self.potion_ids = [data.item_id(n) for n in ("Hi-Potion", "Potion")]
        self.died = False
        self.log = []

    def _known(self, sim, char, spell):
        b = CHAR_SPELLS + SPELL_SLOT * char
        w = sim.w
        if w[b + spell] != spell: return None
        flags = w[b + 520 + spell]
        if flags & 0x80: return None                       # disabled (not enough MP / muted)
        return w[b + 260 + spell]                          # MP cost

    def __call__(self, sim, char):
        party = sim.party
        if any(c.dead for c in party): self.died = True
        me = party[char]
        living_mon = [m for m in sim.monsters if not m.dead and m.hp > 0]
        target = (living_mon[0].i - 4) if living_mon else 0
        # revive a fallen ally: Life (a healer's spell) first, a Phoenix Down otherwise
        fallen = [c.i for c in party if c.dead and c.i != char]
        if fallen:
            ally = fallen[0]
            for sp in self.life_ids:
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp and not me.dead:
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
            slot = next((sl for sl, it in sim.inventory_slots.items() if it == self.pd_id), None)
            if slot is not None: return Command.item(slot, party=(ally,))
        # heal
        hurt = [(c.hp / max(1, c.max_hp), c.i) for c in party if not c.dead and c.max_hp and c.hp > 0 and c.hp / c.max_hp < self.heal_below and c.row & 0x40 == 0]
        if hurt:
            _, ally = min(hurt)
            for sp in reversed(self.heal_ids):
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp:
                    if sp != self.heal_ids[0] and (party[ally].max_hp - party[ally].hp) < 40 * (self.heal_ids.index(sp) + 1): continue
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
        # potions (healer out of MP, or a fighter): the most hurt ally below potion_below
        low = [(c.hp / max(1, c.max_hp), c.i) for c in party if not c.dead and c.hp > 0 and c.max_hp and c.hp / c.max_hp < self.potion_below]
        if low:
            _, ally = min(low)
            inv = sim.inventory_slots
            for item in self.potion_ids:
                slot = next((sl for sl, it in inv.items() if it == item), None)
                if slot is not None: return Command.item(slot, party=(ally,))
        # attack magic
        for sp in reversed(self.attack_ids):
            cost = self._known(sim, char, sp)
            if cost is not None and cost <= me.mp and living_mon:
                return Command(MAGIC_COMMAND, 0x20, Command.monster_mask(target), 0, sp)
        return Command.fight(target)


class FleePolicy:
    """Run away: the real game flees while L+R are held (the caller holds them); a ready character just guards."""
    mode = "direct"
    def __init__(self): self.died = False
    def __call__(self, sim, char):
        if any(c.dead for c in sim.party): self.died = True
        return Command.guard()
