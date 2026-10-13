#!/usr/bin/env python3
"""Python driver for the RetroArch test bed (network commands UDP 55355, network gamepad UDP 55400), used to play-test the route.
Pad: 20-byte little-endian remote_message (port, device=1, index=0, id int32, state uint16, 2 pad); buttons latch, one packet per frame.
Dev cheats (allowed by the user for route testing only): write WRAM via WRITE_CORE_RAM."""
import socket, struct, time, os

IDS = {"B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5, "LEFT": 6, "RIGHT": 7, "A": 8, "X": 9, "L": 10, "R": 11}
FRAME = 1 / 60.0

class Game:
    def __init__(self, host="127.0.0.1", fast=1.0):
        self.c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.c.settimeout(1.0)
        self.p = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.host, self.fast = host, fast

    # ---- commands ----
    def cmd(self, s, reply=False):
        self.c.sendto(s.encode(), (self.host, 55355))
        if reply:
            try: return self.c.recvfrom(65536)[0].decode()
            except socket.timeout: return None

    def read(self, off, n=1):
        want = "READ_CORE_RAM %x" % off
        for _ in range(8):
            self.c.setblocking(False)
            try:
                while True: self.c.recvfrom(65536)   # drop stale replies (non-blocking: costs microseconds)
            except Exception: pass
            self.c.settimeout(1.0)
            r = self.cmd("READ_CORE_RAM %x %d" % (off, n), True)
            if r:
                t = r.split()
                if len(t) == 2 + n and t[0] == "READ_CORE_RAM" and t[1] == want.split()[1] and t[2] != "-1":
                    return bytes(int(x, 16) for x in t[2:])
            time.sleep(0.05)
        raise IOError("read failed %x %d" % (off, n))

    def write(self, off, data):
        self.cmd("WRITE_CORE_RAM %x %s" % (off, " ".join("%02x" % b for b in data)))
        time.sleep(0.02)

    # ---- fast-forward (RetroArch's network command FAST_FORWARD only toggles; the FAST_FORWARD_HOLD variants are rejected by 1.22.2) ----
    FF_ON = os.environ.get("FFV_FF", "1") != "0"      # FFV_FF=0 turns fast-forwarding in cutscenes off
    _ff = False; _base_fps = None
    def frame_rate(self, dt=0.25):
        """Game frames per second of wall time, from the frame counter at WRAM $094A (0 when it does not tick)."""
        a = self.u8(0x94A); t = time.time(); time.sleep(dt); b = self.u8(0x94A)
        return ((b - a) & 255) / (time.time() - t)
    def normalize_speed(self):
        """Call once at start: if the emulator is already fast-forwarding (left on by a stopped run or by hand), switch it off and remember the normal rate."""
        if not self.FF_ON: return
        try: r = self.frame_rate()
        except IOError: return
        if r > 75:
            self.cmd("FAST_FORWARD"); time.sleep(0.3); r = self.frame_rate()
            print("   emulator was fast-forwarding: switched off (%.0f fps)" % r, flush=True)
        if 5 < r <= 75: self._base_fps = r
        self._ff = False
    def field_running(self, dt=0.06):
        """True when the field engine is running (the frame counter at WRAM $094A ticks); False in battles - including the result windows, when
        the battle flag $18 is already 0 - and in other frozen states."""
        a = self.u8(0x94A); time.sleep(dt); return self.u8(0x94A) != a
    def fast_forward(self, on):
        """Fast-forward on/off. Assumes the emulator runs at normal speed when first called; the normal-speed frame rate is measured then, and
        after each toggle the speed is checked against it (toggling again once if it went the wrong way), so a state changed by hand recovers."""
        if not self.FF_ON or on == self._ff: return self._ff
        if self._base_fps is None:
            r = self.frame_rate()
            if r > 75:                                  # faster than the console's 60 fps: fast-forward is already running (left on by hand or an earlier run)
                self.cmd("FAST_FORWARD"); time.sleep(0.3); self._ff = True; r = self.frame_rate()
                if r > 75: self.FF_ON = False; return False         # still fast: no toggle command to trust
            if r < 5: self.FF_ON = False; return False              # counter not ticking: cannot verify, leave the speed alone
            self._base_fps = r; self._ff = False
            if on == self._ff: return False
        for _ in range(2):
            self.cmd("FAST_FORWARD"); time.sleep(0.15)
            r = self.frame_rate(0.1)
            if r < 1: break                             # the counter is frozen (battle, a scene loading): the speed cannot be judged and one toggle is one toggle; a second one would undo it and leave the emulator fast-forwarding while we think it is not (every later battle hangs)
            if (r > 1.5 * self._base_fps) == on: break
        self._ff = on
        print("   fast-forward %s (%.0f -> %.0f fps)" % ("on" if on else "off", self._base_fps, self.frame_rate(0.1)), flush=True)
        return on
    def u8(self, o): return self.read(o)[0]
    def u16(self, o): b = self.read(o, 2); return b[0] | b[1] << 8
    def shot(self):
        self.cmd("SCREENSHOT"); time.sleep(0.5)
        d = os.environ.get("FFV_SHOTS", "/tmp/ffv-shots"); fs = sorted((os.path.join(d, f) for f in os.listdir(d)), key=os.path.getmtime)
        return fs[-1]
    slot = 0
    # RetroArch's save-state folder for the core: set FFV_STATES to it (default: the container test bed). Saving and loading
    # states is optional; without a readable folder the helpers below do nothing and return None.
    STATES = os.environ.get("FFV_STATES", "/mnt/project-files/retroarch-testbed/states/Snes9x")
    def save_as(self, path):
        """Save a state of the current slot (no "save slot N" network command exists) and copy the file to `path`."""
        import glob, shutil
        if not os.path.isdir(self.STATES): return None
        t0 = time.time(); self.cmd("SAVE_STATE")
        f = None
        for _ in range(40):                      # wait for the emulator to write the file (newer than this call)
            time.sleep(0.05)
            fs = [g for g in glob.glob(self.STATES + "/*.state*") if not g.endswith(".png") and os.path.getmtime(g) >= t0 - 0.2]
            if fs: f = max(fs, key=os.path.getmtime); break
        if f is None: return None
        time.sleep(0.1)
        try: os.makedirs(os.path.dirname(path) or ".", exist_ok=True); shutil.copy(f, path)
        except OSError: return None
        return f
    def load_from(self, path, slot=1):
        import shutil
        if not (os.path.isdir(self.STATES) and os.path.exists(path)): return False
        fs = [f for f in os.listdir(self.STATES) if f.endswith(".state%d" % slot)]
        rom = os.environ.get("FFV_ROM")           # RetroArch names state slots after the loaded content
        name = os.path.splitext(os.path.basename(rom))[0] + ".state%d" % slot if rom else (fs[0] if fs else None)
        if name is None: return False
        f = os.path.join(self.STATES, name)
        shutil.copy(path, f); self.load(slot); return True
    def load(self, slot): self.cmd("LOAD_STATE_SLOT %d" % slot); self.slot = slot; time.sleep(1.0)

    # ---- state ----
    def state(self):
        mp = self.u16(0xAD4)
        x, y = self.u8(0xAD8), self.u8(0xAD9)
        return {"map": mp & 0xFFF, "x": x, "y": y, "local": self.u8(0xB53), "battle": self.u8(0x18), "msg": self.u8(0xB57)}
    def flag(self, n): return bool(self.u8(0xA14 + (n >> 3)) >> (n & 7) & 1)
    def gil(self): b = self.read(0x947, 3); return b[0] | b[1] << 8 | b[2] << 16

    # ---- pad ----
    def _pkt(self, bid, down): return struct.pack("<iiiiHH", 0, 1, 0, bid, 1 if down else 0, 0)
    def _send(self, bid, down): self.p.sendto(self._pkt(bid, down), (self.host, 55400))
    def wait(self, frames): time.sleep(frames * FRAME / self.fast)
    def press(self, b, hold=4, gap=4):
        self._send(IDS[b], True); self.wait(hold); self._send(IDS[b], False); self.wait(gap)
    def hold(self, b, frames):
        self._send(IDS[b], True); self.wait(frames); self._send(IDS[b], False); self.wait(1)
    def release_all(self): self.p.sendto(b"\0", (self.host, 55400))
