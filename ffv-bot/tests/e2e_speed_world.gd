extends SceneTree
## Times continuous walking on the world map from the pocket start (175,143): to (168,144) and back.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	nav.verbose = true
	var rw := FFVRomWorld.new()
	rw.load_rom(OS.get_environment("FFV_ROM"))
	nav.rom_map = rw.world(0); nav.rom_walk = rw.walkable_ids(0)
	nav.world_entrances = {157: true}
	for g in [Vector2i(168, 144), Vector2i(175, 144)]:
		var t := Time.get_ticks_msec()
		var r := await nav.goto_world(g, 40)
		print(g, ": ", r, " at ", await nav.position(), " ", Time.get_ticks_msec() - t, " ms")
	quit()
