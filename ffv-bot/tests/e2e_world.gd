extends SceneTree
## Needs the test bed on the world map (checkpoint world_after_crater). Walks 14 tiles east then 12 south.

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
	nav.world_entrances = {157: true}  # the crater entrance tile (north of the start), seen in RAM
	var start := await nav.position()
	print("start ", start)
	for g in [start + Vector2i(6, 0), start + Vector2i(6, 6)]:
		print("goto ", g, " -> ", await nav.goto_world(g), " at ", await nav.position(), " blocked=", nav.world_known.size())
	print("known ", nav.world_known, " bumps ", nav.bumps, " entr ", nav.world_entrances)
	quit()
