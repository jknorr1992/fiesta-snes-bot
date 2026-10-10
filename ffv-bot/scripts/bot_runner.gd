extends Control
## Bot runner: connect to RetroArch, press Start and the bot plays what it can (battles, dialog, the opening
## forest, exploring local maps) while the advisor panel shows the next goals. Stop interrupts it between actions.

const ROM_PATH_KEY := "rom_path"

var pad := VirtualPad.new()
var ra := RetroArchClient.new()
var nav: FFVNav
var battle: FFVBattle
var advisor := FFVAdvisor.new()
var rom_world := FFVRomWorld.new()
var running := false
var host_edit: LineEdit
var rom_edit: LineEdit
var status_label: Label
var goal_label: RichTextLabel
var log_label: RichTextLabel
var start_btn: Button
var connected := false
var _lines: Array[String] = []


func _ready() -> void:
	add_child(pad)
	add_child(ra)
	advisor.load_route()
	var root := VBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 12)
	add_child(root)

	var row := HBoxContainer.new()
	root.add_child(row)
	row.add_child(_label("RetroArch host"))
	host_edit = LineEdit.new()
	host_edit.text = "127.0.0.1"
	host_edit.custom_minimum_size.x = 140
	row.add_child(host_edit)
	row.add_child(_label("ROM (for the world map)"))
	rom_edit = LineEdit.new()
	rom_edit.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rom_edit.placeholder_text = "path to the .sfc, optional"
	rom_edit.text = _saved_rom()
	row.add_child(rom_edit)
	var connect_btn := Button.new()
	connect_btn.text = "Connect"
	connect_btn.pressed.connect(_connect)
	row.add_child(connect_btn)

	var row2 := HBoxContainer.new()
	root.add_child(row2)
	start_btn = Button.new()
	start_btn.text = "Start bot"
	start_btn.custom_minimum_size = Vector2(140, 48)
	start_btn.pressed.connect(_toggle)
	row2.add_child(start_btn)
	var name_btn := Button.new()
	name_btn.text = "Confirm name screen"
	name_btn.tooltip_text = "Moves to End and presses A once; the name entry screen has no RAM flag yet."
	name_btn.pressed.connect(_confirm_name)
	row2.add_child(name_btn)
	status_label = Label.new()
	status_label.text = "Not connected"
	row2.add_child(status_label)

	root.add_child(_label("Advisor"))
	goal_label = RichTextLabel.new()
	goal_label.custom_minimum_size.y = 120
	goal_label.fit_content = false
	root.add_child(goal_label)
	root.add_child(_label("Log"))
	log_label = RichTextLabel.new()
	log_label.size_flags_vertical = Control.SIZE_EXPAND_FILL
	log_label.scroll_following = true
	root.add_child(log_label)


func _label(t: String) -> Label:
	var l := Label.new()
	l.text = t
	return l


func _log(t: String) -> void:
	_lines.append(t)
	if _lines.size() > 300:
		_lines.pop_front()
	log_label.text = "\n".join(_lines)


func _saved_rom() -> String:
	var cfg := ConfigFile.new()
	if cfg.load("user://runner.cfg") == OK:
		return str(cfg.get_value("runner", ROM_PATH_KEY, ""))
	return ""


func _connect() -> void:
	ra.open(host_edit.text)
	pad.open(host_edit.text)
	var v := await ra.autodetect()
	_log("autodetect: " + v)
	nav = FFVNav.new(ra, pad, get_tree())
	battle = FFVBattle.new(ra, pad, get_tree())
	if rom_edit.text != "" and rom_world.load_rom(rom_edit.text):
		nav.rom_map = rom_world.world(0)
		nav.rom_walk = rom_world.walkable_ids(0)
		var cfg := ConfigFile.new()
		cfg.set_value("runner", ROM_PATH_KEY, rom_edit.text)
		cfg.save("user://runner.cfg")
		_log("ROM world map loaded")
	connected = true
	await _refresh()


func _toggle() -> void:
	if not connected:
		await _connect()
	running = not running
	start_btn.text = "Stop bot" if running else "Start bot"
	if running:
		_log("bot started")
		_loop()
	else:
		_log("bot stopping after the current action")


func _confirm_name() -> void:
	if nav == null:
		return
	await nav._press("UP", 6)
	for i in 4:
		await nav._press("RIGHT", 6)
		await get_tree().create_timer(0.15).timeout
	await nav._press("A", 6)


func _refresh() -> FFVGameState:
	var s := await FFVGameState.read(ra)
	if s == null:
		status_label.text = "State read failed (is the core snes9x and content running?)"
		return null
	status_label.text = "map %d at (%d,%d)  gil %d  %s" % [s.map_id, s.map_x, s.map_y, s.gil, "BATTLE" if s.in_battle else ""]
	goal_label.text = advisor.describe(s)
	return s


## One decision per iteration; every branch ends with a game-side action so the loop never spins silently.
func _loop() -> void:
	var idle := 0
	while running:
		var s := await _refresh()
		if s == null:
			await get_tree().create_timer(1.0).timeout
			continue
		if s.in_battle:
			_log("battle: " + await battle.run(400))
			continue
		if await nav.dialog_open():
			await nav.clear_dialog(5)
			continue
		var act := await _act(s)
		if act == "":
			idle += 1
			if idle == 1:
				_log("nothing to do here yet; waiting (the advisor panel shows the next goal)")
			await get_tree().create_timer(1.0).timeout
		else:
			idle = 0
			_log(act)
	_log("bot stopped")


## Returns a log line for what it did, or "" when it has no action for this situation.
func _act(s: FFVGameState) -> String:
	var in_local := (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 1
	if in_local and s.map_id == 122:
		if not s.event_flag(16):  # opening forest: Lenna and Galuf scene
			if not s.event_flag(306):
				return "forest: walk to the goblin trigger: " + await nav.goto(Vector2i(27, 21), 200)
			var r := await nav.goto(Vector2i(46, 14), 300)
			if r == "ok":
				await nav.interact("UP")
			return "forest: Lenna " + r
		return "forest: leave through the exit: " + await nav.goto(Vector2i(9, 25), 300)
	if in_local:
		return "explore: " + await nav.explore(5)
	return ""
