#!/usr/bin/env python3
"""Extract game data tables from the patched FFV ROM into ffv-bot/data/*.json.

Usage: extract_rom_data.py <rom.sfc> <outdir>
Only reads the ROM. Text encoding (verified by search): A-Z = 0x60.., a-z = 0x7A.., space = 0xFF, '-' = 0xC5;
a few ligature glyphs (e.g. 0xA4, 0x95, 0xA6/0xA7) are kept as '?' for now.
"""
import json, os, sys

def ch(b):
    if 0x60 <= b <= 0x79: return chr(ord('A') + b - 0x60)
    if 0x7A <= b <= 0x93: return chr(ord('a') + b - 0x7A)
    if b == 0xFF: return ' '
    if b == 0xC5: return '-'
    return '?'

def text(bs): return ''.join(ch(b) for b in bs).strip()

JOB_TABLE, JOB_COUNT, JOB_LEN = 0x115600, 22, 8

def main(rom_path, out):
    rom = open(rom_path, 'rb').read()
    os.makedirs(out, exist_ok=True)
    jobs = [text(rom[JOB_TABLE + JOB_LEN * i: JOB_TABLE + JOB_LEN * (i + 1)]) for i in range(JOB_COUNT)]
    # Ligature glyph fixes for the job names (seen in the menu font)
    fix = {'Mys?cKnt': 'MysticKnt', 'Wh?eMage': 'WhiteMage', '??ckMage': 'BlackMage', 'Berserkr': 'Berserker',
           'Geomancr': 'Geomancer', 'Normal': 'Freelancer'}
    jobs = [fix.get(j, j) for j in jobs]
    json.dump(jobs, open(os.path.join(out, 'jobs.json'), 'w'), indent=1)
    print('jobs:', jobs)

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
