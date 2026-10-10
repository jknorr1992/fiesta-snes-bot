extends Control
## Map viewer for marking likely connections. Shows the static world map from the ROM (colored by tile id, with the
## on-foot passability prior) or, when RetroArch is connected, the live local map from RAM, with the player marker.
## Mouse: wheel = zoom, right/middle drag = pan, left click = select a tile. Markers and links are saved to
## user://marks.json and exported (Export button) to the path in the export field.

const WORLD_SIZE := 256
var rom_world := FFVRomWorld.new()
var ra := RetroArchClient.new()
var rom_edit: LineEdit
var export_edit: LineEdit
var world_opt: OptionButton
var live_check: CheckBox
var auto_check: CheckBox
var _live_view := ""
var _live_pos := Vector2i(-1, -1)
var name_edit: LineEdit
var kind_opt: OptionButton
var info: Label
var status: Label
var canvas: Control

var view_key := "world0"        # "world0".."world2" or "local<id>"
var image_tex: ImageTexture
var tiles := PackedByteArray()  # tile ids of the current view
var dim := Vector2i(WORLD_SIZE, WORLD_SIZE)
var walk := {}
var zoom := 3.0
var pan := Vector2(10, 10)
var selected := Vector2i(-1, -1)
var player := Vector2i(-1, -1)
var marks: Array = []           # {view, x, y, name, kind}
var links: Array = []           # {a: index, b: index, note}
var link_start := -1
var _drag := false
var _timer := 0.0


func _ready() -> void:
	add_child(ra)
	ra.open()
	_load_marks()
	var root := VBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 8)
	add_child(root)
	var bar := HBoxContainer.new()
	root.add_child(bar)
	rom_edit = LineEdit.new()
	rom_edit.placeholder_text = "Path to the ROM (.sfc) for the static world map"
	rom_edit.custom_minimum_size.x = 320
	bar.add_child(rom_edit)
	var load_btn := Button.new()
	load_btn.text = "Load ROM"
	load_btn.pressed.connect(_on_load_rom)
	bar.add_child(load_btn)
	world_opt = OptionButton.new()
	for i in 3:
		world_opt.add_item("World %d" % (i + 1), i)
	world_opt.item_selected.connect(func(i): _show_world(i))
	bar.add_child(world_opt)
	live_check = CheckBox.new()
	live_check.text = "Follow RetroArch (player + live local map)"
	bar.add_child(live_check)
	auto_check = CheckBox.new()
	auto_check.text = "Auto-link map transitions"
	auto_check.button_pressed = true
	bar.add_child(auto_check)
	canvas = Control.new()
	canvas.size_flags_vertical = Control.SIZE_EXPAND_FILL
	canvas.clip_contents = true
	canvas.draw.connect(_draw_canvas)
	canvas.gui_input.connect(_canvas_input)
	root.add_child(canvas)
	info = Label.new()
	root.add_child(info)
	var row := HBoxContainer.new()
	root.add_child(row)
	name_edit = LineEdit.new()
	name_edit.placeholder_text = "Name (e.g. Tule, Wind Shrine)"
	name_edit.custom_minimum_size.x = 220
	row.add_child(name_edit)
	kind_opt = OptionButton.new()
	for k in ["town", "dungeon", "entrance", "exit", "chest", "npc", "other"]:
		kind_opt.add_item(k)
	row.add_child(kind_opt)
	_btn(row, "Add marker", _add_marker)
	_btn(row, "Delete marker", _delete_marker)
	_btn(row, "Link: start", func(): link_start = _marker_at(selected); _set_status("Link start = marker %d" % link_start))
	_btn(row, "Link: end", _finish_link)
	_btn(row, "Center on player", func(): _center(player))
	var row2 := HBoxContainer.new()
	root.add_child(row2)
	export_edit = LineEdit.new()
	export_edit.text = "ffv_marks_export.json"
	export_edit.custom_minimum_size.x = 320
	row2.add_child(export_edit)
	_btn(row2, "Export", _export)
	status = Label.new()
	row2.add_child(status)
	_show_world(0)


func _btn(parent: Node, text: String, cb: Callable) -> void:
	var b := Button.new()
	b.text = text
	b.pressed.connect(cb)
	parent.add_child(b)


func _set_status(s: String) -> void:
	status.text = "  " + s


func _on_load_rom() -> void:
	if rom_world.load_rom(rom_edit.text.strip_edges()):
		walk = rom_world.walkable_ids(world_opt.selected)
		_show_world(world_opt.selected)
		_set_status("ROM loaded")
	else:
		_set_status("Could not read the ROM")


func _show_world(w: int) -> void:
	view_key = "world%d" % w
	dim = Vector2i(WORLD_SIZE, WORLD_SIZE)
	tiles = rom_world.world(w)
	walk = rom_world.walkable_ids(w)
	if tiles.is_empty():
		tiles.resize(WORLD_SIZE * WORLD_SIZE)
	_rebuild_image()


func _color(t: int) -> Color:
	if t == 135:
		return Color(0.08, 0.16, 0.6)
	var c := Color.from_hsv(fmod(t * 0.147, 1.0), 0.55, 0.95)
	if walk.has(t) and not walk[t]:
		c = c.darkened(0.45)
	return c


func _rebuild_image() -> void:
	var img := Image.create(dim.x, dim.y, false, Image.FORMAT_RGB8)
	for y in dim.y:
		for x in dim.x:
			img.set_pixel(x, y, _color(tiles[y * dim.x + x]))
	image_tex = ImageTexture.create_from_image(img)
	canvas.queue_redraw()


func _process(delta: float) -> void:
	_timer += delta
	if _timer < 0.25 or not live_check.button_pressed:
		return
	_timer = 0.0
	_poll()


func _poll() -> void:
	var s := await FFVGameState.read(ra)
	if s == null:
		return
	player = Vector2i(s.map_x, s.map_y)
	var live := "local%d" % s.map_id if s.map_id != 0 else "world%d" % world_opt.selected
	if live != _live_view:
		if auto_check.button_pressed and _live_view != "" and _live_pos.x >= 0 and not s.in_battle:
			_auto_link(_live_view, _live_pos, live, player)
		_live_view = live
	_live_pos = player
	if s.map_id != 0:  # live local map from RAM
		var key := "local%d" % s.map_id
		var t := await ra.read_wram(FFVRam.TILE_MAP, FFVRam.TILE_MAP_SIZE * FFVRam.TILE_MAP_SIZE)
		if t.size() == FFVRam.TILE_MAP_SIZE * FFVRam.TILE_MAP_SIZE:
			if key != view_key:
				view_key = key
				dim = Vector2i(FFVRam.TILE_MAP_SIZE, FFVRam.TILE_MAP_SIZE)
				walk = {}
				_center(player)
			tiles = t
			_rebuild_image()
	elif view_key.begins_with("local"):
		_show_world(world_opt.selected)
	canvas.queue_redraw()


## Record a transition seen while following RetroArch: a marker where the party left the old map, one where it
## arrived on the new map, and a link between them. Existing markers at those spots are reused.
func _auto_link(from_view: String, from_pos: Vector2i, to_view: String, to_pos: Vector2i) -> void:
	var a := _ensure_marker(from_view, from_pos, "entrance" if from_view.begins_with("world") else "exit")
	var b := _ensure_marker(to_view, to_pos, "exit" if to_view.begins_with("world") else "entrance")
	for l in links:
		if l.a == a and l.b == b:
			return
	links.append({"a": a, "b": b, "note": "auto %s -> %s" % [from_view, to_view]})
	_save_marks()
	_set_status("Linked %s (%d,%d) -> %s (%d,%d)" % [from_view, from_pos.x, from_pos.y, to_view, to_pos.x, to_pos.y])


func _ensure_marker(view: String, p: Vector2i, kind: String) -> int:
	for i in marks.size():
		if marks[i].view == view and marks[i].x == p.x and marks[i].y == p.y:
			return i
	marks.append({"view": view, "x": p.x, "y": p.y, "name": "auto", "kind": kind, "tile_id": -1})
	return marks.size() - 1


func _center(p: Vector2i) -> void:
	if p.x >= 0:
		pan = canvas.size / 2.0 - (Vector2(p) + Vector2(0.5, 0.5)) * zoom


func _to_tile(pos: Vector2) -> Vector2i:
	return Vector2i(((pos - pan) / zoom).floor())


func _canvas_input(ev: InputEvent) -> void:
	if ev is InputEventMouseButton:
		var mb := ev as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_WHEEL_UP or mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			var f := 1.25 if mb.button_index == MOUSE_BUTTON_WHEEL_UP else 0.8
			var before := (mb.position - pan) / zoom
			zoom = clampf(zoom * f, 1.0, 24.0)
			pan = mb.position - before * zoom
			canvas.queue_redraw()
		elif mb.button_index == MOUSE_BUTTON_RIGHT or mb.button_index == MOUSE_BUTTON_MIDDLE:
			_drag = mb.pressed
		elif mb.button_index == MOUSE_BUTTON_LEFT and mb.pressed:
			var t := _to_tile(mb.position)
			if t.x >= 0 and t.y >= 0 and t.x < dim.x and t.y < dim.y:
				selected = t
				var id := tiles[t.y * dim.x + t.x]
				info.text = "%s  tile (%d,%d)  id %d  %s" % [view_key, t.x, t.y, id,
						("walkable prior" if walk.get(id, false) else "blocked/unknown prior") if not walk.is_empty() else ""]
				canvas.queue_redraw()
	elif ev is InputEventMouseMotion and _drag:
		pan += (ev as InputEventMouseMotion).relative
		canvas.queue_redraw()


func _draw_canvas() -> void:
	canvas.draw_rect(Rect2(Vector2.ZERO, canvas.size), Color(0.05, 0.05, 0.08))
	if image_tex:
		canvas.draw_texture_rect(image_tex, Rect2(pan, Vector2(dim) * zoom), false)
	for i in marks.size():
		var m: Dictionary = marks[i]
		if m.view != view_key:
			continue
		var c := (Vector2(m.x, m.y) + Vector2(0.5, 0.5)) * zoom + pan
		canvas.draw_circle(c, maxf(zoom, 4.0), Color(1, 0.9, 0.1))
		canvas.draw_string(ThemeDB.fallback_font, c + Vector2(6, -6), "%d %s" % [i, m.name], HORIZONTAL_ALIGNMENT_LEFT, -1, 12, Color.WHITE)
	if view_key.begins_with("world"):
		for e in rom_world.entrances(int(view_key.substr(5))):
			var c := (Vector2(e.x, e.y) + Vector2(0.5, 0.5)) * zoom + pan
			canvas.draw_rect(Rect2(c - Vector2(3, 3), Vector2(6, 6)), Color(0.2, 1, 1))
			if zoom >= 4.0:
				canvas.draw_string(ThemeDB.fallback_font, c + Vector2(5, 12), "->%d" % e.dest_map, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.6, 1, 1))
	for l in links:
		var a: Dictionary = marks[l.a]
		var b: Dictionary = marks[l.b]
		if a.view == view_key and b.view == view_key:
			canvas.draw_line((Vector2(a.x, a.y) + Vector2(0.5, 0.5)) * zoom + pan, (Vector2(b.x, b.y) + Vector2(0.5, 0.5)) * zoom + pan, Color(1, 0.4, 0.2), 2.0)
	if selected.x >= 0:
		canvas.draw_rect(Rect2(Vector2(selected) * zoom + pan, Vector2(zoom, zoom)), Color.WHITE, false, 2.0)
	if player.x >= 0 and live_check.button_pressed:
		canvas.draw_circle((Vector2(player) + Vector2(0.5, 0.5)) * zoom + pan, maxf(zoom * 0.8, 4.0), Color(1, 0.1, 0.1))


func _marker_at(t: Vector2i) -> int:
	for i in marks.size():
		if marks[i].view == view_key and marks[i].x == t.x and marks[i].y == t.y:
			return i
	return -1


func _add_marker() -> void:
	if selected.x < 0:
		return
	var i := _marker_at(selected)
	var m := {"view": view_key, "x": selected.x, "y": selected.y, "name": name_edit.text, "kind": kind_opt.get_item_text(kind_opt.selected),
			"tile_id": tiles[selected.y * dim.x + selected.x]}
	if i >= 0:
		marks[i] = m
	else:
		marks.append(m)
	_save_marks()
	canvas.queue_redraw()


func _delete_marker() -> void:
	var i := _marker_at(selected)
	if i < 0:
		return
	marks.remove_at(i)
	links = links.filter(func(l): return l.a != i and l.b != i).map(func(l): return {"a": l.a - (1 if l.a > i else 0), "b": l.b - (1 if l.b > i else 0), "note": l.note})
	_save_marks()
	canvas.queue_redraw()


func _finish_link() -> void:
	var b := _marker_at(selected)
	if link_start < 0 or b < 0 or b == link_start:
		_set_status("Pick a start marker, then select the end marker")
		return
	links.append({"a": link_start, "b": b, "note": name_edit.text})
	link_start = -1
	_save_marks()
	canvas.queue_redraw()


func _save_marks() -> void:
	var f := FileAccess.open("user://marks.json", FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify({"marks": marks, "links": links}, "\t"))


func _load_marks() -> void:
	if not FileAccess.file_exists("user://marks.json"):
		return
	var d = JSON.parse_string(FileAccess.get_file_as_string("user://marks.json"))
	if d is Dictionary:
		marks = d.get("marks", [])
		links = d.get("links", [])


func _export() -> void:
	var path := export_edit.text.strip_edges()
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		_set_status("Cannot write " + path)
		return
	f.store_string(JSON.stringify({"marks": marks, "links": links}, "\t"))
	_set_status("Exported to " + ProjectSettings.globalize_path(path) if path.begins_with("res://") or path.begins_with("user://") else "Exported to " + path)
