extends SceneTree
## Continue exploring the current local map without reloading anything (state as left by the previous run).
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var r := await nav.explore(int(OS.get_environment("GOALS") if OS.has_environment("GOALS") else "30"))
	var s := await FFVGameState.read(ra)
	var tiles := await nav.read_tiles()
	print("explore -> ", r, " map=", s.map_id, " pos=(", s.map_x, ",", s.map_y, ") visited=", nav.visited.size())
	for y in range(maxi(0, s.map_y - 6), mini(64, s.map_y + 7)):
		var line := ""
		for x in range(maxi(0, s.map_x - 10), mini(64, s.map_x + 11)):
			line += "%3d%s" % [tiles[y * 64 + x], "@" if Vector2i(x, y) == Vector2i(s.map_x, s.map_y) else (" " if nav.known.get(tiles[y * 64 + x], null) != false else "x")]
		print(line)
	quit()
