extends SceneTree
## Exploratory: from the meteor forest (testbed slot 0 then enter the crater tile), explore the local map and log positions.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 0:
		await nav.goto_world(Vector2i(175, 142), 20)
		await create_timer(2.0).timeout
	await nav.goto(Vector2i(16, 20))
	for i in 14:
		await pad.tap("A", 6)
		await create_timer(0.7).timeout
	var r := await nav.explore(int(OS.get_environment("GOALS") if OS.has_environment("GOALS") else "20"))
	var s := await FFVGameState.read(ra)
	print("explore -> ", r, " map=", s.map_id, " pos=(", s.map_x, ",", s.map_y, ") visited=", nav.visited.size(), " known=", nav.known)
	quit()
