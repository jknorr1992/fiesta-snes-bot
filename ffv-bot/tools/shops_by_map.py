#!/usr/bin/env python3
"""Which shops (event command $A1) each map offers, with their stock and prices, from the ROM.

usage: shops_by_map.py [--rom PATH] [--out data/shops_by_map.json]
ROM: --rom PATH or env FFV_ROM.  Shop table: ShopProp (D1:2D40, 9 bytes: type + 8 items), prices: ItemPriceTbl (D1:2A00; price = byte1 * 10^(byte0 & 31))
and MagicPriceTbl for magic shops.  Shop types: 0 magic, 1 weapons, 2 armor, 3 items, 4/5 special."""
import sys, os, json, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "battle")); sys.path.insert(0, os.path.join(ROOT, "sim"))
from data import GameData

DEFAULT_ROM = os.environ.get("FFV_ROM", "/mnt/project-files/FFV/Final Fantasy 5 (Japan) (patched)(2).sfc")
SHOP_PROP = 0x112D40; ITEM_PRICE = 0x112A00; MAGIC_PRICE = 0x112C00; SHOP_NAMES = 0x112D00

def price(rom, base, i): return rom[base + 2 * i + 1] * 10 ** (rom[base + 2 * i] & 31)

def shop_table(rom):
    """{shop index: {type, stock:[item/spell ids]}}"""
    out = {}
    for s in range(0x100):
        rec = rom[SHOP_PROP + 9 * s:SHOP_PROP + 9 * s + 9]
        if rec[0] > 5 and s > 60: break
        out[s] = dict(type=rec[0], stock=[b for b in rec[1:] if b])
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", default=DEFAULT_ROM); ap.add_argument("--out", default=os.path.join(ROOT, "data", "shops_by_map.json"))
    a = ap.parse_args()
    rom = open(a.rom, "rb").read(); d = GameData(rom)
    from rom import Rom
    from events import EventRom
    R = Rom(a.rom); E = EventRom(R.r)
    def shops_in(ev, seen):
        if ev in seen or ev >= E.N: return []
        seen.add(ev); out = []
        for c in E.cmds(ev):
            if c.op == 0xA1: out.append(c.b[1])
            elif c.op == 0xCD: out += shops_in(c.b[1] | c.b[2] << 8, seen)
        return out
    table = shop_table(rom); res = {}
    for m in range(512):
        found = set()
        try:
            for x, y, ts in R.event_triggers(m):
                for conds, ev in R.trigger_script(ts): found |= set(shops_in(ev, set()))
            for n in R.npcs(m):
                for conds, ev in R.npc_script(n["script"]): found |= set(shops_in(ev, set()))
        except Exception:
            continue
        if found: res[m] = sorted(found)
    shops = {}
    for s in sorted({s for v in res.values() for s in v}):
        t = table.get(s)
        if not t: continue
        if t["type"] == 0: stock = [dict(id=i, name=d.spell_name(i), price=price(rom, MAGIC_PRICE, i)) for i in t["stock"]]
        else: stock = [dict(id=i, name=d.item_name(i), price=price(rom, ITEM_PRICE, i)) for i in t["stock"]]
        shops[s] = dict(type=t["type"], stock=stock)
    json.dump(dict(maps={str(k): v for k, v in res.items()}, shops={str(k): v for k, v in shops.items()}), open(a.out, "w"), indent=1)
    print(len(res), "maps with shops,", len(shops), "shops ->", a.out)

if __name__ == "__main__": main()
