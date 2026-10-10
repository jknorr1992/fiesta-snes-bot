class_name FFVBattle
extends RefCounted
## Minimal battle handler: while the in-battle flag is set, confirm the default command (Fight) on the
## first target. Good enough for the opening fights; command choice by job/menu cursor comes later
## once the battle menu state is mapped in RAM.

var ra: RetroArchClient
var pad: VirtualPad
var tree: SceneTree


func _init(p_ra: RetroArchClient, p_pad: VirtualPad, p_tree: SceneTree) -> void:
	ra = p_ra
	pad = p_pad
	tree = p_tree


## Returns "won", "lost" (whole party at 0 HP) or "timeout".
func run(max_presses := 400) -> String:
	for i in max_presses:
		if (await ra.read_u8(FFVRam.IN_BATTLE)) != 1:
			return "won"
		var s := await FFVGameState.read(ra)
		if s != null:
			var alive := false
			for m in s.party:
				if int(m.max_hp) > 0 and int(m.hp) > 0:
					alive = true
			if not alive:
				return "lost"
		await pad.tap("A", 4)
		await tree.create_timer(0.5).timeout
	return "timeout"
