extends SceneTree
## Times continuous walking on a local map: goes 10 tiles up (or GX/GY) and back.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	nav.verbose = true
	var battle := FFVBattle.new(ra, pad, self)
	if (await ra.read_u8(FFVRam.IN_BATTLE)) == 1:
		print("battle: ", await battle.run(400))
		await create_timer(2.0).timeout
	var a := await nav.position()
	var b := a + Vector2i(0, 8)
	var t := Time.get_ticks_msec()
	var r := await nav.goto(b, 40)
	print("out: ", r, " ", await nav.position(), " ", Time.get_ticks_msec() - t, " ms for ", absi(b.y - a.y), " tiles")
	t = Time.get_ticks_msec()
	r = await nav.goto(a, 40)
	print("back: ", r, " ", await nav.position(), " ", Time.get_ticks_msec() - t, " ms")
	quit()
