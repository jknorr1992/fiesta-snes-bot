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
MONSTER_NAMES, ITEM_NAMES, CMD_NAMES = 0x200050, 0x111380, 0x201150

_CH = {0xFF: " ", 0x96: " ", 0xC5: "-", 0x94: "in", 0x95: "it", 0x97: "li"}
def text(bs):
    o = []
    for b in bs:
        if 0x60 <= b <= 0x79: o.append(chr(65 + b - 0x60))
        elif 0x7A <= b <= 0x93: o.append(chr(97 + b - 0x7A))
        elif 0x54 <= b <= 0x5D: o.append(str(b - 0x54))
        else: o.append(_CH.get(b, "?" if b else ""))
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
