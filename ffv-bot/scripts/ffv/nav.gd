class_name FFVNav
extends RefCounted
## Walks the party across the current local map using the tile grid in RAM and a learned
## passability table (tile id -> walkable). Inputs go through VirtualPad only.
##
## Passability is learned by trying: a move that changes the position marks the target tile id walkable,
## a move that doesn't (after pressing A to clear a possible dialog) marks it blocked.
## Seeded with ids seen in the Tycoon meteor forest; unknown ids are allowed at a higher path cost.

const DIRS := {"RIGHT": Vector2i(1, 0), "LEFT": Vector2i(-1, 0), "UP": Vector2i(0, -1), "DOWN": Vector2i(0, 1)}

var ra: RetroArchClient
var pad: VirtualPad
var known := {12: true, 37: true, 39: true, 69: true, 70: true, 98: true, 108: true,
		38: false, 47: false, 53: false, 54: false, 55: false, 206: false, 207: false, 208: false}
var step_frames := 8
var tree: SceneTree


func _init(p_ra: RetroArchClient, p_pad: VirtualPad, p_tree: SceneTree) -> void:
	ra = p_ra
	pad = p_pad
	tree = p_tree


func position() -> Vector2i:
	var b := await ra.read_wram(FFVRam.MAP_X, 2)
	return Vector2i(b[0], b[1]) if b.size() == 2 else Vector2i(-1, -1)


func read_tiles() -> PackedByteArray:
	return await ra.read_wram(FFVRam.TILE_MAP, FFVRam.TILE_MAP_SIZE * FFVRam.TILE_MAP_SIZE)


func _tile(tiles: PackedByteArray, p: Vector2i) -> int:
	return tiles[p.y * FFVRam.TILE_MAP_SIZE + p.x]


func _cost(tiles: PackedByteArray, p: Vector2i) -> int:
	if p.x < 0 or p.y < 0 or p.x >= FFVRam.TILE_MAP_SIZE or p.y >= FFVRam.TILE_MAP_SIZE:
		return -1
	var k = known.get(_tile(tiles, p), null)
	if k == false:
		return -1
	return 1 if k == true else 4


## A* over the tile grid. Returns an array of direction names, or empty if none.
func plan(tiles: PackedByteArray, start: Vector2i, goal: Vector2i) -> Array[String]:
	var open := [[0, start]]
	var best := {start: 0}
	var came := {}
	while not open.is_empty():
		open.sort_custom(func(a, b): return a[0] < b[0])
		var cur: Vector2i = open.pop_front()[1]
		if cur == goal:
			var path: Array[String] = []
			while came.has(cur):
				path.push_front(came[cur][1])
				cur = came[cur][0]
			return path
		for d in DIRS:
			var n: Vector2i = cur + DIRS[d]
			var c := _cost(tiles, n)
			if c < 0:
				continue
			var g: int = best[cur] + c
			if g < best.get(n, 1 << 30):
				best[n] = g
				came[n] = [cur, d]
				open.append([g + absi(n.x - goal.x) + absi(n.y - goal.y), n])
	return []


func _press(dir: String, frames: int) -> void:
	await pad.tap(dir, frames)
	await tree.create_timer(0.2).timeout


## Walk to `goal`. Returns "ok", "no_path", "stuck", "map_changed" or "battle".
func goto(goal: Vector2i, max_steps := 200) -> String:
	var tiles := await read_tiles()
	for i in max_steps:
		var cur := await position()
		if cur == goal:
			return "ok"
		if (await ra.read_u8(FFVRam.IN_BATTLE)) == 1:
			return "battle"
		if i % 6 == 0:
			tiles = await read_tiles()
		var path := plan(tiles, cur, goal)
		if path.is_empty():
			tiles = await read_tiles()  # the map may have changed under us
			path = plan(tiles, cur, goal)
		if path.is_empty():
			return "no_path"
		var d := path[0]
		var target: Vector2i = cur + DIRS[d]
		var tid := _tile(tiles, target)
		await _press(d, step_frames)
		var now := await position()
		if now == cur:
			await _press("A", 4)  # a dialog may be eating the input
			await tree.create_timer(0.6).timeout
			await _press(d, step_frames)
			now = await position()
		if now == cur:
			known[tid] = false
		else:
			if not known.has(tid):
				known[tid] = true
			if absi(now.x - cur.x) + absi(now.y - cur.y) > 2:
				return "map_changed"
	return "stuck"
