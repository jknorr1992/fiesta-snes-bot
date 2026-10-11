"""ROM tables for the battle simulator (FF5r Clean layout; all offsets are plain file offsets).  Table layouts follow the
everything8215/ff5 disassembly (notes/ff5-sfc-rom-map.txt, src/battle/battle_ram.inc) and were checked against the ROM."""
import functools

# --- table bases (file offsets) ---
MONSTER_PROP = 0x100000      # 384 x 32: 0-255 normal, 256-383 bosses (D0:0000 / D0:2000)
BATTLE_PROP = 0x103000       # 512 x 16
RAND_GROUPS = 0x106800       # 256 x 8 (4 battle ids, u16)
EVENT_GROUPS = 0x107800      # 128 x 4 (2 battle ids)
WORLD_GROUPS = 0x107A00      # 768 x 2 (-> random group)
MAP_GROUPS = 0x108000        # 512 x 2 (-> random group)
MAP_RATES = 0x1084C0         # 2 bits per map
JOB_NAMES, JOB_STATS, JOB_EQUIP, JOB_CMDS, JOB_PASSIVES = 0x115600, 0x1156B0, 0x115708, 0x115760, 0x1157B8
INITIAL_CHARS = 0x117000     # 4 x 0x50 (the starting field character structs)
ITEM_PROP_W, ITEM_PROP_A, EQUIP_TYPES, SPELL_NAMES = 0x110000, 0x110600, 0x112480, 0x111C80      # 12-byte property records; 4-byte equip-type masks; 6-byte spell names
LEVEL_EXP, LEVEL_HP, LEVEL_MP, JOB_ABILITY_COUNT, ENABLE_SPELLS_TBL = 0x115000, 0x115129, 0x1151EF, 0x1152EA, 0x00E931
MONSTER_NAMES, ITEM_NAMES, CMD_NAMES = 0x200050, 0x111380, 0x201150

# text encoding (FF5 US charset; digraph codes inferred from the names in this ROM; icon glyphs and unknown codes are dropped)
_CH = {0xFF: "", 0x96: " ", 0xC5: "-", 0xE2: "+", 0xCD: "%", 0xA1: "!", 0xA2: "?", 0xA3: ".", 0x9D: ",", 0x99: "'",
       0x94: "il", 0x95: "it", 0x97: "li", 0x98: "ll", 0xA4: "ti", 0xA5: "fi", 0xA6: "ro", 0xA7: "te", 0xA8: "le", 0xA9: "el", 0xAC: "if",
       0xAD: "It", 0xAE: "tl", 0xAF: "ir", 0xB0: "tt", 0xB1: "ie", 0xB2: "in", 0xB9: "la", 0xBA: "oi", 0xBB: "er"}
def text(bs):
    o = []
    for b in bs:
        if 0x60 <= b <= 0x79: o.append(chr(65 + b - 0x60))
        elif 0x7A <= b <= 0x93: o.append(chr(97 + b - 0x7A))
        elif 0x53 <= b <= 0x5C: o.append(str(b - 0x53))
        else: o.append(_CH.get(b, ""))
    return "".join(o).strip()

BATTLE_OVER = {0x80: "victory", 0x40: "defeat", 0x01: "escaped", 0x20: "timer"}

class GameData:
    def __init__(self, rom):
        self.r = rom
    def u16(self, o): return self.r[o] | (self.r[o + 1] << 8)

    # ---- names
    def monster_name(self, i): return text(self.r[MONSTER_NAMES + 10 * i:MONSTER_NAMES + 10 * i + 10])
    def item_name(self, i): return text(self.r[ITEM_NAMES + 9 * i:ITEM_NAMES + 9 * i + 9])
    def job_name(self, i): return text(self.r[JOB_NAMES + 8 * i:JOB_NAMES + 8 * i + 8])
    def command_name(self, i): return text(self.r[CMD_NAMES + 7 * i:CMD_NAMES + 7 * i + 7])
    def find_items(self, sub):
        """[(id, name)] of items whose (9-character, icon-prefixed) ROM name contains `sub`.  Names are truncated, so e.g.
        "Mythril" matches many weapons: use the id (or a longer unique prefix such as "MageMash")."""
        return [(i, self.item_name(i)) for i in range(256) if sub.lower() in self.item_name(i).lower().lstrip("?")]
    def item_id(self, name):
        """Id of the item whose name equals `name` (leading icon char ignored); KeyError if none, ValueError if ambiguous."""
        hits = [i for i in range(256) if self.item_name(i).lower().lstrip("?") == name.lower().lstrip("?")]
        if not hits: raise KeyError(name)
        if len(hits) > 1: raise ValueError(f"{name!r} is ambiguous: ids {hits}")
        return hits[0]
    def job_id(self, name):
        for i in range(22):
            if self.job_name(i).lower().startswith(name.lower()[:6]): return i
        raise KeyError(name)

    # ---- monsters / battles
    def monster(self, i):
        b = MONSTER_PROP + 32 * i; r = self.r
        return dict(id=i, name=self.monster_name(i), speed=r[b], attack=r[b + 1], attack_mult=r[b + 2], evade=r[b + 3],
                    defense=r[b + 4], magic=r[b + 5], mdefense=r[b + 6], mevade=r[b + 7], hp=self.u16(b + 8), mp=self.u16(b + 10),
                    exp=self.u16(b + 12), gil=self.u16(b + 14), level=r[b + 0x1F], status=bytes(r[b + 0x1A:b + 0x1E]).hex(),
                    weak=r[b + 0x17], absorb=r[b + 0x15], immune=r[b + 0x11], creature=r[b + 0x18])
    def battle(self, i):
        """Battle property record (index 0-511)."""
        b = BATTLE_PROP + 16 * (i & 0x1FF); r = self.r
        flags = r[b + 14]; boss = bool(flags & 0x20)
        mons = [r[b + 4 + k] for k in range(8)]
        ids = [(m + (256 if boss else 0)) if m != 0xFF else None for m in mons]
        return dict(id=i, intro=r[b], flee=r[b + 1], ap=r[b + 2], visible=r[b + 3], monster_ids=ids,
                    monsters=[self.monster_name(m) if m is not None else None for m in ids],
                    music=r[b + 13], flags=flags, boss=boss, no_run=bool(r[b + 1] & 0x80), raw=bytes(r[b:b + 16]).hex())
    def battle_summary(self, i):
        b = self.battle(i)
        return f"battle {i:#05x}: " + ", ".join(m for m in b["monsters"] if m) + (" [boss]" if b["boss"] else "")

    # ---- where encounters come from
    def random_group(self, g): return [self.u16(RAND_GROUPS + 8 * g + 2 * k) for k in range(4)]
    @functools.lru_cache(maxsize=None)
    def map_encounters(self, map_id):
        """Random battles on a normal map: (battle id, probability) with the field code's roll thresholds 90/90/60/16 of 256."""
        g = self.u16(MAP_GROUPS + 2 * map_id)
        rate = (self.r[MAP_RATES + (map_id >> 2)] >> (2 * (map_id & 3))) & 3
        probs = (90 / 256, 90 / 256, 60 / 256, 16 / 256)
        return dict(group=g, rate=rate, battles=list(zip(self.random_group(g), probs)))
    def world_encounters(self, region):
        g = self.u16(WORLD_GROUPS + 2 * region)
        return dict(group=g, battles=list(zip(self.random_group(g), (90 / 256, 90 / 256, 60 / 256, 16 / 256))))
    def event_battle(self, e):
        return [self.u16(EVENT_GROUPS + 4 * e + 2 * k) for k in range(2)]    # second used when the roll >= 0xC0

    # ---- jobs / starting characters
    def job(self, j):
        r = self.r
        return dict(id=j, name=self.job_name(j), stat_mods=list(r[JOB_STATS + 4 * j:JOB_STATS + 4 * j + 4]),
                    equip_types=bytes(r[JOB_EQUIP + 4 * j:JOB_EQUIP + 4 * j + 4]), commands=list(r[JOB_CMDS + 4 * j:JOB_CMDS + 4 * j + 4]),
                    passives=self.u16(JOB_PASSIVES + 2 * j))
    def initial_character(self, i): return bytes(self.r[INITIAL_CHARS + 0x50 * i:INITIAL_CHARS + 0x50 * i + 0x50])

    # ---- items / equipment (layouts from the menu's equip filter, menu-main.asm _c2ecf3 / _c2d9ab)
    def item_icon(self, i): return self.r[ITEM_NAMES + 9 * i]
    def item_prop(self, i):
        """12-byte property record: weapons 2-0x7F, armor 0x80-0xDF, consumables (8-byte records) 0xE0-0xFF."""
        if 2 <= i < 0x80: o = ITEM_PROP_W + 12 * i
        elif 0x80 <= i < 0xE0: o = ITEM_PROP_A + 12 * (i - 0x80)
        else: return None
        return bytes(self.r[o:o + 12])
    def equip_type_masks(self, t):
        o = EQUIP_TYPES + 4 * (t & 0x3F); return self.u16(o), self.u16(o + 2)       # (weapon-class mask, armor-class mask)
    ITEM_BLANK = {"", "Item0000", "Dummy", "Empty", "Left", "Right"}
    def item_valid(self, i): return 2 <= i < 256 and self.item_name(i) not in self.ITEM_BLANK
    def item_kind(self, i):
        """weapon | shield | head | body | accessory | consumable (None for placeholder ids)."""
        if not self.item_valid(i): return None
        if i >= 0xE0: return "consumable"
        p = self.item_prop(i)
        if i < 0x80: return "weapon" if self.equip_type_masks(p[2]) != (0, 0) else None
        for bit, k in ((8, "shield"), (4, "head"), (2, "body"), (1, "accessory")):
            if p[0] & bit: return k
        return None
    def item_equippable(self, i, weapon_mask, armor_mask):
        """The menu's test: the item's equip class must intersect the character's weapon or armor class mask (from the job)."""
        p = self.item_prop(i)
        if p is None: return False
        w, a = self.equip_type_masks(p[2]); return bool(w & weapon_mask) or bool(a & armor_mask)

    # ---- level tables
    def level_exp(self, level): o = LEVEL_EXP + 3 * (level - 1); return self.r[o] | self.r[o + 1] << 8 | self.r[o + 2] << 16
    def level_hp_mp(self, level, vit, mag):
        """Max HP/MP after reaching `level` (>= 2), as the battle program's LevelUp routine computes them."""
        hp0, mp0 = self.u16(LEVEL_HP + 2 * (level - 1)), self.u16(LEVEL_MP + 2 * (level - 1))
        return min(9999, hp0 + (hp0 * vit >> 5)), min(999, mp0 + (mp0 * mag >> 5))
    def job_ability_count(self, j): return self.r[JOB_ABILITY_COUNT + j]
    def enable_spells(self, commands):
        """Spell levels per magic type that the four battle commands allow (menu-main.asm _c2e6e0) -> the field char's 3 EnableSpells bytes."""
        lv = [0] * 6
        for c in commands:
            if c & 0x80 or c < 0x2C or c >= 0x4D: continue
            if c < 0x49:
                t = self.r[ENABLE_SPELLS_TBL + c - 0x2C]; typ, l = t >> 4, t & 15
                lv[typ] = max(lv[typ], l)
            else:
                l = 3 if c == 0x4C else c - 0x48
                lv[1] = max(lv[1], l); lv[2] = max(lv[2], l)
        return bytes((lv[2 * k] << 4) | lv[2 * k + 1] for k in range(3))
    def spell_name(self, i): return text(self.r[SPELL_NAMES + 6 * i:SPELL_NAMES + 6 * i + 6])
    def spell_icon(self, i):
        return {0xE3: "sword", 0xE4: "white", 0xE5: "black", 0xE6: "time", 0xBE: "special", 0xC4: "music", 0xBC: "key", 0xC6: "star"}.get(self.r[SPELL_NAMES + 6 * i], "spell")
