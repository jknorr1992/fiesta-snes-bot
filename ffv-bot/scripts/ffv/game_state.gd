class_name FFVGameState
extends RefCounted
## Snapshot of the game as read from RAM (read-only).

var in_battle := false
var gil := 0
var map_x := 0
var map_y := 0
var map_id := 0
var party: Array[Dictionary] = []  ## {slot, flags, job, level, exp, hp, max_hp, mp, max_mp}

static var _jobs: PackedStringArray = PackedStringArray()


static func job_name(id: int) -> String:
	if _jobs.is_empty():
		var f := FileAccess.open("res://data/jobs.json", FileAccess.READ)
		if f:
			var arr = JSON.parse_string(f.get_as_text())
			if arr is Array:
				_jobs = PackedStringArray(arr)
	return _jobs[id] if id >= 0 and id < _jobs.size() else "Job%d" % id


## Read everything the decision code needs with three small requests.
static func read(ra: RetroArchClient) -> FFVGameState:
	var s := FFVGameState.new()
	var low := await ra.read_wram(0x0000, 0x20)
	var party_bytes := await ra.read_wram(FFVRam.PARTY, FFVRam.PARTY_STRIDE * FFVRam.PARTY_SLOTS)
	var misc := await ra.read_wram(0x0900, 0x300)
	if low.size() != 0x20 or party_bytes.size() != FFVRam.PARTY_STRIDE * FFVRam.PARTY_SLOTS or misc.size() != 0x300:
		return null
	s.in_battle = low[FFVRam.IN_BATTLE] == 1
	s.gil = FFVRam.u24(misc, FFVRam.GIL - 0x0900)
	s.map_x = misc[FFVRam.MAP_X - 0x0900]
	s.map_y = misc[FFVRam.MAP_Y - 0x0900]
	s.map_id = misc[FFVRam.MAP_ID - 0x0900]
	for i in FFVRam.PARTY_SLOTS:
		var o := i * FFVRam.PARTY_STRIDE
		s.party.append({
			"slot": i,
			"flags": party_bytes[o + FFVRam.C_FLAGS],
			"job": party_bytes[o + FFVRam.C_JOB],
			"level": party_bytes[o + FFVRam.C_LEVEL],
			"exp": FFVRam.u24(party_bytes, o + FFVRam.C_EXP),
			"hp": FFVRam.u16(party_bytes, o + FFVRam.C_HP),
			"max_hp": FFVRam.u16(party_bytes, o + FFVRam.C_MAX_HP),
			"mp": FFVRam.u16(party_bytes, o + FFVRam.C_MP),
			"max_mp": FFVRam.u16(party_bytes, o + FFVRam.C_MAX_MP),
		})
	return s


func describe() -> String:
	var lines: Array[String] = ["%s  gil=%d  pos=(%d,%d) map=%d" % ["BATTLE" if in_battle else "field", gil, map_x, map_y, map_id]]
	for c in party:
		if c.max_hp == 0:
			continue
		lines.append("  slot%d %s Lv%d HP %d/%d MP %d/%d exp %d" % [c.slot, job_name(c.job), c.level, c.hp, c.max_hp, c.mp, c.max_mp, c.exp])
	return "\n".join(lines)
