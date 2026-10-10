extends SceneTree
## From the forest after the goblin battle + naming: talk to Lenna and dump flag changes.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var s0 := await FFVGameState.read(ra)
	print("start map=", s0.map_id, " pos=", s0.map_x, ",", s0.map_y)
	print("to Lenna: ", await nav.goto(Vector2i(46, 14), 300))
	print("talk: ", await nav.interact("UP"))
	var n := await nav.clear_dialog(40)
	var s := await FFVGameState.read(ra)
	var d: Array[int] = []
	for i in 512:
		if s.event_flag(i) != s0.event_flag(i):
			d.append(i)
	print("presses=", n, " map=", s.map_id, " pos=", s.map_x, ",", s.map_y, " battle=", s.in_battle, " changed=", d)
	quit()
