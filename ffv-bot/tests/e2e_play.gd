extends SceneTree
## Exploratory autoplay: explore local maps, fight battles, log map changes and event flag changes.
## Env: ROUNDS (default 20). Needs the test bed running.

func _initialize() -> void:
	var ra := RetroArchClient.new()
	var pad := VirtualPad.new()
	root.add_child(ra); root.add_child(pad)
	await process_frame
	ra.open(); pad.open(); await ra.autodetect()
	var nav := FFVNav.new(ra, pad, self)
	var battle := FFVBattle.new(ra, pad, self)
	var prev := await FFVGameState.read(ra)
	var base_flags := prev.event_flags.duplicate()
	for round in int(OS.get_environment("ROUNDS") if OS.has_environment("ROUNDS") else "20"):
		var r := await nav.explore(30)
		var s := await FFVGameState.read(ra)
		var line := "round %d explore=%s map=%d pos=(%d,%d) battle=%s" % [round, r, s.map_id, s.map_x, s.map_y, s.in_battle]
		if s.in_battle:
			line += " battle=" + await battle.run()
			s = await FFVGameState.read(ra)
			line += " party=" + str(s.party.map(func(m): return "%d/%d" % [m.hp, m.max_hp]))
		var changed: Array[int] = []
		for n in s.event_flags.size() * 8:
			if prev.event_flag(n) != s.event_flag(n):
				changed.append(n)
		if not changed.is_empty():
			line += " flags_changed=" + str(changed)
		print(line)
		prev = s
		if r == "map_changed":
			await create_timer(1.5).timeout
	quit()
