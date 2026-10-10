class_name FFVRomWorld
extends RefCounted
## Static world map read from the ROM file (data extraction only, no writes anywhere).
## World row y of world w: pointer = u16 at 0xFE000 + 512*w + 2*y, data at 0x70000 + pointer.
## Compression: byte > 0xBF repeats the next byte (n - 0xBF) times; 0x0C/0x1C/0x2C expand to three tiles.

const PTR_TABLE := 0xFE000
const DATA_BASE := 0x70000
const PROP_TABLE := 0xFEA00  # 3 bytes per tile id, 192 ids per world

var rom := PackedByteArray()
var _rows := {}  # world -> PackedByteArray (256*256), filled lazily


func load_rom(path: String) -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return false
	rom = f.get_buffer(f.get_length())
	return rom.size() > 0x100000


func world(w: int) -> PackedByteArray:
	if _rows.has(w):
		return _rows[w]
	var out := PackedByteArray()
	if rom.is_empty():
		return out
	for y in 256:
		out.append_array(_row(w, y))
	_rows[w] = out
	return out


func _row(w: int, y: int) -> PackedByteArray:
	var p := PTR_TABLE + 512 * w + 2 * y
	var off := DATA_BASE + (rom[p] | (rom[p + 1] << 8))
	var out := PackedByteArray()
	while out.size() < 256 and off < rom.size():
		var b := rom[off]
		off += 1
		if b > 0xBF:
			var v := rom[off]
			off += 1
			for i in b - 0xBF:
				out.append(v)
		elif b == 0x0C or b == 0x1C or b == 0x2C:
			out.append(b)
			out.append(b + 1)
			out.append(b + 2)
		else:
			out.append(b)
	out.resize(256)
	return out


## On-foot passability from the tile property table: byte 0 low nibble = enter-from-each-direction bits
## (0x4F plains/grass-like, 0x44 mountain/forest, 0x7C ocean; checked against bump-learned results).
func walkable_ids(w: int) -> Dictionary:
	var out := {}
	if rom.is_empty():
		return out
	for t in 192:
		var o := PROP_TABLE + 3 * (192 * w + t)
		out[t] = (rom[o] & 0x0F) == 0x0F
	return out
