class_name FFVRam
extends RefCounted
## Verified RAM facts for the patched FFV (offsets are into WRAM; offset 0 = $7E0000, as READ_CORE_RAM expects).
## See docs/ram_map.md for how each was found and how sure we are.

const IN_BATTLE := 0x0018        ## 1 while a battle is running, else 0 (also 0 in menus and name entry)
const PARTY := 0x0500            ## four 0x50-byte character slots
const PARTY_STRIDE := 0x50
const PARTY_SLOTS := 4
const GIL := 0x0947              ## 3 bytes little-endian
const MAP_X := 0x0AD8            ## tile x on the current map (world map or local map)
const MAP_Y := 0x0AD9
const INV_IDS := 0x0640   # 256 slots: item id (0 = empty)
const INV_QTY := 0x0740   # 256 slots: quantity
const IN_LOCAL_MAP := 0x0B53  # 0 on the world map, 1 inside a local map (7 states checked)
const MOUNT := 0x0ADA  # 7 while riding the chocobo (observed), 2 on foot after the Boco event
const WORLD_BAND := 0x10000  # world map only: 256x64 tile ids, world row y is band row (y & 63)
const MAP_ID := 0x0B4F  # local map id: 26 = Tycoon meteor forest, 27 = crater (observed)
const EVENT_FLAGS := 0x0A14   # 96 bytes of story/event bits (wiki: $0A14-$0AD3), bit n = byte n>>3, bit n&7
const EVENT_FLAG_BYTES := 96
const TREASURE_FLAGS := 0x09D4   # 32 bytes, one bit per opened chest
const DIALOG_OPEN := 0x0B57   # wiki: message dialog opened flag (unverified)
const TILE_MAP := 0x10000        ## 64x64 tile ids of the current local map, row-major ($7F0000)
const TILE_MAP_SIZE := 64

## Character slot field offsets
const C_FLAGS := 0x00            ## low bits: character id; 0x40 bit set while in the party
const C_JOB := 0x01              ## index into jobs.json (0x15 = "Normal"/Freelancer)
const C_LEVEL := 0x02
const C_EXP := 0x03              ## 3 bytes little-endian
const C_HP := 0x06               ## u16
const C_MAX_HP := 0x08           ## u16
const C_MP := 0x0A               ## u16
const C_MAX_MP := 0x0C           ## u16


static func u16(b: PackedByteArray, i: int) -> int:
	return b[i] | (b[i + 1] << 8)


static func u24(b: PackedByteArray, i: int) -> int:
	return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16)
