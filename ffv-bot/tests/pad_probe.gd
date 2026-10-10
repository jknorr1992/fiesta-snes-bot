extends SceneTree
## Headless alternative to the test bench: godot --headless --path . -s tests/pad_probe.gd
## Cycles every packet layout, tapping DOWN 3 times each. Prefer scenes/test_bench.tscn.

func _initialize() -> void:
	var pad := VirtualPad.new()
	root.add_child(pad)
	await process_frame
	pad.open()
	for l in VirtualPad.Layout.values():
		pad.layout = l
		print("Trying layout ", VirtualPad.Layout.keys()[l], " in 2s ...")
		await create_timer(2.0).timeout
		for i in 3:
			await pad.tap("DOWN", 6)
			await create_timer(0.4).timeout
	print("Done. Which layout moved the cursor?")
	quit()
