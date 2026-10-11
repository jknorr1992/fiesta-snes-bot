"""Describe a party, its equipment and the inventory in plain terms and get a ready-to-fight Field, with every value checked against the ROM's tables.

    spec = {
      "gil": 2000,
      "party": [
        {"character": "Bartz", "job": "Knight", "level": 25, "row": "front",
         "equipment": {"right_hand": "Broad", "left_hand": "Aegis", "head": "Plumed", "body": "Copper", "accessory": "Power"}},
        {"character": "Lenna", "job": "WhiteMage", "level": 25, "row": "back", "hp": 0.5}],
      "items": {"Potion": 10, "Hi-Potion": 3},
      "magic": ["Cure", "Cure2", "Protect"],
    }
    field = build_field(data, spec)            # raises SpecError listing every illegal value
    # or: Field.from_spec(data, spec) / run.py fight 0x5d --spec party.json

What is checked (all from the ROM, the same tests the game's own menus apply):
  character  one of the four field characters (Bartz, Lenna, Galuf/Krile, Faris); no duplicates; 1-4 in the party, at most one per slot
  job        a job in the job table (Knight ... Freelancer); gives the battle commands, equip classes and passives of the job table;
             job_level 0..(number of abilities of that job) is recorded as is
  level      1-99; exp, max HP and max MP are derived (exp table, and the LevelUp HP/MP formula with the character's Vit/Mag);
             hp / mp may only be a fraction 0-1, "full", or a number <= the maximum
  equipment  right_hand / left_hand (weapon or shield), head, body, accessory: the item must exist, belong in that slot (item property
             record) and be equippable by the job (equip class masks); empty slots are fine
  items      real inventory items only (no placeholders), quantity 1-99, at most 256 kinds
  magic      spell ids 0-95 that have a name ("Cure", or "black:Fire" / "sword:Fire" when the name is shared); which ones a character can cast is then decided by the ROM from the job's commands
Not covered: learned job abilities beyond the job's own commands, mastery bonuses, status effects.
Items are named by ROM name (9 characters, e.g. "Hi-Potion", "Mythril"), optionally "slot:Name" ("shield:Mythril", "head:Mythril") when
a name is shared, or by id; an ambiguous name is an error that lists the candidates."""
from data import GameData, text
from field import Field, Character, CHAR_SIZE

CHARACTERS = {"bartz": 0, "lenna": 1, "galuf": 2, "krile": 2, "faris": 3}
CHARACTER_NAMES = ["Bartz", "Lenna", "Galuf", "Faris"]
HANDS = ("right_hand", "left_hand")
ARMOR_SLOTS = ("head", "body", "accessory")
SLOT_KIND = {"head": "head", "body": "body", "accessory": "accessory"}
EMPTY = {"head": 0x80, "body": 0x80, "accessory": 0x80, "rh_shield": 0, "lh_shield": 0, "rh_weapon": 0, "lh_weapon": 0}
JOB_ALIASES = {"mysticknight": "SpellBlade", "mystic": "SpellBlade", "beastmaster": "Tamer", "whitemage": "WhiteMage", "blackmage": "BlackMage",
               "timemage": "TimeMage", "bluemage": "BlueMage", "redmage": "RedMage", "oracle": "Geomancer"}

class SpecError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("illegal party specification:\n  - " + "\n  - ".join(self.problems))

def _norm(s): return "".join(ch for ch in str(s).lower() if ch.isalnum())

class Catalog:
    """Name lookups over the ROM tables."""
    def __init__(self, data):
        self.d = data
        self.jobs = {_norm(data.job_name(j)): j for j in range(22) if data.job_name(j)}
        for k, v in JOB_ALIASES.items():
            if _norm(v) in self.jobs: self.jobs.setdefault(k, self.jobs[_norm(v)])
        self.items = {}
        for i in range(2, 256):
            if data.item_valid(i): self.items.setdefault(_norm(data.item_name(i)), []).append(i)
        self.spells = {}
        for i in range(96):
            n = data.spell_name(i)
            if n and i < 87: self.spells.setdefault(_norm(n), []).append(i)
    def category(self, i):
        d = self.d; k = d.item_kind(i)
        if k == "weapon":
            sym = {0xE3: "sword", 0xE7: "dagger", 0xE8: "spear", 0xE9: "axe", 0xEA: "katana", 0xEB: "rod", 0xEC: "staff", 0xED: "bow", 0xEE: "harp",
                   0xEF: "whip", 0xF0: "bell", 0xCA: "claw", 0xC8: "scroll", 0xBF: "hammer", 0xC6: "star"}.get(d.item_icon(i))
            return sym or "weapon"
        return k or ("throwable" if i < 0x80 else "item")
    def describe(self, i): return f"{i:#04x} {self.category(i)}:{self.d.item_name(i)}"
    def item(self, ref, allow=None, what="item"):
        """ref: id, "Name" or "category:Name".  allow(i)->bool restricts the candidates (e.g. to what fits a slot)."""
        d = self.d
        if isinstance(ref, int):
            if not d.item_valid(ref): raise SpecError([f"{what}: item id {ref} is not a real item"])
            return ref
        cat = None
        if ":" in str(ref): cat, ref = str(ref).split(":", 1); cat = _norm(cat)
        c = self.items.get(_norm(ref), [])
        if cat: c = [i for i in c if _norm(self.category(i)) == cat]
        if not c: raise SpecError([f"{what}: no item named {ref!r}" + (f" in category {cat!r}" if cat else "")])
        fit = [i for i in c if allow is None or allow(i)]
        if not fit: raise SpecError([f"{what}: {ref!r} cannot go there (candidates: {', '.join(self.describe(i) for i in c)})"])
        if len(fit) > 1: raise SpecError([f"{what}: {ref!r} is ambiguous, say one of: {', '.join(self.describe(i) for i in fit)}"])
        return fit[0]

def legal_items(data, slot, job=None):
    """Item ids that may be equipped in `slot` (right_hand/left_hand/head/body/accessory) by `job` (None = ignore the job)."""
    cat = Catalog(data); out = []
    wm = am = 0xFFFF
    if job is not None:
        j = data.job(cat.jobs[_norm(job)] if isinstance(job, str) else job); wm = int.from_bytes(j["equip_types"][0:2], "little"); am = int.from_bytes(j["equip_types"][2:4], "little")
    for i in range(2, 0xE0):
        k = data.item_kind(i)
        if k is None or (slot in HANDS and k not in ("weapon", "shield")) or (slot in ARMOR_SLOTS and k != SLOT_KIND[slot]): continue
        if data.item_equippable(i, wm, am): out.append(i)
    return out

def _check_qty(q, what, problems):
    if isinstance(q, bool) or not isinstance(q, int) or not 1 <= q <= 99: problems.append(f"{what}: quantity {q!r} must be an integer 1-99"); return False
    return True

def build_field(data, spec, rng_seed=0x07, battle_config=0x2A):
    """Validate `spec` (see the module doc) and return a Field.  Raises SpecError with every problem found."""
    d = data; cat = Catalog(d); problems = []
    extra = set(spec) - {"gil", "party", "items", "magic"}
    if extra: problems.append(f"unknown keys: {sorted(extra)}")
    members = spec.get("party") or []
    if not 1 <= len(members) <= 4: problems.append(f"party: needs 1-4 members, got {len(members)}")
    f = Field.new(d, party=0, rng_seed=rng_seed, battle_config=battle_config)
    used = set(); built = {}
    for n, m in enumerate(members):
        tag = f"party[{n}]"
        bad = set(m) - {"character", "job", "job_level", "level", "row", "hp", "mp", "equipment"}
        if bad: problems.append(f"{tag}: unknown keys {sorted(bad)}")
        cname = str(m.get("character", "")).lower()
        if cname not in CHARACTERS: problems.append(f"{tag}: character {m.get('character')!r} must be one of Bartz, Lenna, Galuf (or Krile), Faris"); continue
        slot = CHARACTERS[cname]; tag = f"{CHARACTER_NAMES[slot]}"
        if slot in used: problems.append(f"{tag}: listed twice"); continue
        used.add(slot)
        ch = Character(d.initial_character(slot)); ch.row &= ~0x40
        # job
        jref = m.get("job", "Freelancer")
        jid = jref if isinstance(jref, int) else cat.jobs.get(_norm(jref))
        if jid is None or not 0 <= jid < 22: problems.append(f"{tag}: unknown job {jref!r} (jobs: {', '.join(d.job_name(j) for j in range(22))})"); continue
        ch.passives1 = 0; ch.passives2 = 0; ch.set_job(d, jid)
        # level -> exp / HP / MP
        lv = m.get("level", 1)
        if isinstance(lv, bool) or not isinstance(lv, int) or not 1 <= lv <= 99: problems.append(f"{tag}: level {lv!r} must be an integer 1-99"); continue
        ch.level = lv; ch.exp = d.level_exp(lv)
        if lv >= 2: ch.max_hp, ch.max_mp = d.level_hp_mp(lv, ch.base_vit, ch.base_mag)
        ch.raw[0x3D:0x40] = d.enable_spells(ch.commands)
        for key, mx, setter in (("hp", ch.max_hp, "hp"), ("mp", ch.max_mp, "mp")):
            v = m.get(key, "full")
            if v == "full": cur = mx
            elif isinstance(v, float) and 0 <= v <= 1: cur = max(1 if key == "hp" else 0, round(mx * v))
            elif isinstance(v, int) and not isinstance(v, bool) and (1 if key == "hp" else 0) <= v <= mx: cur = v
            else: problems.append(f"{tag}: {key} {v!r} must be 'full', a fraction 0-1, or a number up to the maximum ({mx})"); cur = mx
            setattr(ch, setter, cur)
        row = m.get("row", "front")
        if row not in ("front", "back"): problems.append(f"{tag}: row must be 'front' or 'back'")
        ch.back_row = (row == "back")
        # equipment
        wm, am = ch.equip_weapons, ch.equip_armor
        eq = m.get("equipment", {}) or {}
        bad = set(eq) - set(HANDS) - set(ARMOR_SLOTS)
        if bad: problems.append(f"{tag}: unknown equipment slots {sorted(bad)} (use right_hand, left_hand, head, body, accessory)")
        for s in ch.EQUIP_SLOTS: setattr(ch, s, EMPTY[s])
        for s in HANDS + ARMOR_SLOTS:
            ref = eq.get(s)
            if ref in (None, "", 0): continue
            what = f"{tag}.{s}"
            if s in HANDS: ok = lambda i: d.item_kind(i) in ("weapon", "shield") and d.item_equippable(i, wm, am)
            else: ok = lambda i, s=s: d.item_kind(i) == SLOT_KIND[s] and d.item_equippable(i, wm, am)
            try: i = cat.item(ref, ok, what)
            except SpecError as e: problems += e.problems; continue
            if s in HANDS:
                hand = "rh" if s == "right_hand" else "lh"
                setattr(ch, hand + ("_shield" if d.item_kind(i) == "shield" else "_weapon"), i)
            else: setattr(ch, s, i)
        for hand in ("rh", "lh"):
            if getattr(ch, hand + "_weapon") and getattr(ch, hand + "_shield"): problems.append(f"{tag}: both a weapon and a shield in the {hand} hand")
        jl = m.get("job_level", 0); mx = d.job_ability_count(jid)
        if isinstance(jl, bool) or not isinstance(jl, int) or not 0 <= jl <= mx: problems.append(f"{tag}: job_level {jl!r} must be an integer 0-{mx} for {d.job_name(jid)}")
        else: ch.job_level = jl
        built[slot] = ch
    if not problems or built:
        for slot in range(4):
            if slot in built: f.set_character(slot, built[slot])
            else:
                ch = Character(d.initial_character(slot)); ch.row |= 0x40; f.set_character(slot, ch)
    # inventory
    items = spec.get("items", {}) or {}
    inv = {}
    if len(items) > 256: problems.append("items: more than 256 kinds")
    for k, q in items.items():
        try: i = cat.item(k, None, f"items[{k!r}]")
        except SpecError as e: problems += e.problems; continue
        if _check_qty(q, f"items[{k!r}]", problems): inv[i] = inv.get(i, 0) + q
    for i, q in list(inv.items()):
        if q > 99: problems.append(f"items: {cat.describe(i)} totals {q} (max 99)")
    # spells
    spells = []
    for s in spec.get("magic", []) or []:
        if isinstance(s, int) and not isinstance(s, bool):
            if 0 <= s < 96 and d.spell_name(s): spells.append(s)
            else: problems.append(f"magic: spell id {s} is not a spell (0-95)")
        else:
            sc, sn = (str(s).split(":", 1) if ":" in str(s) else (None, str(s)))
            c = [i for i in cat.spells.get(_norm(sn), []) if sc is None or d.spell_icon(i) == _norm(sc)]
            if not c: problems.append(f"magic: no spell named {s!r}")
            elif len(c) > 1: problems.append(f"magic: {s!r} is ambiguous, say one of: {', '.join(d.spell_icon(i) + ':' + d.spell_name(i) + f' ({i})' for i in c)}")
            else: spells.append(c[0])
    gil = spec.get("gil", 0)
    if isinstance(gil, bool) or not isinstance(gil, int) or not 0 <= gil <= 9_999_999: problems.append(f"gil: {gil!r} must be an integer 0-9999999")
    if problems: raise SpecError(problems)
    f.set_items({i: q for i, q in inv.items()}); f.set_magic(spell_ids=spells); f.gil = gil
    return f

def load_spec(path):
    import json
    with open(path) as fh: return json.load(fh)
