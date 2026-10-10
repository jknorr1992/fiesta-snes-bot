extends SceneTree
## Needs the test bed on the world map. Env: GX, GY target tile, FFV_ROM optional. Prints result, position, map id.

func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra)
	root.add_child(pad)
	await process_frame
	ra.open()
	pad.open()
	await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	nav.world_entrances = {157: true}
	if OS.has_environment("FFV_ROM"):
		var rw := FFVRomWorld.new()
		if rw.load_rom(OS.get_environment("FFV_ROM")):
			nav.rom_map = rw.world(0)
			nav.rom_walk = rw.walkable_ids(0)
	var g := Vector2i(int(OS.get_environment("GX")), int(OS.get_environment("GY")))
	var r := await nav.goto_world(g, 600)
	var st := await FFVGameState.read(ra)
	print("goto ", g, " -> ", r, " at ", await nav.position(), " map_id=", st.map_id, " known=", nav.world_known, " entr=", nav.world_entrances)
	quit()
