extends SceneTree
## Needs the test bed with the first_battle state loaded.

func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra)
	root.add_child(pad)
	await process_frame
	ra.open()
	pad.open()
	await ra.autodetect()
	print((await FFVGameState.read(ra)).describe())
	var res := await FFVBattle.new(ra, pad, self).run()
	print("battle -> ", res)
	print((await FFVGameState.read(ra)).describe())
	quit()
