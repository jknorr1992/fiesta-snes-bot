extends SceneTree
## Needs the test bed running with the meteor_site state loaded (position 10,26 in the Tycoon meteor forest).
## Walks to (16,20), where the Boco dialog event fires and prints the decoded state.

func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra)
	root.add_child(pad)
	await process_frame
	ra.open()
	pad.open()
	print("autodetect: ", await ra.autodetect())
	var nav := FFVNav.new(ra, pad, self)
	var s := await FFVGameState.read(ra)
	print(s.describe())
	var res := await nav.goto(Vector2i(16, 20))
	print("goto -> ", res)
	print((await FFVGameState.read(ra)).describe())
	quit()
