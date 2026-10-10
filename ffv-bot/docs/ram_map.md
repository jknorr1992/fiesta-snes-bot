# Verified RAM facts (WRAM offsets, snes9x core, READ_CORE_RAM offset 0 = $7E0000)

| Address | Meaning |
|---|---|
| $0018 | In-battle flag (1 = battle) |
| $0500 + 0x50*slot | Party slot struct: +0 flags, +1 job, +2 level, +3 exp (u24), +6 HP, +8 max HP, +A MP, +C max MP |
| $0640 / $0740 | Inventory: 256 slots of item id / quantity. Phoenix Down = 228 (seen after opening a chest); new game has id 224 x3, probably Potion |
| $0947 | Gil (u24 LE), 500 at new game |
| $0AD8 / $0AD9 | Map X / Y tile position |
| $0B4F | Map id (observed: 1 world map, 26 meteor forest, 27 crater) |
| $10000 | 64x64 local-map tile id grid (row-major); passability learned at runtime. Tile id 2 = treasure chest |

Found by WRAM snapshot/diff against checkpoint save states in the test bed (see retroarch-testbed/README.md).
Unknown so far: dialog flag, menu/battle-cursor state, story flags, treasure-opened flags.
