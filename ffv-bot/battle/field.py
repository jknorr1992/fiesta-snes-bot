"""Builds the field-side WRAM image the battle program starts from: the party (4 x 0x50-byte character structs at $0500),
inventory, gil, learned magic and battle config.  Everything the battle derives itself (equipment stats, spell lists, ATB, RNG
seed from WRAM sums, monster stats) is left to the ROM's own code.

    f = Field.from_wram(live_wram)            # exactly the live game (best fidelity)
    f = Field.new(data)                       # starting party from the ROM, level-1 stats
    f.chars[0].set_equipment(head="Helmet")   # etc.
    f.set_items({"Potion": 5})
    wram = f.to_wram()
"""
from data import GameData

FIELD_CHAR, CHAR_SIZE = 0x0500, 0x50
FIELD_ITEMS, FIELD_QTY = 0x0640, 0x0740
GIL, CONFIG1, MAGIC_BITS, BLUE_BITS = 0x0947, 0x0970, 0x0950, 0x0960
FIELD_DATA, RNG_SEED = 0x09B4, 0x0AF9

def _u8(o): return property(lambda s: s.raw[o], lambda s, v: s.raw.__setitem__(o, v & 0xFF))
def _u16(o): return property(lambda s: s.raw[o] | (s.raw[o + 1] << 8),
                             lambda s, v: (s.raw.__setitem__(o, v & 0xFF), s.raw.__setitem__(o + 1, (v >> 8) & 0xFF)) and None)

class Character:
    """One field character struct (see CreateFieldCharStruct in battle_ram.inc)."""
    row = _u8(0x00); job = _u8(0x01); level = _u8(0x02)
    hp = _u16(0x06); max_hp = _u16(0x08); mp = _u16(0x0A); max_mp = _u16(0x0C)
    head, body, accessory, rh_shield, lh_shield, rh_weapon, lh_weapon = (_u8(0x0E + i) for i in range(7))
    caught_monster = _u8(0x15)
    status1, status2, status3, status4 = _u8(0x1A), _u8(0x1B), _u8(0x1C), _u8(0x1D)
    passives1, passives2, element_up = _u8(0x20), _u8(0x21), _u8(0x22)
    base_str, base_agi, base_vit, base_mag = _u8(0x24), _u8(0x25), _u8(0x26), _u8(0x27)
    job_level = _u8(0x3A); ap = _u16(0x3B)
    equip_weapons, equip_armor = _u16(0x40), _u16(0x42)
    EQUIP_SLOTS = ("head", "body", "accessory", "rh_shield", "lh_shield", "rh_weapon", "lh_weapon")

    def __init__(self, raw=None):
        self.raw = bytearray(raw if raw is not None else bytes(CHAR_SIZE))
        assert len(self.raw) == CHAR_SIZE
    def copy(self): return Character(bytes(self.raw))
    @property
    def exp(self): return self.raw[3] | (self.raw[4] << 8) | (self.raw[5] << 16)
    @exp.setter
    def exp(self, v): self.raw[3:6] = (v & 0xFFFFFF).to_bytes(3, "little")
    @property
    def commands(self): return list(self.raw[0x16:0x1A])
    @commands.setter
    def commands(self, v): self.raw[0x16:0x1A] = bytes(v)
    @property
    def in_party(self): return not (self.row & 0x40)
    @property
    def back_row(self): return bool(self.row & 0x80)
    @back_row.setter
    def back_row(self, v): self.row = (self.row & 0x7F) | (0x80 if v else 0)

    def set_equipment(self, data=None, **slots):
        """set_equipment(data, head="Helmet", rh_weapon=0x12 ...): item names need a GameData; ids are used as-is."""
        for k, v in slots.items():
            assert k in self.EQUIP_SLOTS, k
            setattr(self, k, data.item_id(v) if isinstance(v, str) else v)
    def equipment(self): return {k: getattr(self, k) for k in self.EQUIP_SLOTS}
    def set_job(self, data, job):
        """Job id/name -> job id, default battle commands, equippable-type masks and innate passives from the ROM job tables.
        Stats, HP/MP and learned abilities are not touched (set them yourself or import them from the live game)."""
        j = data.job(data.job_id(job) if isinstance(job, str) else job)
        self.job = j["id"]; self.commands = j["commands"]
        self.raw[0x40:0x44] = j["equip_types"]       # JobEquipTbl layout == EquipWeapons/EquipArmor in the field struct
        p = j["passives"]; self.passives1 |= p & 0xFF; self.passives2 |= (p >> 8) & 0xFF
    def __repr__(self): return f"<Character job={self.job} lv={self.level} hp={self.hp}/{self.max_hp} mp={self.mp}/{self.max_mp}>"

class Field:
    def __init__(self, data=None, wram=None):
        self.data = data
        self.wram = bytearray(wram if wram is not None else bytes(0x20000))
        assert len(self.wram) == 0x20000
    # ---- construction
    @classmethod
    def from_wram(cls, wram, data=None): return cls(data, wram)
    @classmethod
    def new(cls, data, party=4, rng_seed=0x07, battle_config=0x2A):
        """Starting characters from the ROM (level 1, starting gear); the first `party` are put in the party."""
        f = cls(data)
        for i in range(4):
            ch = Character(data.initial_character(i))
            if i < party: ch.row &= ~0x40
            else: ch.row |= 0x40
            f.set_character(i, ch)
        f.wram[CONFIG1] = battle_config; f.wram[RNG_SEED] = rng_seed
        return f
    # ---- party
    @property
    def chars(self): return _CharList(self)
    def character(self, i): return Character(self.wram[FIELD_CHAR + CHAR_SIZE * i:FIELD_CHAR + CHAR_SIZE * (i + 1)])
    def set_character(self, i, ch): self.wram[FIELD_CHAR + CHAR_SIZE * i:FIELD_CHAR + CHAR_SIZE * (i + 1)] = ch.raw
    # ---- inventory (256 slots: item id + quantity)
    def items(self):
        return {self.wram[FIELD_ITEMS + i]: self.wram[FIELD_QTY + i] for i in range(256) if self.wram[FIELD_ITEMS + i] and self.wram[FIELD_QTY + i]}
    def set_items(self, items):
        """items: {name-or-id: qty}; replaces the inventory (qty capped at 99)."""
        self.wram[FIELD_ITEMS:FIELD_ITEMS + 256] = bytes(256); self.wram[FIELD_QTY:FIELD_QTY + 256] = bytes(256)
        for slot, (k, q) in enumerate(items.items()):
            iid = self.data.item_id(k) if isinstance(k, str) else k
            self.wram[FIELD_ITEMS + slot] = iid; self.wram[FIELD_QTY + slot] = min(int(q), 99)
    @property
    def gil(self): return int.from_bytes(self.wram[GIL:GIL + 3], "little")
    @gil.setter
    def gil(self, v): self.wram[GIL:GIL + 3] = int(v).to_bytes(3, "little")
    # ---- magic (bit i of MagicBits = spell i learned; blue magic separate)
    def set_magic(self, spell_ids=(), blue_ids=()):
        self.wram[MAGIC_BITS:MAGIC_BITS + 12] = bytes(12); self.wram[BLUE_BITS:BLUE_BITS + 4] = bytes(4)
        for s in spell_ids: self.wram[MAGIC_BITS + (s >> 3)] |= 0x80 >> (s & 7)
        for s in blue_ids: self.wram[BLUE_BITS + (s >> 3)] |= 0x80 >> (s & 7)
    @property
    def rng_seed(self): return self.wram[RNG_SEED]
    @rng_seed.setter
    def rng_seed(self, v): self.wram[RNG_SEED] = v & 0xFF
    def to_wram(self): return bytes(self.wram)
    # ---- import from a running/saved game
    @classmethod
    def from_spec(cls, data, spec, **kw):
        """Party / gear / items from a plain description, checked against the ROM tables (see party.py)."""
        import party
        return party.build_field(data, spec, **kw)
    @classmethod
    def from_state(cls, path, data=None):
        """WRAM out of a RetroArch/snes9x save state (tools/snes9x_state.py)."""
        import os, sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        import snes9x_state
        return cls(data, snes9x_state.State(path).wram[:0x20000])
    @classmethod
    def from_live(cls, game, data=None, chunk=512):
        """WRAM of a running RetroArch via Network Commands; `game.read(offset, n)` as in tools/play.py."""
        w = bytearray()
        for off in range(0, 0x20000, chunk): w += game.read(off, chunk)
        return cls(data, bytes(w))

class _CharList:
    """f.chars[i] returns a live proxy: attribute writes go straight back into the WRAM image."""
    def __init__(self, f): self.f = f
    def __len__(self): return 4
    def __getitem__(self, i):
        f = self.f; base = FIELD_CHAR + CHAR_SIZE * i
        class Proxy(Character):
            def __init__(p): p.raw = _View(f.wram, base)
        return Proxy()
class _View:
    def __init__(self, buf, base): self.buf, self.base = buf, base
    def __getitem__(self, i):
        if isinstance(i, slice): return self.buf[self.base + (i.start or 0):self.base + (i.stop if i.stop is not None else CHAR_SIZE)]
        return self.buf[self.base + i]
    def __setitem__(self, i, v):
        if isinstance(i, slice): self.buf[self.base + (i.start or 0):self.base + (i.stop if i.stop is not None else CHAR_SIZE)] = v
        else: self.buf[self.base + i] = v
