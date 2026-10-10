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


## Position after a step: on-foot walking takes longer than the tap, so wait until it changes and then stops.
func _settle(from: Vector2i) -> Vector2i:
	var p := await position()
	for k in 6:
		if p != from:
			break
		await tree.create_timer(0.15).timeout
		p = await position()
	if p != from:
		for k in 4:
			await tree.create_timer(0.12).timeout
			var q := await position()
			if q == p:
				break
			p = q
	return p


## True while a message window is open (WRAM $0B57, verified: 1 during the Boco dialog, 0 after).
func dialog_open() -> bool:
	return (await ra.read_u8(FFVRam.DIALOG_OPEN)) == 1


## Press A until the dialog closes (or give up). Returns how many presses it took.
func clear_dialog(max_presses := 30) -> int:
	var n := 0
	while n < max_presses and await dialog_open():
		await _press("A", 4)
		await tree.create_timer(0.4).timeout
		n += 1
	return n


## Talk to whatever stands next to us in `dir`: turn towards it (the move is blocked), then press A.
## Returns true if a dialog opened.
func interact(dir: String) -> bool:
	await pad.tap(dir, 8)
	await tree.create_timer(0.5).timeout
	await pad.tap("A", 8)
	await tree.create_timer(0.8).timeout
	return await dialog_open()


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
		if await dialog_open():
			await clear_dialog()
			continue
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
		var now := await _settle(cur)
		if now == cur and await dialog_open():
			await clear_dialog()
			continue  # an event interrupted the step; not evidence that the tile is blocked
		if now == cur:
			await _press("A", 4)  # a dialog may be eating the input
			await tree.create_timer(0.6).timeout
			await _press(d, step_frames)
			now = await _settle(cur)
		if now == cur:
			local_bumps[tid] = int(local_bumps.get(tid, 0)) + 1
			if known.get(tid, null) != true and int(local_bumps[tid]) >= 2:
				known[tid] = false  # two failed attempts and never walked onto: treat as blocked
		else:
			known[tid] = true  # walking onto a tile once proves it passable (ledges aside)
			if absi(now.x - cur.x) + absi(now.y - cur.y) > 2:
				return "map_changed"
	return "stuck"


## World map. The game keeps a rolling 256x64 band of world tile ids at WRAM $10000; world row y lives in
## band row (y & 63) and is only valid near the player. Passability is learned by bumping, keyed by tile id.
var world_known := {135: false, 173: true, 189: true}  # ocean blocks Boko; 173/189 are the crater column's walkable tiles (event overlay, not in ROM)
var world_entrances := {}       # tile ids that open a town/dungeon/cave
var last_tid := -1
var _band_center := 0
var rom_walk := {}  ## tile id -> bool from FFVRomWorld.walkable_ids; a prior that bump-learning overrides
var rom_map := PackedByteArray()  ## optional 256x256 static world map from FFVRomWorld; plans beyond the RAM band
var bumps := {}  # tile id -> failed attempts


func read_world_band() -> PackedByteArray:
	var out := PackedByteArray()
	for part in 4:
		var b := await ra.read_wram(FFVRam.WORLD_BAND + part * 4096, 4096)
		if b.size() != 4096:
			return PackedByteArray()
		out.append_array(b)
	return out


func _wtile(band: PackedByteArray, p: Vector2i) -> int:
	if not rom_map.is_empty() and absi(p.y - _band_center) > 28:
		return rom_map[p.y * 256 + p.x]  # outside the live band: static map (event changes such as the crater are missing)
	return band[(p.y & 63) * 256 + p.x]


func _wcost(band: PackedByteArray, around: Vector2i, p: Vector2i) -> int:
	if p.x < 0 or p.y < 0 or p.x > 255 or p.y > 255:
		return -1
	if rom_map.is_empty() and absi(p.y - around.y) > 28:
		return 2  # outside the loaded band: unknown terrain
	var t := _wtile(band, p)
	if p == world_goal:
		return 1  # the goal itself is always enterable (entrance tiles are low-nibble-D in the ROM, so rom_walk calls them blocked)
	var k = world_known.get(t, rom_walk.get(t, null))
	if k == false or world_entrances.has(t):
		return -1
	return 1 if k == true else 3


var world_goal := Vector2i(-1, -1)


## One world step; waits until the position stops changing (the chocobo glides a little after a tap).
func _step_world(d: String) -> Vector2i:
	await _press(d, 12)
	var a := await position()
	for k in 5:
		await tree.create_timer(0.25).timeout
		var b := await position()
		if b == a:
			break
		a = b
	return a


func goto_world(goal: Vector2i, max_steps := 400) -> String:
	world_goal = goal
	for i in max_steps:
		var cur := await position()
		if cur == goal:
			return "ok"
		if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 1:
			if last_tid >= 0:
				world_entrances[last_tid] = true  # a tile that leads inside; avoid it unless it is the goal
			return "entered_local_map"
		if (await ra.read_u8(FFVRam.IN_BATTLE)) == 1:
			return "battle"
		_band_center = cur.y
		var band := await read_world_band()
		if band.is_empty():
			return "read_failed"
		var path := _plan_world(band, cur, goal)
		if path.is_empty():
			return "no_path"
		var d := path[0]
		var target: Vector2i = cur + DIRS[d]
		var tid := _wtile(band, target) if (not rom_map.is_empty() or absi(target.y - cur.y) <= 28) else -1
		last_tid = tid
		var now := await _step_world(d)
		if tid >= 0:
			if now == cur:
				bumps[tid] = int(bumps.get(tid, 0)) + 1
				if int(bumps[tid]) >= 3 and world_known.get(tid, null) != true:
					world_known[tid] = false
			else:
				world_known[tid] = true
	return "stuck"


func _plan_world(band: PackedByteArray, start: Vector2i, goal: Vector2i) -> Array[String]:
	var buckets := {0: [start]}  # f -> nodes; costs are small ints so a bucket queue is enough
	var best := {start: 0}
	var came := {}
	var f_min := 0
	var f_max := 0
	var seen := 0
	while f_min <= f_max and seen < 60000:
		if not buckets.has(f_min) or buckets[f_min].is_empty():
			f_min += 1
			continue
		var cur: Vector2i = buckets[f_min].pop_back()
		seen += 1
		if cur == goal:
			var path: Array[String] = []
			while came.has(cur):
				path.push_front(came[cur][1])
				cur = came[cur][0]
			return path
		for d in DIRS:
			var n: Vector2i = cur + DIRS[d]
			var c := _wcost(band, start, n)
			if c < 0:
				continue
			var g: int = best[cur] + c
			if g < best.get(n, 1 << 30):
				best[n] = g
				came[n] = [cur, d]
				var f: int = g + absi(n.x - goal.x) + absi(n.y - goal.y)
				f = maxi(f, f_min)
				if not buckets.has(f):
					buckets[f] = []
				buckets[f].append(n)
				f_max = maxi(f_max, f)
	return []


## Local-map frontier exploration: repeatedly walk to the nearest reachable tile that has not been visited yet,
## preferring tiles whose id is still unknown. Stops on a map change or battle. Returns the reason.
var visited := {}  # Vector2i -> true, for the current map
var _visited_map := -1
var local_bumps := {}  # tile id -> failed local-map attempts


func _nearest_unvisited(tiles: PackedByteArray, start: Vector2i) -> Vector2i:
	var seen := {start: true}
	var q: Array[Vector2i] = [start]
	var i := 0
	var fallback := Vector2i(-1, -1)
	while i < q.size():
		var cur := q[i]
		i += 1
		if not visited.has(cur) and cur != start:
			if not known.has(_tile(tiles, cur)):
				return cur
			if fallback.x < 0:
				fallback = cur
		for d in DIRS:
			var n: Vector2i = cur + DIRS[d]
			if seen.has(n) or _cost(tiles, n) < 0:
				continue
			seen[n] = true
			q.append(n)
	return fallback


func explore(max_goals := 40) -> String:
	var map0 := await ra.read_u8(FFVRam.MAP_ID)
	if map0 != _visited_map:
		visited.clear()  # kept across calls on the same map so repeated runs keep making progress
		_visited_map = map0
	for g in max_goals:
		var cur := await position()
		visited[cur] = true
		var tiles := await read_tiles()
		var goal := _nearest_unvisited(tiles, cur)
		if goal.x < 0:
			return "explored"
		var r := await goto(goal, 60)
		visited[goal] = true
		if r == "battle" or r == "map_changed":
			return r
		if (await ra.read_u8(FFVRam.MAP_ID)) != map0 or (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 0:
			return "map_changed"
	return "budget"
