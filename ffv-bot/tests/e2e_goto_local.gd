extends SceneTree
## Walk to local-map tile (GX,GY) (env) in the current map, then mash A and log what happens (battle, flags).
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var s0 := await FFVGameState.read(ra)
	var r := await nav.goto(Vector2i(int(OS.get_environment("GX")), int(OS.get_environment("GY"))), 300)
	var s := await FFVGameState.read(ra)
	var d: Array[int] = []
	for n in s.event_flags.size() * 8:
		if s.event_flag(n) != s0.event_flag(n):
			d.append(n)
	print("goto -> ", r, " pos=(", s.map_x, ",", s.map_y, ") map=", s.map_id, " battle=", s.in_battle, " dialog=", await nav.dialog_open(), " flags_changed=", d)
	quit()
