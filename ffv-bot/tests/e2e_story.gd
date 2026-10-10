extends SceneTree
## Exploratory: from the world map checkpoint, step into the crater tile then mash A, logging map/flag changes.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var s0 := await FFVGameState.read(ra)
	if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 0:
		print("goto crater tile -> ", await nav.goto_world(Vector2i(175, 142), 20))
		await create_timer(2.0).timeout
	if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 1:
		print("goto (16,20) -> ", await nav.goto(Vector2i(16, 20)))
	var prev := ""
	for i in int(OS.get_environment("STEPS") if OS.has_environment("STEPS") else "60"):
		await pad.tap("A", 6)
		await create_timer(0.7).timeout
		var s := await FFVGameState.read(ra)
		var diff: Array[int] = []
		for n in s.event_flags.size() * 8:
			if s.event_flag(n) != s0.event_flag(n):
				diff.append(n)
		var line := "map=%d pos=(%d,%d) battle=%s flags_changed=%s" % [s.map_id, s.map_x, s.map_y, s.in_battle, str(diff)]
		if line != prev:
			print(i, " ", line)
			prev = line
	quit()
