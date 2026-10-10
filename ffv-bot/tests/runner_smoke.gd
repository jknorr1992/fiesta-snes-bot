extends SceneTree
## Smoke test for the bot runner scene against a running RetroArch: connect, run ~6 s, stop.
func _initialize() -> void:
	var r: Control = load("res://scenes/bot_runner.tscn").instantiate()
	root.add_child(r)
	await process_frame
	r.rom_edit.text = OS.get_environment("FFV_ROM")
	r._toggle()
	await create_timer(6.0).timeout
	print("status: ", r.status_label.text)
	print("advisor: ", r.goal_label.text.substr(0, 120).replace("\n", " | "))
	print("log: ", r._lines)
	r.running = false
	await create_timer(1.5).timeout
	quit()
