#!/bin/bash
# Replays the opening from the new-game state (testbed slot 1) to the world map after the Lenna/Galuf scene.
# Needs RetroArch testbed running, FFV_ROM, and a Godot binary at $GODOT (default /tmp/work/godot).
G=${GODOT:-/tmp/work/godot}
cd "$(dirname "$0")/.."
python3 - <<'P'
import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.sendto(b"LOAD_STATE_SLOT 1",("127.0.0.1",55355)); time.sleep(3)
P
timeout 400 $G --headless --path . -s tests/e2e_opening.gd 2>&1 | tail -4
timeout 120 $G --headless --path . -s tests/e2e_name.gd 2>&1 | tail -1
timeout 300 $G --headless --path . -s tests/e2e_lenna.gd 2>&1 | tail -2
N=150 timeout 200 $G --headless --path . -s tests/e2e_clear.gd 2>&1 | tail -1
