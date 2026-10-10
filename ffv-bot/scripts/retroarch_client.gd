class_name RetroArchClient
extends Node
## Read-only link to a running RetroArch via its UDP network command interface
## (enable Settings > Network > Network Commands, default port 55355).
##
## RULE: this class may only READ emulator state. There is deliberately no
## WRITE_CORE_MEMORY or any other state-changing command. Controller input goes
## through VirtualPad, which can only press the 12 SNES buttons.

const ALLOWED_COMMANDS := ["READ_CORE_MEMORY", "READ_CORE_RAM", "VERSION", "GET_STATUS"]

## Raw traffic for debugging: command sent and reply received ("" = timeout).
signal traffic(cmd: String, reply: String)

## Which read command to use. READ_CORE_RAM addresses WRAM from 0 (0 = $7E0000 on SNES) and
## works on cores without a libretro memory map; READ_CORE_MEMORY uses the core's own map.
var read_command := "READ_CORE_RAM"
## Address of WRAM offset 0 for the chosen command (0 for READ_CORE_RAM, 0x7E0000 for READ_CORE_MEMORY).
var wram_base := 0

var host := "127.0.0.1"
var port := 55355
var timeout_sec := 1.0

var _udp := PacketPeerUDP.new()
var _busy := false
var _waiting := false
var _waiting_cmd := ""  ## expected reply prefix; "" accepts any reply (VERSION replies just "1.22.2")
var _waiting_key := ""
var _reply := ""
var _got_reply := false


func open(p_host := "127.0.0.1", p_port := 55355) -> Error:
	host = p_host
	port = p_port
	_udp.close()
	return _udp.connect_to_host(host, port)


func _process(_dt: float) -> void:
	while _udp.get_available_packet_count() > 0:
		var text := _udp.get_packet().get_string_from_utf8().strip_edges()
		if _waiting and text.begins_with(_waiting_cmd):
			_reply = text
			_got_reply = true


## Send one allow-listed command and await its single-line reply ("" on timeout).
func _request(cmd: String, key_prefix: String) -> String:
	assert(cmd.split(" ")[0] in ALLOWED_COMMANDS, "command not allowed: " + cmd)
	while _busy:
		await get_tree().process_frame
	_busy = true
	_waiting = true
	_waiting_cmd = key_prefix
	_got_reply = false
	_udp.put_packet(cmd.to_utf8_buffer())
	var deadline := Time.get_ticks_msec() + int(timeout_sec * 1000.0)
	while not _got_reply and Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
	var out := _reply if _got_reply else ""
	_waiting = false
	_waiting_cmd = ""
	_busy = false
	traffic.emit(cmd, out)
	return out


## Read `length` bytes at `address` using `read_command`.
## Returns an empty array on timeout or error. Use as: var b = await client.read_memory(...)
func read_memory(address: int, length: int) -> PackedByteArray:
	return await _read(read_command, address, length)


func _read(command: String, address: int, length: int) -> PackedByteArray:
	var reply := await _request("%s %x %d" % [command, address, length], command)
	var out := PackedByteArray()
	var parts := reply.split(" ")
	# Reply: <CMD> <addr> <b0> <b1> ...   (error: "<CMD> <addr> -1 <message>" or "<CMD> -1 <message>")
	if parts.size() < 3 or parts[1] == "-1" or parts[2] == "-1":
		return out
	for i in range(2, parts.size()):
		out.append(parts[i].hex_to_int())
	return out


## Try the known ways to read WRAM and pick the first that returns data. Returns a report string.
func autodetect() -> String:
	var tries := [["READ_CORE_RAM", 0x0], ["READ_CORE_MEMORY", 0x7E0000], ["READ_CORE_MEMORY", 0x0]]
	for t in tries:
		var b := await _read(t[0], t[1], 16)
		if b.size() == 16:
			read_command = t[0]
			wram_base = t[1]
			return "OK: use %s with base address %x" % [t[0], t[1]]
	return "No read command returned data (see raw replies above)."


## Read from SNES work RAM by offset (offset 0 = $7E0000), whatever the detected mapping is.
func read_wram(offset: int, length: int) -> PackedByteArray:
	return await read_memory(wram_base + offset, length)


## Read all 128 KB of work RAM in chunks (replies are size-limited). Empty array on failure.
func snapshot_wram(chunk := 512) -> PackedByteArray:
	var out := PackedByteArray()
	var off := 0
	while off < 0x20000:
		var b := await read_wram(off, chunk)
		if b.size() != chunk:
			return PackedByteArray()
		out.append_array(b)
		off += chunk
	return out


func read_u8(address: int) -> int:
	var b := await read_memory(address, 1)
	return b[0] if b.size() == 1 else -1


func read_u16(address: int) -> int:
	var b := await read_memory(address, 2)
	return (b[0] | (b[1] << 8)) if b.size() == 2 else -1


func get_version() -> String:
	return await _request("VERSION", "")
