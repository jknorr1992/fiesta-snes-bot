extends SceneTree
## Clears dialog (env N presses, default 60) and prints state + set event flags.
func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var n := await nav.clear_dialog(int(OS.get_environment("N")) if OS.has_environment("N") else 60)
	var s := await FFVGameState.read(ra)
	var on: Array[int] = []
	for i in 512:
		if s.event_flag(i):
			on.append(i)
	print("presses=", n, " map=", s.map_id, " pos=", s.map_x, ",", s.map_y, " local=", s.in_local_map, " battle=", s.in_battle, " flags=", on)
	quit()
