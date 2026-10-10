extends SceneTree
## End-to-end check against a live RetroArch: mash A/START through the intro and print Gil (expect 500 on a new game).
## Needs RetroArch + snes9x running (see retroarch-testbed/).

func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra)
	root.add_child(pad)
	await process_frame
	ra.open()
	pad.open()
	print("autodetect: ", await ra.autodetect())
	for i in int(OS.get_environment("E2E_PRESSES") if OS.has_environment("E2E_PRESSES") else "60"):
		await pad.tap("START" if i % 4 == 0 else "A", 6)
		await create_timer(0.8).timeout
		if i % 10 == 9:
			var g := await ra.read_wram(0x947, 3)
			print("t=", i, " gil bytes: ", g.hex_encode())
	var g := await ra.read_wram(0x947, 3)
	print("FINAL gil = ", g[0] | (g[1] << 8) | (g[2] << 16) if g.size() == 3 else -1)
	quit()
