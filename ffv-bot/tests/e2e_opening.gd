extends SceneTree
## Replays the opening from the world checkpoint (testbed slot 0): crater tile -> Boco event -> Lenna scene.
## Prints the dialog length and event flags that changed. Leaves the game at the end of the Lenna scene.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var s0 := await FFVGameState.read(ra)
	if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 0:
		nav.world_entrances = {157: true}
		await nav.goto_world(Vector2i(175, 142), 20)
		await create_timer(2.0).timeout
	print("to Boco event: ", await nav.goto(Vector2i(16, 20)))
	await nav.clear_dialog(80)
	print("to Lenna: ", await nav.goto(Vector2i(46, 14), 300))
	print("talk: ", await nav.interact("UP"))
	var n := await nav.clear_dialog(200)
	var s := await FFVGameState.read(ra)
	var d: Array[int] = []
	for i in s.event_flags.size() * 8:
		if s.event_flag(i) != s0.event_flag(i):
			d.append(i)
	print("dialog presses=", n, " pos=(", s.map_x, ",", s.map_y, ") battle=", s.in_battle, " flags_changed=", d)
	quit()
