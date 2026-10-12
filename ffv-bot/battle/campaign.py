#!/usr/bin/env python3
"""Simulated campaign: fight the way through the regions of data/encounters_by_region.json with the real battle program.

    python3 battle/campaign.py --rom ROM --out DIR [--jobs 4] [--runs 64]

The party walks the regions in the order the route reached them (data/reached_regions.json).  In every region it fights the region's
battles (random ones by their ROM probabilities, scripted ones once) one after another; each fight is a batch of `--runs` battles
started from the party's current state (RNG seeds spread over the game's 256 random-table start points).

  * A fight COUNTS only if >= 95% of the batch are victories with nobody dead (at any moment).  The next fight then starts from the
    batch's average result: the run whose total exp is closest to the batch mean, with HP/MP set to the mean over the flawless runs and
    gil set to start gil + mean gil.
  * If a fight does not count, the party stays where it was and the previous region is simulated again (healed, new gear bought) until
    every member has a quantifiable power boost (a higher level or better equipped gear score), then the failed region is tried again.
  * Healing: only a Tent (price from the ROM) at the end of a region, on the world map before a fight when HP is under 60%, or at the
    one save point assumed in the middle of every region with 3+ maps.  No Tent money = no healing.
  * Gear: before each region the party buys, from the shops of the regions reached so far (data/shops_by_map.json: stock and prices
    from the ROM), the upgrades with the best (power gain)/(net price), selling the replaced item for half its price, keeping gil for
    one Tent.  Magic shops: Cure / attack spells for the white / black mage.  Everything must be legal per the ROM's equip tables.
  * Party: Bartz, Lenna, Galuf from the start as Freelancers; Faris joins when the party has finished the West Valley (level = max(3,
    average of the others)); jobs are assigned when the Wind Crystal region is finished (--jobs-spec, default Knight / White Mage / Monk /
    Black Mage).  Policy: battle/policy.py.
Assumptions that are parameters: steps walked per local map (--steps-per-map) and steps between random encounters per rate."""
import argparse, json, os, sys, time, hashlib
from dataclasses import dataclass, field as dfield
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from multiprocessing import Pool
from data import GameData
from field import Field, Character, FIELD_CHAR, CHAR_SIZE, RNG_SEED, FIELD_ITEMS, FIELD_QTY
from party import Catalog, _norm
from policy import SmartPolicy, FleePolicy, TacticalPolicy, VeteranPolicy
from knowledge import Bestiary
from sim import L, R, Command

TENT = "Tent"
LIQUID_FLAME = 444
CASTLE_FRAMES = 18000       # user (2026-10-12): fighting in the exploding Castle Karnak may take at most 18000 frames, else game over -> back to just after Liquid Flame
STEPS_BETWEEN = {"normal": 24, "low": 48, "high": 12, "very high": 6}
SUBS = ("Shop", "Inn", "Tavern", "Jail", "Storage", "Throne Room", "King's Room", "Queen's Room", "Lenna's Room", "Rooftop", "Grand Hall", "Sealed Room", "House")
DEFAULT_JOBS = {"Bartz": "Knight", "Lenna": "WhiteMage", "Galuf": "Monk", "Faris": "BlackMage"}
SLOT_NAMES = ("Bartz", "Lenna", "Galuf", "Faris")

# ------------------------------------------------------------------ battle workers
_W = {}
def _init(rom_path, policy="tactical", boss_deaths=False):
    from sim import BattleSim
    rom = open(rom_path, "rb").read()
    _W["sim"] = BattleSim(rom); _W["data"] = GameData(rom); _W["policy"] = policy; _W["boss_deaths"] = boss_deaths

def _policy(d, enc):
    if _W.get("policy") == "smart": return SmartPolicy(d)
    b = d.battle(enc)
    hard = bool(b["no_run"] or b["boss"])
    if _W.get("policy") == "veteran":
        if "bestiary" not in _W: _W["bestiary"] = Bestiary(d)
        return VeteranPolicy(d, _W["bestiary"], b["monster_ids"], hard=hard)
    return TacticalPolicy(d, hard=hard)

def _run(job):
    from scenario import run_battle
    wram, enc, bg, seed, mode = job
    d = _W["data"]
    w = bytearray(wram); w[RNG_SEED] = seed & 0xFF
    pol = None
    for attempt in range(4):             # a rare sim deadlock (direct command raced a status change) is retried on the next seed; counted in `stalls`
        w[RNG_SEED] = (seed + attempt) & 0xFF
        pol = FleePolicy() if mode == "flee" else _policy(d, enc)
        _W["sim"].hold = (L | R) if mode == "flee" else 0           # running away = holding L+R
        r = run_battle(bytes(w), enc, bg, policy=pol, max_frames=40000, sim=_W["sim"], keep_wram=True)
        _W["sim"].hold = 0
        if not r.crash.startswith("sim stall"): break
    stalls = attempt
    dead_end = any(c.hp == 0 or (c.status1 & 0xC0) for c in r.party_after if c.in_party)
    b = d.battle(enc)
    if mode != "flee" and (b["no_run"] or b["boss"]) and _W.get("boss_deaths"):         # boss fights: a win counts even when somebody fell
        flawless = r.outcome == "victory"
    else:
        flawless = r.outcome == ("escaped" if mode == "flee" else "victory") and not pol.died and not dead_end
    return dict(outcome=r.outcome, flawless=flawless, frames=r.frames, gil=r.gil_gained, exp=r.exp_gained, items=r.items_won,
                stalls=stalls, wram=r.wram if r.outcome in ("victory", "escaped") else b"")

# ------------------------------------------------------------------ gear / shops
class Shops:
    def __init__(self, data, path):
        self.d = data; j = json.load(open(path))
        self.maps = {int(k): v for k, v in j["maps"].items()}; self.shops = {int(k): v for k, v in j["shops"].items()}
        self.cat = Catalog(data)
        self.tent = data.item_id(TENT) if hasattr(data, "item_id") else None
        for s in self.shops.values():
            for it in s["stock"]:
                if s["type"] == 3 and it["name"] == TENT: self.tent_price = it["price"]
        self.tent_price = getattr(self, "tent_price", 250)
    def stock(self, maps):
        """{'weapon': {id: price}, 'armor': {...}, 'magic': {...}, 'item': {...}} sold by the shops of these maps."""
        out = {"weapon": {}, "armor": {}, "magic": {}, "item": {}}
        kind = {0: "magic", 1: "weapon", 2: "armor", 3: "item"}
        for m in maps:
            for s in self.maps.get(m, []):
                sh = self.shops.get(s)
                if sh and sh["type"] in kind:
                    for it in sh["stock"]: out[kind[sh["type"]]][it["id"]] = it["price"]
        return out

def gear_score(d, i):
    """Power score of a weapon / armor piece from its ROM property record (attack power, defense, magic defense, shield evade)."""
    if not i or i in (0x80,): return 0
    p = d.item_prop(i)
    if p is None: return 0
    kind = d.item_kind(i)
    if kind == "weapon": return 3 * p[7]
    if kind == "shield": return 3 * p[7] + p[6] // 8 + p[9]
    return 3 * p[7] + p[9]

SLOT_FIELDS = {"right_hand": ("rh_weapon",), "left_hand": ("lh_shield",), "head": ("head",), "body": ("body",)}

def member_power(d, ch):
    return (ch.level, sum(gear_score(d, getattr(ch, s)) for s in ("rh_weapon", "lh_shield", "head", "body")))

def equip_candidates(d, ch):
    """{slot: [item ids the member's job may wear there]} for the slots the buying logic manages."""
    wm, am = ch.equip_weapons, ch.equip_armor
    c = {"rh_weapon": [], "lh_shield": [], "head": [], "body": []}
    for i in range(2, 0xE0):
        k = d.item_kind(i)
        if k is None or not d.item_equippable(i, wm, am): continue
        if k == "weapon": c["rh_weapon"].append(i)
        elif k == "shield": c["lh_shield"].append(i)
        elif k in ("head", "body"): c[k].append(i)
    return c

def item_price(shops, d, i):
    for sh in shops.shops.values():
        for it in sh["stock"]:
            if it["id"] == i and sh["type"] in (1, 2): return it["price"]
    return 0

TREASURE_MAP, TREASURE_PROP = 0x113000, 0x113210          # ROM D1/3000: first treasure index per map (1 byte), D1/3210: x, y, kind, value (4 bytes each)

def chests(d, m):
    """[(treasure index, kind, value)] of the plain chests on map m: kind 'gil' (value = gil), 'spell', 'item'; monster-in-a-box chests are left out."""
    r = d.r; out = []
    for k in range(r[TREASURE_MAP + m], r[TREASURE_MAP + m + 1]):
        b2, b3 = r[TREASURE_PROP + 4 * k + 2], r[TREASURE_PROP + 4 * k + 3]
        if b2 & 0x80: continue
        a = b2 & 0xE0
        if a == 0: out.append((k, "gil", b3 * 10 ** (b2 & 7)))
        elif a == 0x20: out.append((k, "spell", b3))
        else: out.append((k, "item", b3))
    return out

def equip_owned(state, d, log):
    """Wear the best owned (looted / dropped) gear: per member and slot the owned piece with the biggest gear-score gain; the old piece goes back to the bag."""
    items = state.items()
    while True:
        best = None
        for slot_i in range(4):
            ch = state.character(slot_i)
            if not ch.in_party: continue
            for slot, ids in equip_candidates(d, ch).items():
                cur = getattr(ch, slot); cs = gear_score(d, cur)
                if slot == "lh_shield" and ch.lh_weapon: continue
                for i in ids:
                    if items.get(i, 0) < 1: continue
                    g = gear_score(d, i) - cs
                    if g > 0 and (best is None or g > best[0]): best = (g, slot_i, slot, i, cur)
        if not best: return
        _, slot_i, slot, i, old = best
        ch = state.character(slot_i); setattr(ch, slot, i); state.set_character(slot_i, ch)
        items[i] -= 1
        if items[i] <= 0: del items[i]
        if old and old != 0x80: items[old] = items.get(old, 0) + 1
        state.set_items(items)
        log.append(f"{SLOT_NAMES[slot_i]}: {slot} {d.item_name(old) if old and old != 0x80 else '-'} -> {d.item_name(i)} (owned)")

SPELL_VALUE = {"Protect": 45, "Life": 55, "Cure": 60, "Cure2": 70, "Cure3": 110, "Fire": 30, "Ice": 30, "Bolt": 30, "Fire2": 60, "Ice2": 60, "Bolt2": 60, "Fire3": 100, "Ice3": 100, "Bolt3": 100}

def shop(state, stock, shops, d, reserve, jobs, log, elem=None):
    """Greedy: repeatedly make the purchase (equipment upgrade for a member, or a spell for a white/black mage) with the best
    (power gain)/(net gil) that the money allows, keeping `reserve` gil.  Equipment gain = gear_score difference; spells have fixed values."""
    price_of = {**stock["weapon"], **stock["armor"]}
    while True:
        best = None
        for slot_i in range(4):
            ch = state.character(slot_i)
            if not ch.in_party: continue
            for slot, ids in equip_candidates(d, ch).items():
                cur = getattr(ch, slot)
                cur_score = gear_score(d, cur)
                resale = item_price(shops, d, cur) // 2 if cur and cur != 0x80 else 0
                for i in ids:
                    if i not in price_of or i == cur: continue
                    gain = gear_score(d, i) - cur_score
                    if gain <= 0: continue
                    net = max(price_of[i] - resale, 1)
                    if state.gil - net < reserve: continue
                    if slot == "lh_shield" and ch.lh_weapon: continue
                    if best is None or gain / net > best[0]: best = (gain / net, "gear", slot_i, slot, i, net, resale)
        have = {s for s in range(96) if state.wram[MAGIC_BITS + (s >> 3)] & (0x80 >> (s & 7))}
        for sp, price in stock["magic"].items():
            nm = d.spell_name(sp); icon = d.spell_icon(sp)
            if sp in have or nm not in SPELL_VALUE or icon not in ("white", "black"): continue
            if ("WhiteMage" if icon == "white" else "BlackMage") not in jobs.values(): continue
            if state.gil - price < reserve: continue
            val = SPELL_VALUE[nm]
            if elem and icon == "black":                      # an element the coming monsters are weak to is worth more; one they absorb / resist, less
                val *= 1 + elem.get(d.r[0x110B80 + 8 * sp + 7], 0)
            if best is None or val / price > best[0]: best = (val / price, "spell", sp, nm, price)
        if not best: break
        if best[1] == "spell":
            _, _, sp, nm, price = best
            state.wram[MAGIC_BITS + (sp >> 3)] |= 0x80 >> (sp & 7); state.gil = state.gil - price
            log.append(f"learned {nm} ({price} gil)")
        else:
            _, _, slot_i, slot, i, net, resale = best
            ch = state.character(slot_i); old = getattr(ch, slot)
            setattr(ch, slot, i); state.set_character(slot_i, ch); state.gil = state.gil - net
            log.append(f"{SLOT_NAMES[slot_i]}: {slot} {d.item_name(old) if old and old != 0x80 else '-'} -> {d.item_name(i)} (net {net} gil)")

from field import MAGIC_BITS

# ------------------------------------------------------------------ regions
def build_stages(enc_json, reached_json, areas_json, steps_per_map, jobs_data=None):
    """Ordered list of stages from the route: consecutive steps in the same place form one stage."""
    enc = json.load(open(enc_json)); reached = json.load(open(reached_json)); areas = json.load(open(areas_json))
    def label(m):
        for k in range(m, max(m - 25, 10), -1):
            t = areas.get(str(k))
            if t and not any(s in t for s in SUBS): return t
        return "(untitled maps)"
    maps_by_label = {}
    for name, reg in enc["regions"].items():
        if reg["kind"] == "local":
            for m in reg["maps"]: maps_by_label[m["map"]] = (name, m)
    step_loc = {}
    for ms, ss in reached["local_maps"].items():
        for s in ss: step_loc.setdefault(s, {"L": set(), "W": {}})["L"].add(label(int(ms)))
    for w, x, y, veh, s in reached["world_tiles"]:
        step_loc.setdefault(s, {"L": set(), "W": {}})["W"].setdefault((w, x >> 5, y >> 5), 0)
        step_loc[s]["W"][(w, x >> 5, y >> 5)] += 1
    stages = []
    for s in sorted(step_loc):
        loc = step_loc[s]
        prev = stages[-1] if stages else None
        if loc["L"]:
            lab = sorted(loc["L"])
            pick = next((l for l in lab if not prev or l != prev["name"]), lab[0])
            if prev and prev["kind"] == "local" and prev["name"] in lab: pick = prev["name"]
            if prev and prev["name"] == pick:
                prev["steps"].append(s)
            else: stages.append(dict(name=pick, kind="local", steps=[s], world={}))
        else:
            if prev and prev["kind"] == "world":
                prev["steps"].append(s)
                for b, n in loc["W"].items(): prev["world"][b] = prev["world"].get(b, 0) + n
            else: stages.append(dict(name="world", kind="world", steps=[s], world=dict(loc["W"])))
    # attach battles
    wname = {}
    for name, reg in enc["regions"].items():
        if reg["kind"] == "world":
            w = reg["world"]; import re
            mm = re.search(r"x (\d+)-\d+, y (\d+)-\d+", name)
            wname[(w, int(mm.group(1)) // 32, int(mm.group(2)) // 32)] = reg
    for st in stages:
        st["fights"] = []        # (battle id, background, weight)
        st["maps"] = []
        if st["kind"] == "local":
            reg = enc["regions"].get(st["name"], {"maps": []})
            seen_maps = {int(m) for m, ss in reached["local_maps"].items() if any(s in st["steps"] for s in ss)}
            walked = 0
            for m in reg["maps"]:
                if m["map"] not in seen_maps: continue
                st["maps"].append(m["map"])
                if m["random_battles_enabled"] and m.get("random_battles"):
                    rate = m.get("random_rate", "normal")
                    n = steps_per_map / STEPS_BETWEEN[rate]
                    st["fights"].append(dict(kind="random", map=m["map"], expected=n, bg=m["default_background"],
                                             battles=[(b["battle"], b["chance"]) for b in m["random_battles"]]))
                for e in m["event_battles"]:
                    if not e["on_route"]: continue
                    bg = e["background"] if isinstance(e["background"], int) else m["default_background"]
                    st["fights"].append(dict(kind="scripted", map=m["map"], expected=1, bg=bg, event=e["started_by_event"],
                                             battles=[(b["battle"], b["chance"]) for b in e["battles"]]))
        else:
            for blk, n in st["world"].items():
                reg = wname.get(blk)
                if not reg: continue
                tot = sum(t.get("tiles_visited", 1) for t in reg["terrains"] if t.get("random_battles")) or 1
                for t in reg["terrains"]:
                    if not t.get("random_battles") or t["rate"] == "none": continue
                    bgv = t["background"]; bgv = int(str(bgv).split()[0], 0) if isinstance(bgv, str) else bgv
                    share = t["tiles_visited"] / tot
                    st["fights"].append(dict(kind="random", map=("W", blk), expected=n * share / STEPS_BETWEEN[t["rate"]], bg=bgv,
                                             battles=[(b["battle"], b["chance"]) for b in t["random_battles"]]))
        st["dungeon"] = len(st["maps"]) >= 3
    return stages

def n_random(stage): return sum(max(1, round(f['expected'])) for f in stage['fights'] if f['kind'] == 'random')

def schedule(stage, offset=0, random_only=False):
    """Concrete ordered fights of one pass of the stage: every distinct battle at least once, random ones repeated by expectation,
    scripted ones last (bosses).  Deterministic (`offset` rotates the low-discrepancy picks between passes)."""
    out = []; seen = set()
    for f in stage["fights"]:
        if f["kind"] != "random": continue
        n = max(1, round(f["expected"])); bs = f["battles"]
        total = sum(c for _, c in bs)
        merged = {}
        for b, c in bs: merged[b] = merged.get(b, 0) + c / total
        picked = []
        for k in range(n):
            u = ((k + offset) * 0.6180339887 + 0.37) % 1.0; acc = 0
            for b, c in sorted(merged.items()):
                acc += c
                if u < acc: picked.append(b); break
        for b in sorted(merged):
            if b not in picked and b not in seen: picked.append(b)      # every possible battle of the stage is fought at least once
        seen.update(picked)
        out += [(b, f["bg"], f["map"], False) for b in picked]
    for f in stage["fights"]:
        if f["kind"] == "scripted" and not random_only:
            for b, c in f["battles"]: out.append((b, f["bg"], f["map"], True))
    return out

# ------------------------------------------------------------------ campaign
class Campaign:
    def __init__(self, a):
        self.a = a
        self.rom_path = a.rom
        self.d = GameData(open(a.rom, "rb").read())
        self.shops = Shops(self.d, a.shops)
        self.stages = build_stages(a.encounters, a.reached, a.areas, a.steps_per_map)
        self.jobs = dict(DEFAULT_JOBS)
        if a.jobs_spec: self.jobs.update(json.load(open(a.jobs_spec)))
        self.pool = Pool(a.jobs, initializer=_init, initargs=(a.rom, a.policy, a.boss_deaths))
        self.fight_log = []; self.log = []; self.t0 = time.time(); self.fights_run = 0; self.tents = 0
        os.makedirs(a.out, exist_ok=True)
        self.state = self.new_state()
        self.jobs_unlocked = False; self.faris_in = False; self.looted = set()
        liquid = [i for i, st in enumerate(self.stages) if any(f["kind"] == "scripted" and any(b == LIQUID_FLAME for b, _ in f["battles"]) for f in st["fights"])]
        self.liquid_idx = liquid[0] if liquid else None
        for i, st in enumerate(self.stages):         # the first Castle Karnak visit (before Liquid Flame) is peaceful (user, 2026-10-12): no fights
            if self.liquid_idx is not None and i < self.liquid_idx and st["name"] == "Castle Karnak": st["fights"] = []
        # Castle Karnak after Liquid Flame is on a 10 minute self-destruct timer; the user budgets 5 minutes of fighting (frames) in it
        self.timed = {i for i, st in enumerate(self.stages) if self.liquid_idx is not None and i > self.liquid_idx and st["name"] == "Castle Karnak"}
        self.after_liquid = None; self._frames = 0; self.rush = False

    # ---- party
    def new_state(self):
        f = Field.new(self.d, party=3)
        f.gil = 0
        for i in range(3):
            ch = f.character(i); ch.hp = ch.max_hp; ch.mp = ch.max_mp; f.set_character(i, ch)
        return f

    def say(self, msg):
        line = f"[{time.time() - self.t0:7.0f}s] {msg}"
        print(line, flush=True); self.log.append(line)
        with open(os.path.join(self.a.out, "campaign.log"), "a") as fh: fh.write(line + "\n")

    def avg_level(self):
        lv = [self.state.character(i).level for i in range(4) if self.state.character(i).in_party]
        return sum(lv) // max(1, len(lv))

    def add_faris(self):
        f = self.state; ch = f.character(3)
        lv = max(3, self.avg_level())
        ch.row &= ~0x40
        if lv > ch.level:
            ch.level = lv; ch.exp = max(ch.exp, self.d.level_exp(lv))
            ch.max_hp, ch.max_mp = self.d.level_hp_mp(lv, ch.base_vit, ch.base_mag)
        ch.hp = ch.max_hp; ch.mp = ch.max_mp
        f.set_character(3, ch); self.faris_in = True
        self.say(f"Faris joins at level {ch.level}")

    def unlock_jobs(self):
        f = self.state
        for i, n in enumerate(SLOT_NAMES):
            ch = f.character(i)
            if not ch.in_party: continue
            ch.set_job(self.d, self.jobs[n])
            ch.raw[0x3D:0x40] = self.d.enable_spells(ch.commands)
            # keep only gear the new job may wear
            wm, am = ch.equip_weapons, ch.equip_armor
            for s in ("rh_weapon", "lh_weapon", "rh_shield", "lh_shield", "head", "body", "accessory"):
                v = getattr(ch, s)
                if v and v != 0x80 and not self.d.item_equippable(v, wm, am):
                    setattr(ch, s, 0x80 if s in ("head", "body", "accessory") else 0)
            f.set_character(i, ch)
        self.jobs_unlocked = True
        self.say("jobs assigned: " + ", ".join(f"{n} {self.jobs[n]}" for n in SLOT_NAMES))

    def heal(self, why, price=None):
        if price is None:                                  # no inn here: a Tent from the bag is free, else one is bought
            tent = self.d.item_id(TENT); items = self.state.items()
            if items.get(tent, 0) > 0:
                items[tent] -= 1
                if items[tent] <= 0: del items[tent]
                self.state.set_items(items); price = 0
        price = self.shops.tent_price if price is None else price
        if self.state.gil < price: return False
        self.state.gil = self.state.gil - price
        for i in range(4):
            ch = self.state.character(i)
            if ch.in_party: ch.hp = ch.max_hp; ch.mp = ch.max_mp; ch.status1 = ch.status2 = ch.status3 = ch.status4 = 0; self.state.set_character(i, ch)       # full rest: revived, no ailments
        self.tents += 1
        return True

    def inn_price(self, st):
        """Cheapest inn of the town this stage is in (None = no inn: a Tent is used)."""
        if "inn_by_stage" not in self.__dict__:
            areas = json.load(open(self.a.areas)); inns = json.load(open(self.a.shops)).get("inns", {})
            def label(m):
                for k in range(m, max(m - 25, 10), -1):
                    t = areas.get(str(k))
                    if t and not any(x in t for x in SUBS): return t
            self.inn_by_stage = {}
            for m, p in inns.items():
                l = label(int(m)); self.inn_by_stage[l] = min(p, self.inn_by_stage.get(l, 10**9))
        return self.inn_by_stage.get(st["name"]) if st["kind"] == "local" else None

    def hp_fraction(self):
        fr = [c.hp / max(1, c.max_hp) for c in (self.state.character(i) for i in range(4)) if c.in_party]
        return min(fr)

    # ---- fights
    def batch(self, enc, bg, runs, mode="fight"):
        w = self.state.to_wram()
        seeds = [(k * 97 + (enc * 31)) % 256 for k in range(runs)]
        return self.pool.map(_run, [(w, enc, bg, s, mode) for s in seeds], chunksize=2)

    def adopt(self, runs):
        """The batch's average result becomes the party state: the run whose exp is closest to the mean, HP/MP the mean, gil start + mean."""
        mean_exp = sum(sum(r["exp"]) for r in runs) / len(runs)
        self._frames = round(sum(r["frames"] for r in runs) / len(runs))
        rep = min(runs, key=lambda r: abs(sum(r["exp"]) - mean_exp))
        f = Field.from_wram(rep["wram"], self.d)
        for i in range(4):
            ch = f.character(i)
            if not ch.in_party: continue
            hps = []; mps = []
            for r in runs:
                c = Character(r["wram"][FIELD_CHAR + CHAR_SIZE * i:FIELD_CHAR + CHAR_SIZE * (i + 1)])
                hps.append(c.hp); mps.append(c.mp)
            ch.hp = min(ch.max_hp, max(1, round(sum(hps) / len(hps)))); ch.mp = min(ch.max_mp, round(sum(mps) / len(mps)))
            f.set_character(i, ch)
        f.gil = self.state.gil + round(sum(r["gil"] for r in runs) / len(runs))
        self.state = f
        return round(mean_exp)

    def fight(self, enc, bg, label, runs, scripted=False):
        """One fight as a batch.  Returns (counted, summary).  Advances self.state to the batch's average result when it counts.
        A random fight that cannot be won flawlessly may be run away from (hold L+R) if that works 95% of the time without a death."""
        b = self.d.battle(enc)
        at_cap = (bool(self.a.max_level) and self.top_level() >= self.a.max_level) or self.rush
        if (self.a.flee_first or at_cap) and not scripted and not b["no_run"] and not b["boss"]:       # at the level cap more experience is worth nothing: run
            fl = self.batch(enc, bg, runs, "flee"); self.fights_run += 1          # least experience: run from every fight that can be run from safely
            ok = [r for r in fl if r["flawless"]]
            if len(ok) / len(fl) >= self.a.flawless:
                info = dict(battle=enc, name=self.d.battle_summary(enc).split(": ", 1)[-1], rate=0, flee_rate=round(len(ok) / len(fl), 3), map=str(label), fled=True)
                info["exp"] = self.adopt(ok); info["frames"] = self._frames; return True, info
        res = self.batch(enc, bg, runs)
        self.fights_run += 1
        flawless = [r for r in res if r["flawless"]]
        rate = len(flawless) / len(res)
        name = self.d.battle_summary(enc).split(": ", 1)[-1]
        info = dict(battle=enc, name=name, rate=round(rate, 3), map=str(label))
        if rate >= self.a.flawless:
            info["exp"] = self.adopt(flawless); info["frames"] = self._frames; return True, info
        b = self.d.battle(enc)
        if not scripted and not b["no_run"] and not b["boss"] and self.a.flee:
            fl = self.batch(enc, bg, runs, "flee"); self.fights_run += 1
            ok = [r for r in fl if r["flawless"]]
            info["flee_rate"] = round(len(ok) / len(fl), 3)
            if len(ok) / len(fl) >= self.a.flawless:
                info["exp"] = self.adopt(ok); info["fled"] = True; info["frames"] = self._frames; return True, info
        return False, info

    def top_level(self):
        return max(self.state.character(i).level for i in range(4) if self.state.character(i).in_party)

    def run_pass(self, stage, offset, who, random_only=False, timed=False):
        """One pass over a stage's fights.  Returns (ok, failed_fight_info).  `timed`: the stage is on the Castle Karnak self-destruct
        clock: fights are run away from (when that is safe) once the average pace would overrun CASTLE_FRAMES; a pass whose fights
        take more frames than that is a game over (info['timer'])."""
        fights = schedule(stage, offset, random_only)
        mid = len(fights) // 2; used = 0; done = 0
        for k, (enc, bg, label, scripted) in enumerate(fights):
            self.rush = False
            if timed and done:
                pace = used / done
                self.rush = used + pace * (len(fights) - k) > self.a.castle_frames          # at this pace the castle blows up: run from what can be run from
            if stage["kind"] == "world" and self.hp_fraction() < 0.6 and self.heal("world"): pass
            if stage["dungeon"] and k == mid and k > 0 and self.hp_fraction() < 0.8 and self.heal("save point"): pass
            if scripted and stage["kind"] == "local": self.heal("before boss")        # a player rests / saves before a boss: HP and MP full (one Tent)
            ok, info = self.fight(enc, bg, label, 2 * self.a.runs if scripted else self.a.runs, scripted)
            self.rush = False
            self.fight_log.append(dict(stage=stage["name"], **info, counted=ok))
            if ok and timed:
                used += info.get("frames", 0); done += 1
                if used > self.a.castle_frames:
                    self.say(f"   CASTLE TIMER: {used} frames of fighting > {self.a.castle_frames}: Castle Karnak explodes, back to just after Liquid Flame")
                    info = dict(info, timer=True, used=used); return False, info
            if not ok:
                open(os.path.join(self.a.out, f"fail_{enc}_{self.fights_run}.bin"), "wb").write(self.state.to_wram())
                return False, info
        return True, None

    def party_power(self):
        return [member_power(self.d, self.state.character(i)) for i in range(4) if self.state.character(i).in_party]

    def shop_maps(self, upto):
        maps = set()
        for st in self.stages[:upto + 1]:
            maps |= set(st["maps"])
        # town maps hold their shops: use every map whose region heading belongs to a visited stage name
        return maps

    def go_shopping(self, idx):
        areas = json.load(open(self.a.areas))
        visited = {st["name"] for st in self.stages[:idx + 1] if st["kind"] == "local"}
        def label(m):
            for k in range(m, max(m - 25, 10), -1):
                t = areas.get(str(k))
                if t and not any(s in t for s in SUBS): return t
            return None
        maps = {m for m in self.shops.maps if label(m) in visited}
        if self.a.extra_towns:       # towns the route passes without a random fight: story map ids rise along the route
            top = max(m for st in self.stages[:idx + 1] for m in st["maps"]) if any(st["maps"] for st in self.stages[:idx + 1]) else 0
            maps |= {m for m in self.shops.maps if m <= top}
        stock = self.shops.stock(maps)
        reserve = 3 * self.shops.tent_price          # keep three Tents' worth for healing (a broke party cannot rest and fails every later fight)
        log = []
        equip_owned(self.state, self.d, log)
        shop(self.state, stock, self.shops, self.d, reserve, self.jobs if self.jobs_unlocked else {}, log, self.element_weights(idx))
        self.stock_potions(stock, reserve, log)
        for l in log: self.say("  bought: " + l)

    def collect_loot(self, st):
        """Open every plain chest on the stage's maps (once): gil, items and spells go straight into the party state."""
        if not self.a.loot: return
        got = []; items = self.state.items()
        for m in st["maps"]:
            for k, kind, v in chests(self.d, m):
                if k in self.looted: continue
                self.looted.add(k)
                if kind == "gil": self.state.gil = self.state.gil + v; got.append(f"{v} gil")
                elif kind == "spell":
                    self.state.wram[MAGIC_BITS + (v >> 3)] |= 0x80 >> (v & 7); got.append(f"spell {self.d.spell_name(v)}")
                else: items[v] = min(99, items.get(v, 0) + 1); got.append(self.d.item_name(v))
        if got:
            self.state.set_items(items); self.say("  loot: " + ", ".join(got))
        log = []; equip_owned(self.state, self.d, log)
        for l in log: self.say("  equipped: " + l)

    def element_weights(self, idx):
        """{element bit: bonus in -0.5..+1.5} from the monsters of this stage and the next three (bosses count six-fold: the spells are bought for them): what they are weak to / absorb."""
        score = {1: 0.0, 2: 0.0, 4: 0.0}; tot = 0.0
        for st in self.stages[idx:idx + 4]:
            for enc, _bg, _l, scripted in schedule(st):
                b = self.d.battle(enc); w = 6.0 if (scripted or b["boss"]) else 1.0
                for mid in b["monster_ids"]:
                    if mid is None: continue
                    m = self.d.monster(mid); tot += w
                    for bit in score:
                        if m["weak"] & bit: score[bit] += w
                        if (m["absorb"] | m["immune"]) & bit: score[bit] -= w
        return {bit: max(-0.5, min(1.5, 2.0 * v / tot)) for bit, v in score.items()} if tot else None

    def stock_potions(self, stock, reserve, log):
        """Spare gil (above two Tents) goes to Potions, up to 20, from an item shop of a visited region."""
        pot = self.d.item_id("Potion")
        if pot not in stock["item"]: return
        have = dict(zip(self.state.wram[FIELD_ITEMS:FIELD_ITEMS + 256], self.state.wram[FIELD_QTY:FIELD_QTY + 256])).get(pot, 0)
        n = min(20 - have, max(0, (self.state.gil - 2 * reserve) // stock["item"][pot]))
        if n <= 0: return
        items = {k: v for k, v in zip(self.state.wram[FIELD_ITEMS:FIELD_ITEMS + 256], self.state.wram[FIELD_QTY:FIELD_QTY + 256]) if k}
        items[pot] = have + n
        self.state.set_items(items); self.state.gil = self.state.gil - n * stock["item"][pot]
        log.append(f"{n} Potion(s) ({n * stock['item'][pot]} gil)")

    def summary_line(self):
        ps = []
        for i in range(4):
            c = self.state.character(i)
            if c.in_party: ps.append(f"{SLOT_NAMES[i]} lv{c.level} {self.d.job_name(c.job)}")
        return ", ".join(ps) + f"; gil {self.state.gil}"

    def save(self, idx, tag=""):
        p = os.path.join(self.a.out, "state.bin")
        open(p, "wb").write(self.state.to_wram())
        json.dump(dict(stage=idx, jobs_unlocked=self.jobs_unlocked, faris_in=self.faris_in, tents=self.tents, looted=sorted(self.looted)), open(os.path.join(self.a.out, "state.json"), "w"))

    def run(self):
        self.tents = 0; report = []
        idx = 0; passes_at = {}
        sj = os.path.join(self.a.out, "state.json")
        if self.a.resume and os.path.exists(sj):
            j = json.load(open(sj)); idx = j["stage"] + 1; self.jobs_unlocked = j["jobs_unlocked"]; self.faris_in = j["faris_in"]; self.tents = j["tents"]; self.looted = set(j.get("looted", []))
            self.state = Field.from_wram(open(os.path.join(self.a.out, "state.bin"), "rb").read(), self.d)
            al = os.path.join(self.a.out, "after_liquid.bin")
            if os.path.exists(al): self.after_liquid = open(al, "rb").read()
            self.say(f"resumed after stage {idx - 1} | {self.summary_line()}")
        while idx < len(self.stages) and (self.a.until is None or idx <= self.a.until):
            st = self.stages[idx]; self.cur_idx = idx
            label = st["name"] if st["kind"] == "local" else "world map " + ",".join(f"{w}:{bx},{by}" for (w, bx, by) in st["world"])
            self.say(f"== stage {idx}: {label} (steps {st['steps'][0]}-{st['steps'][-1]}), {len(schedule(st))} fights | {self.summary_line()}")
            self.collect_loot(st)
            self.go_shopping(idx)
            before = self.party_power()
            fail_state = None
            attempts = 0
            while True:
                snap = (self.state.to_wram())
                ok, info = self.run_pass(st, attempts, label, timed=idx in self.timed)
                if self.a.max_level and self.top_level() > self.a.max_level:
                    self.say(f"   LEVEL CAP {self.a.max_level} exceeded (level {self.top_level()}) in {label}: challenge failed")
                    report.append(dict(stage=idx, name=label, status="level cap exceeded")); return self.finish(report, stuck=idx)
                if ok: break
                self.say(f"   fight did not count: {info['name']} at {info['map']}: flawless {info['rate']:.0%} < {self.a.flawless:.0%}")
                attempts += 1
                if info.get("timer") and self.after_liquid: snap = self.after_liquid       # game over: the party is as it was right after Liquid Flame
                self.state = Field.from_wram(snap, self.d)       # the failed fight does not count; back to the start of the pass
                if attempts > self.a.max_attempts:
                    self.say("   STUCK: giving up at this stage"); report.append(dict(stage=idx, name=label, status="stuck", info=info))
                    return self.finish(report, stuck=idx)
                # simulate the previous region until every member got a power boost
                before = self.party_power()
                gi = max(0, idx - 1)
                def usable(g):       # Castle Karnak blows up after Iron Claw (user, 2026-10-12): its fights can never be simulated again
                    return not (self.timed and idx >= min(self.timed) and self.stages[g]["name"] == "Castle Karnak")
                while gi > 0 and (n_random(self.stages[gi]) < 3 or not usable(gi)): gi -= 1       # grind the nearest earlier stage with a few random fights (a boss stage would just re-fight the boss)
                if n_random(self.stages[gi]) < 3 or not usable(gi):
                    gi = max(0, idx - 1)
                    while gi > 0 and (not schedule(self.stages[gi]) or not usable(gi)): gi -= 1
                self.grind(gi, before)
                if self.a.max_level and self.top_level() > self.a.max_level:
                    self.say(f"   LEVEL CAP {self.a.max_level} exceeded (level {self.top_level()}) while grinding for {label}: challenge failed")
                    report.append(dict(stage=idx, name=label, status="level cap exceeded")); return self.finish(report, stuck=idx)
                self.go_shopping(idx)
                self.save(idx - 1)                            # checkpoint: a restart resumes this stage with the boosted party
            self.heal("end of region", self.inn_price(st))
            self.go_shopping(idx)
            if st["name"] == "West Valley" and not self.faris_in: self.add_faris()
            if st["name"] == "Wind Crystal" and not self.jobs_unlocked: self.unlock_jobs(); self.go_shopping(idx)
            if idx == self.liquid_idx:
                self.after_liquid = self.state.to_wram(); open(os.path.join(self.a.out, "after_liquid.bin"), "wb").write(self.after_liquid)
            self.say(f"   region done after {attempts} failed attempt(s) | {self.summary_line()}")
            report.append(dict(stage=idx, name=label, status="done", attempts=attempts, party=self.summary_line()))
            self.save(idx)
            idx += 1
        return self.finish(report)

    def grind(self, gi, before):
        """Simulate stage gi again (fights count only when they pass the rule) until every member's power is above `before`."""
        st = self.stages[gi]; n = 0
        while True:
            n += 1
            self.heal("grind", self.inn_price(st))
            ok, info = self.run_pass(st, 1000 + n, st["name"], random_only=n_random(st) >= 3)
            boosted = all(a > b for a, b in zip(self.party_power(), before)) if len(self.party_power()) == len(before) else True
            self.say(f"   grind pass {n} in {st['name']}: {'ok' if ok else 'a fight failed: ' + info['name']} | {self.summary_line()} | boost {boosted}")
            if boosted: return
            if self.a.max_level and self.top_level() > self.a.max_level: return
            self.save(self.cur_idx - 1)                        # keep the progress of long grinds across restarts
            if n >= self.a.max_grind_passes:
                self.say("   grind limit reached"); return
            self.go_shopping(gi)

    def finish(self, report, stuck=None):
        out = os.path.join(self.a.out, "report.json")
        json.dump(dict(fights=self.fight_log, report=report, final=self.summary_line(), tents_used=self.tents, fights_simulated=self.fights_run, stuck=stuck), open(out, "w"), indent=1)
        self.say(f"finished: {self.summary_line()} | tents {self.tents} | batches {self.fights_run}")
        return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=os.environ.get("FFV_ROM"), help="ROM path (or env FFV_ROM)")
    ap.add_argument("--out", required=True, help="output directory (log, state, report)")
    ap.add_argument("--jobs", type=int, default=None, help="worker processes")
    ap.add_argument("--runs", type=int, default=64, help="battles per fight batch")
    ap.add_argument("--flawless", type=float, default=0.95)
    ap.add_argument("--steps-per-map", type=int, default=80, help="steps walked on each local map (for the number of random encounters)")
    ap.add_argument("--castle-frames", type=int, default=CASTLE_FRAMES, help="frame budget for the fights of Castle Karnak after Liquid Flame")
    ap.add_argument("--max-attempts", type=int, default=12); ap.add_argument("--max-grind-passes", type=int, default=40)
    ap.add_argument("--encounters", default=os.path.join(ROOT, "data", "encounters_by_region.json"))
    ap.add_argument("--reached", default=os.path.join(ROOT, "data", "reached_regions.json"))
    ap.add_argument("--areas", default=os.path.join(ROOT, "data", "map_areas.json"))
    ap.add_argument("--shops", default=os.path.join(ROOT, "data", "shops_by_map.json"))
    ap.add_argument("--no-flee", dest="flee", action="store_false", help="never run away from a random fight that cannot be won flawlessly")
    ap.add_argument("--flee-first", action="store_true", help="run away from every runnable random fight that can be escaped flawlessly (95%%); fight the rest")
    ap.add_argument("--until", type=int, default=None, help="stop after finishing this stage index (--list-stages shows them)")
    ap.add_argument("--max-level", type=int, default=None, help="challenge: no member may pass this level; at the cap runnable random fights are fled")
    ap.add_argument("--boss-deaths", action="store_true", help="boss / no-run fights count as won when the party wins, even with deaths (random fights stay flawless)")
    ap.add_argument("--loot", action="store_true", help="open every plain chest on the maps of the regions reached; worn gear is upgraded from the bag")
    ap.add_argument("--policy", choices=("tactical", "smart", "veteran"), default="veteran", help="battle policy (policy.py)")
    ap.add_argument("--extra-towns", action="store_true", help="shops of towns whose map ids lie below the furthest map reached are usable too (Carwen etc.)")
    ap.add_argument("--resume", action="store_true", help="continue from state.bin/state.json in --out (saved after every finished stage)")
    ap.add_argument("--list-stages", action="store_true", help="print the stages and their fights, then exit")
    ap.add_argument("--jobs-spec", help='JSON like {"Bartz": "Knight", "Lenna": "WhiteMage", "Galuf": "Monk", "Faris": "BlackMage"}')
    a = ap.parse_args()
    if not a.rom: ap.error("--rom or env FFV_ROM required")
    if a.list_stages:
        d = GameData(open(a.rom, "rb").read())
        for i, st in enumerate(build_stages(a.encounters, a.reached, a.areas, a.steps_per_map)):
            print(i, st["name"], st["kind"], f"steps {st['steps'][0]}-{st['steps'][-1]}", "maps", st["maps"], "world", list(st["world"]))
            for b, bg, lab, sc in schedule(st): print("    ", b, bg, lab, d.battle_summary(b).split(": ", 1)[-1])
        return
    Campaign(a).run()

if __name__ == "__main__": main()
