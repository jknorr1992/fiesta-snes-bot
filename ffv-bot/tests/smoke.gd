extends SceneTree
## Run: godot --headless --path . -s tests/smoke.gd  (with tools/fake_retroarch.py running)

func _initialize() -> void:
	var c := RetroArchClient.new()
	root.add_child(c)
	c.open()
	await process_frame
	var v := await c.get_version()
	print("version: ", v)
	c.read_command = "READ_CORE_MEMORY"
	var b := await c.read_memory(0x7E0010, 4)
	print("bytes: ", b)
	var ok := v.begins_with("1.") and b == PackedByteArray([112 & 0xFF, 119, 126, 133])
	var bad := await c.read_memory(0x100, 4)
	ok = ok and bad.is_empty()
	ok = ok and (await c.autodetect()).begins_with("OK")
	var pad := VirtualPad.new()
	root.add_child(pad)
	pad.open()
	pad.set_button("A", true)
	pad.packet_sent.connect(func(h): print("packet: ", h))
	ok = ok and not pad.set_button("TURBO", true) and not pad.set_button("F1", true)
	print("SMOKE ", "PASS" if ok else "FAIL")
	quit(0 if ok else 1)
