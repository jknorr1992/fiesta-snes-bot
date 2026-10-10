extends SceneTree
## From the character name screen: move to End and confirm, then clear dialog. Prints map/pos/flags.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	await nav._press("UP", 6)
	for i in 4:
		await nav._press("RIGHT", 6)
		await create_timer(0.15).timeout
	await nav._press("A", 6)
	await create_timer(1.0).timeout
	var n := await nav.clear_dialog(60)
	var s := await FFVGameState.read(ra)
	print("presses=", n, " map=", s.map_id, " pos=(", s.map_x, ",", s.map_y, ") local=", s.in_local_map, " battle=", s.in_battle)
	quit()
