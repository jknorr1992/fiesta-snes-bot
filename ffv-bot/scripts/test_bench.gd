extends Control
## Interactive test bench: connect to RetroArch, press SNES buttons, read RAM.
## Keyboard: arrows = d-pad, Z=B, X=A, A=Y, S=X, Q=L, W=R, Enter=Start, Backspace=Select.

const KEYMAP := {KEY_UP: "UP", KEY_DOWN: "DOWN", KEY_LEFT: "LEFT", KEY_RIGHT: "RIGHT",
		KEY_Z: "B", KEY_X: "A", KEY_A: "Y", KEY_S: "X", KEY_Q: "L", KEY_W: "R",
		KEY_ENTER: "START", KEY_BACKSPACE: "SELECT"}

var pad := VirtualPad.new()
var ra := RetroArchClient.new()
var host_edit: LineEdit
var layout_opt: OptionButton
var log_label: RichTextLabel
var addr_edit: LineEdit
var len_edit: LineEdit
var _lines: Array[String] = []
var _snap := PackedByteArray()


func _ready() -> void:
	add_child(pad)
	add_child(ra)
	pad.packet_sent.connect(func(h): _log("TX pad: " + h))
	var root := VBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT, Control.PRESET_MODE_MINSIZE, 12)
	add_child(root)

	var row := HBoxContainer.new()
	root.add_child(row)
	row.add_child(_label("Host"))
	host_edit = LineEdit.new()
	host_edit.text = "127.0.0.1"
	host_edit.custom_minimum_size.x = 140
	row.add_child(host_edit)
	row.add_child(_label("Pad layout"))
	layout_opt = OptionButton.new()
	for n in VirtualPad.Layout.keys():
		layout_opt.add_item(n)
	layout_opt.item_selected.connect(func(i): pad.layout = i; pad.release_all(); _log("layout = " + VirtualPad.Layout.keys()[i]))
	row.add_child(layout_opt)
	var connect_btn := Button.new()
	connect_btn.text = "Connect"
	connect_btn.pressed.connect(_connect)
	row.add_child(connect_btn)

	root.add_child(_label("Controller (hold to press; keyboard also works)"))
	var grid := GridContainer.new()
	grid.columns = 6
	root.add_child(grid)
	for b in VirtualPad.IDS:
		var btn := Button.new()
		btn.text = b
		btn.custom_minimum_size = Vector2(80, 44)
		btn.button_down.connect(pad.set_button.bind(b, true))
		btn.button_up.connect(pad.set_button.bind(b, false))
		grid.add_child(btn)
	var seq := Button.new()
	seq.text = "Tap each button in turn"
	seq.pressed.connect(_sequence)
	root.add_child(seq)

	ra.traffic.connect(func(c, r): _log("NCI >> %s\nNCI << %s" % [c, r if r != "" else "(no reply: timeout)"]))
	root.add_child(_label("Read RAM (READ_CORE_RAM: hex address from WRAM start, 0 = $7E0000)"))
	var rrow := HBoxContainer.new()
	root.add_child(rrow)
	addr_edit = LineEdit.new()
	addr_edit.text = "0"
	len_edit = LineEdit.new()
	len_edit.text = "16"
	var read_btn := Button.new()
	read_btn.text = "Read"
	read_btn.pressed.connect(_read)
	var auto_btn := Button.new()
	auto_btn.text = "Auto-detect"
	auto_btn.pressed.connect(func(): _log("autodetect: " + await ra.autodetect()))
	var gil_btn := Button.new()
	gil_btn.text = "Read Gil"
	gil_btn.pressed.connect(_read_gil)
	var snap_btn := Button.new()
	snap_btn.text = "Snapshot"
	snap_btn.pressed.connect(_snapshot)
	var diff_btn := Button.new()
	diff_btn.text = "Diff vs snapshot"
	diff_btn.pressed.connect(_diff)
	for c in [addr_edit, len_edit, read_btn, auto_btn, gil_btn, snap_btn, diff_btn]:
		rrow.add_child(c)

	log_label = RichTextLabel.new()
	log_label.size_flags_vertical = Control.SIZE_EXPAND_FILL
	log_label.scroll_following = true
	root.add_child(log_label)
	_connect()


func _label(t: String) -> Label:
	var l := Label.new()
	l.text = t
	return l


func _log(t: String) -> void:
	_lines.append(t)
	if _lines.size() > 200:
		_lines.pop_front()
	log_label.text = "\n".join(_lines)


func _connect() -> void:
	var e1 := pad.open(host_edit.text)
	_log("pad -> udp %s:%d (%s)" % [host_edit.text, 55400 + pad.player, error_string(e1)])
	# RetroArch's command socket may be reachable only over IPv4 or only over IPv6 loopback.
	var hosts: Array[String] = [host_edit.text]
	if host_edit.text == "127.0.0.1":
		hosts.append("::1")
	for h in hosts:
		var e2 := ra.open(h)
		var v := await ra.get_version()
		_log("commands -> [%s]:55355 (%s) VERSION reply: %s" % [h, error_string(e2), v if v != "" else "(none)"])
		if v != "":
			_log("Commands link OK via " + h)
			return
	_log("No reply to VERSION on any address. Check: Network Commands ON (restart RetroArch after enabling), port 55355, firewall, content loaded.")


func _read() -> void:
	var addr := addr_edit.text.hex_to_int()
	var b := await ra.read_memory(addr, int(len_edit.text))
	_log("RAM %x: %s" % [addr, b.hex_encode() if b.size() > 0 else "(no data / unmapped)"])


## Gil is believed to be a 3-byte little-endian value at WRAM $0947 (verify: new game = 500).
func _read_gil() -> void:
	var b := await ra.read_wram(0x947, 3)
	if b.size() == 3:
		_log("Gil @ WRAM 0947 = %d (bytes %s)" % [b[0] | (b[1] << 8) | (b[2] << 16), b.hex_encode()])
	else:
		_log("Gil read failed - run Auto-detect first; if it can't find a mapping, RetroArch exposes no RAM for this core.")


## Address discovery: snapshot, change something in game (buy an item, walk to a new map), then diff.
func _snapshot() -> void:
	_log("snapshotting 128 KB WRAM ...")
	_snap = await ra.snapshot_wram()
	_log("snapshot taken (%d bytes)" % _snap.size() if _snap.size() > 0 else "snapshot FAILED")


func _diff() -> void:
	if _snap.is_empty():
		_log("take a Snapshot first")
		return
	var now := await ra.snapshot_wram()
	if now.is_empty():
		_log("snapshot FAILED")
		return
	var changed := 0
	for i in now.size():
		if now[i] != _snap[i]:
			changed += 1
			if changed <= 60:
				_log("WRAM %04x: %02x -> %02x" % [i, _snap[i], now[i]])
	_log("%d bytes changed%s" % [changed, " (first 60 shown)" if changed > 60 else ""])


func _sequence() -> void:
	for b in VirtualPad.IDS:
		_log("tap " + b)
		await pad.tap(b, 6)
		await get_tree().create_timer(0.4).timeout


func _unhandled_key_input(e: InputEvent) -> void:
	var k := e as InputEventKey
	if k and not k.echo and KEYMAP.has(k.keycode):
		pad.set_button(KEYMAP[k.keycode], k.pressed)
