extends SceneTree
## From the forest after flag 16: exit to the world map, walk to the pocket trigger (156,139), report flags.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var rw := FFVRomWorld.new()
	rw.load_rom(OS.get_environment("FFV_ROM"))
	nav.rom_map = rw.world(0); nav.rom_walk = rw.walkable_ids(0)
	nav.world_entrances = {157: true}  # the crater tile re-enters the forest
	if (await ra.read_u8(FFVRam.IN_LOCAL_MAP)) == 1:
		print("exit: ", await nav.goto(Vector2i(9, 25), 400))
		await create_timer(2.0).timeout
	print("walk: ", await nav.goto_world(Vector2i(156, 139), 200), " at ", await nav.position())
	await create_timer(2.0).timeout
	var n := await nav.clear_dialog(80)
	var s := await FFVGameState.read(ra)
	var on: Array[int] = []
	for i in 512:
		if s.event_flag(i):
			on.append(i)
	print("presses=", n, " map=", s.map_id, " pos=", s.map_x, ",", s.map_y, " flags=", on)
	quit()
