class_name VirtualPad
extends Node
## The ONLY way the bot affects the game: presses of the 12 buttons of an
## unmodified SNES controller, sent to RetroArch's built-in "Network Gamepad"
## (Network RetroPad) receiver over UDP. No sudo, no extra device.
## Unknown button names are rejected, and opposite d-pad directions can't be held together.
##
## Default layout (EVENT_LE_20): RetroArch's `struct remote_message`, 20 bytes, little-endian:
##   int32 port, int32 device (1 = joypad), int32 index (0), int32 id (button), uint16 state (1/0), 2 pad bytes.
## Receiver behaviour: buttons LATCH until a state=0 message, and only ONE datagram is consumed per
## emulated frame per player, so event packets are queued and released one per 60 Hz tick.
## A datagram of any other size zeroes all buttons for the player (used by release_all).
## Other candidate layouts remain selectable in case a RetroArch build differs.

enum Layout { EVENT_LE_20, BITMASK_BE, BITMASK_LE, EVENT_BE_18, EVENT_BE_20, EVENT_LE_18 }

## libretro RETRO_DEVICE_ID_JOYPAD_* ids (also the bit index in the bitmask)
const IDS := {"B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5,
		"LEFT": 6, "RIGHT": 7, "A": 8, "X": 9, "L": 10, "R": 11}
const OPPOSITES := {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}

signal packet_sent(hex: String)

var layout := Layout.EVENT_LE_20
var _queue: Array[PackedByteArray] = []
var player := 0  ## 0 = player 1 -> UDP port 55400
var _udp := PacketPeerUDP.new()
var _held := {}
var _mask := 0


func open(host := "127.0.0.1") -> Error:
	_udp.close()
	return _udp.connect_to_host(host, 55400 + player)


## Bitmask layouts are state snapshots, so keep resending them while anything is held.
func _physics_process(_dt: float) -> void:  # 60 Hz
	if layout in [Layout.BITMASK_BE, Layout.BITMASK_LE]:
		if _mask != 0:
			_send(_state_packet())
	elif not _queue.is_empty():
		_send(_queue.pop_front())


func _state_packet() -> PackedByteArray:
	var p := StreamPeerBuffer.new()
	p.big_endian = layout == Layout.BITMASK_BE
	p.put_u32(_mask)
	for i in 4:
		p.put_16(0)
	p.put_u32(0)
	return p.data_array


func make_event_packet(id: int, down: bool) -> PackedByteArray:
	var p := StreamPeerBuffer.new()
	p.big_endian = layout in [Layout.EVENT_BE_18, Layout.EVENT_BE_20]
	p.put_32(player)
	p.put_32(1)  # RETRO_DEVICE_JOYPAD
	p.put_32(0)
	p.put_32(id)
	p.put_u16(1 if down else 0)
	if layout in [Layout.EVENT_BE_20, Layout.EVENT_LE_20]:
		p.put_u16(0)  # struct padding
	return p.data_array


func _send(data: PackedByteArray) -> void:
	_udp.put_packet(data)
	packet_sent.emit(data.hex_encode())


func set_button(name: String, down: bool) -> bool:
	name = name.to_upper()
	if not IDS.has(name):
		push_error("Not a SNES button: " + name)
		return false
	if down and OPPOSITES.has(name) and _held.get(OPPOSITES[name], false):
		set_button(OPPOSITES[name], false)
	_held[name] = down
	if down:
		_mask |= 1 << IDS[name]
	else:
		_mask &= ~(1 << IDS[name])
	if layout in [Layout.BITMASK_BE, Layout.BITMASK_LE]:
		_send(_state_packet())
	else:
		_queue.append(make_event_packet(IDS[name], down))
	return true


## Hold a button for `frames` frames (60 fps), then release.
func tap(name: String, frames := 6) -> void:
	if set_button(name, true):
		await get_tree().create_timer(frames / 60.0).timeout
		set_button(name, false)


## Panic reset: drop queued events and send one undersized datagram, which makes
## RetroArch zero every button for this player.
func release_all() -> void:
	_queue.clear()
	_held.clear()
	_mask = 0
	_send(PackedByteArray([0]))
