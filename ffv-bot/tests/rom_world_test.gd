extends SceneTree
## Needs RetroArch on the world map (testbed slot 0). Compares ROM-decoded world 0 rows with the RAM band.
## ROM path from env FFV_ROM.

func _initialize() -> void:
	var ra := RetroArchClient.new()
	root.add_child(ra)
	await process_frame
	ra.open()
	await ra.autodetect()
	var rw := FFVRomWorld.new()
	assert(rw.load_rom(OS.get_environment("FFV_ROM")))
	var m := rw.world(0)
	var diffs := 0
	var rows := 0
	for y in range(130, 156):
		var ram := await ra.read_wram(FFVRam.WORLD_BAND + (y & 63) * 256, 256)
		rows += 1
		for x in 256:
			if ram[x] != m[y * 256 + x]:
				diffs += 1
	print("rows ", rows, " differing tiles ", diffs, " (a few are the player/vehicle overlay)")
	quit()
