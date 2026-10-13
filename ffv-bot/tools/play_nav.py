#!/usr/bin/env python3
"""Route play-test driver: walks the ROM-derived route on the test bed with dev cheats (immortal party, 1-HP monsters).
Movement uses ROM data for planning (world rows + tile properties; local map grid and tile properties read from WRAM) and
RAM position feedback; blocked steps are remembered and replanned. Cheats are for route testing only (user-approved)."""
import os, sys, socket, time
from collections import deque
sys.path.insert(0, os.path.dirname(__file__))
from play import Game, IDS
from world_model import World

DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}

HOLDS = (3, 14)

class Nav(Game):
    _void = {}
    def __init__(self, rom_path, fast=1.0, host="127.0.0.1"):
        super().__init__(host=host, fast=fast)
        self.world = World(open(rom_path, "rb").read())
        self.blocked = {}
        self.free = {}       # map -> {tile: set of directions}: forced-move trigger tiles (ladders/doorways) that the property rule rejects
        self.avoid = {}      # map -> set of tiles to path around (map entrances other than the goal)
        self.log = []
        from event_vm import Rom
        self._rom = Rom(rom_path)
        self.rom_entrances = self._rom.entrances

    # ---- cheats ----
    def cheat_party(self):
        for slot in range(4):
            b = 0x500 + 0x50 * slot
            if self.u8(b) & 0x40 or True:
                self.write(b + 6, bytes([0x0F, 0x27, 0x0F, 0x27]))      # hp, max hp 9999
                self.write(b + 10, bytes([0x0F, 0x27, 0x0F, 0x27]))     # mp, max mp 9999
                self.write(b + 2, bytes([99]))                          # level 99
                self.write(b + 0x24, bytes([99] * 8))                   # base and current strength, agility, stamina, magic power
    GEAR = {0x0E: 199, 0x0F: 200, 0x10: 201, 0x11: 198, 0x12: 198, 0x13: 21, 0x14: 0}      # Genji helm / armor / glove, Genji shields, Excalibur
    def cheat_field(self):
        """Route-test cheat on the field character structs (never the position): revive KO'd members, fill HP, and wear the best gear. The
        battle derives attack, defense and evade from the equipment at its start, and the Freelancers of the saved states carry daggers, so
        without this fights are barely winnable even at 'level 99' (the level byte is not used by the damage formula, the stats and gear are).
        FFV_GEAR=0 skips it."""
        if os.environ.get("FFV_GEAR", "1") == "0" or self.u8(0x18) == 1: return
        for slot in range(4):
            b = 0x500 + 0x50 * slot
            if self.u8(b) & 0x40: continue                                     # not in the party
            self.write(b + 6, bytes([0x0F, 0x27, 0x0F, 0x27]))                  # HP and max HP 9999 (a boss that opens with a hit above the party's real max HP would wipe it before the first command)
            self.write(b + 0x1A, bytes([self.u8(b + 0x1A) & 0x3F]))             # not dead (0x80) / stone (0x40)
            for o, v in self.GEAR.items(): self.write(b + o, bytes([v]))

    def start_god(self):
        """Immortality cheat: a background thread (own UDP socket) refills the party's battle HP and sets defense / magic defense to 255 every ~8 ms,
        so no enemy action can land a killing blow between two refills (the max-stat cheat only reaches the base stats; the derived defense values
        come from the equipment, which is nearly empty here)."""
        import threading
        if getattr(self, "_god", None): return
        self._god_stop = False
        def run():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setblocking(False)
            msgs = []
            for slot in range(4):
                b = 0x2000 + 0x80 * slot
                msgs += [("WRITE_CORE_RAM %x 0f 27" % (b + 6)).encode()]        # current HP 9999
                if self.cheat_level == "full":
                    msgs += [("WRITE_CORE_RAM %x ff" % (b + 0x2D)).encode(),    # defense 255 (derived from equipment, so the max-stat cheat does not reach it)
                             ("WRITE_CORE_RAM %x ff" % (b + 0x2F)).encode()]    # magic defense 255
            while not self._god_stop:
                for m in msgs: s.sendto(m, (self.host, 55355))
                try:
                    while True: s.recvfrom(65536)
                except Exception: pass
                time.sleep(0.008)
            s.close()
        self._god = threading.Thread(target=run, daemon=True); self._god.start()
    def stop_god(self):
        if getattr(self, "_god", None):
            self._god_stop = True; self._god.join(1); self._god = None

    def cheat_battle(self):
        for slot in range(4 if self.cheat_level != "enemy" else 0):
            b = 0x2000 + 0x80 * slot
            self.write(b + 6, bytes([0x0F, 0x27]))
        for slot in range(4, 12):
            b = 0x2000 + 0x80 * slot
            if self.u16(b + 6) > (0 if self.kill_mode else 1): self.write(b + 6, bytes([0, 0]) if self.kill_mode else bytes([1, 0]))

    # ---- state helpers ----
    def flags256(self):
        raw = self.read(0xA14, 96)
        m = 0
        for i, b in enumerate(raw): m |= b << (8 * i)
        return m
    def fpos(self):
        b = self.read(0xAD4, 6)
        return ((b[0] | b[1] << 8) & 0xFFF, b[4], b[5])
    def pos(self):
        m, x, y = self.fpos(); return m, x, y, self.u8(0xB53)
    def cur_veh(self):
        """Vehicle the party rides. $0ADC flickers to 0 while a vehicle moves, so take the largest of several samples; also match
        the party tile against the parked-vehicle table ($0ADD, 4 bytes each: flags, height, x, y; vehicle id = slot + 1)."""
        v = 0
        for _ in range(5):
            v = max(v, self.u8(0xADC)); time.sleep(0.03)
        if v: return v
        m, x, y = self.fpos()
        raw = self.read(0xADD, 24)
        for i in range(6):
            if raw[4 * i + 2] == x and raw[4 * i + 3] == y and (x, y) != (0, 0): return i + 1
        return 0
    def stable(self, tries=6):
        last, n = None, 0
        for _ in range(40):
            p = self.pos()
            if p == last: n += 1
            else: last, n = p, 1
            if n >= tries: return p
            self.wait(3)
        return last

    def battle_state(self):
        """(in battle, battle-over code, menu open for party slot or None) from the battle RAM (addresses from ffv-bot/battle/sim.py):
        $0018 battle flag, $7BDE battle-over (0x80 victory, 0x40 party KO, 1 escaped), $41B0 command menu open, $41CC menu character."""
        inb = self.u8(0x18) == 1
        o = self.read(0x41B0, 0x1D)
        over = self.u8(0x7BDE)
        cur = o[0x1C]
        return inb, over, (cur if o[0] == 1 and cur < 4 else None)

    cheat_level = "full"            # "full" = HP refill + defense 255 + 1-HP enemies, "hp" = HP refill + 1-HP enemies, "enemy" = 1-HP enemies only
    LEVELS = ("full", "hp", "enemy")
    nsnap = 0
    kill_mode = False               # enemy HP 0 instead of 1: set when attacks do no damage (e.g. a KO'd or weak party) so a 1-HP enemy never dies
    def fight(self, limit=4000):
        """Win the current battle. The emulator sometimes hangs in a battle (black screen, party idle) while cheats are being written: a
        watchdog (FFV_BATTLE_TIMEOUT s, default 240) notices, reloads the state taken at the start of the battle (FFV_BATTLE_SNAP names a
        folder for those states; they are kept for diagnosis) and retries with fewer cheats, after a different wait so the RNG differs."""
        snap = None
        d = os.environ.get("FFV_BATTLE_SNAP")
        if d:
            Nav.nsnap += 1; snap = os.path.join(d, "bs%d_%d.state" % (os.getpid(), Nav.nsnap))
            if self.save_as(snap) is None: snap = None
        tmo = float(os.environ.get("FFV_BATTLE_TIMEOUT", "240"))
        for attempt in range(len(self.LEVELS)):
            self.cheat_level = self.LEVELS[attempt]; self._hung = False
            try: r = self._fight(limit, time.time() + tmo)
            finally: self.stop_god()
            if not self._hung:
                self.cheat_level = "full"; self.cheat_field(); return r
            print("   battle hung (cheat level %s, attempt %d)%s" % (self.cheat_level, attempt + 1, "; reloading the battle start" if snap else ""), flush=True)
            if not snap or not self.load_from(snap): break
            self.wait(7 * (attempt + 1) + 5)
        self.cheat_level = "full"
        return False

    def _fight(self, limit=4000, deadline=None):
        """Win the current battle (enemy HP cheats), driven by the battle state instead of blind A presses: only act when a command menu is
        open (reset the remembered cursor to the first command, Fight, confirm the first target), dismiss the result windows once the
        battle is over, and when a menu stays open too long (stuck in a sub-menu such as Item) cancel with B and start over.
        Returns True when the battle flag cleared."""
        self.wait(180)                  # let the battle load: writing the cheats while the battle graphics load can hang the game (black screen)
        menu_since = None; menu_char = None; tries = 0; tick = 0; frozen = 0
        loaded = False                  # no cheat writes before the first command menu has opened (battle graphics fully loaded)
        for i in range(limit):
            if deadline and time.time() > deadline and self.u8(0x18) == 1: self._hung = True; return False
            inb, over, mc = self.battle_state()
            if not inb:
                if self.field_running(): return True
                frozen += 1                                  # the result windows (Gil, EXP, items) show with $18 already 0: A until the field runs again
                if frozen > 300: return False
                self.press("A", 3, 12); continue
            frozen = 0
            tick += 1
            if mc is not None and not loaded: loaded = True; t_loaded = time.time(); self.kill_mode = False
            if loaded and not self.kill_mode and time.time() - t_loaded > float(os.environ.get("FFV_KILL_AFTER", "25")):
                self.kill_mode = True; print("   attacks do not finish the enemies: writing enemy HP 0", flush=True)
            if loaded and os.environ.get('FFV_CHEAT', '1') != '0':
                if self.cheat_level != "enemy": self.start_god()
                if tick % 6 == 0: self.cheat_battle()
            if over:
                self.press("A", 3, 12); menu_since = None; continue
            if mc is None:
                menu_since = None; self.wait(2); continue
            if mc != menu_char or menu_since is None: menu_char, menu_since, tries = mc, i, 0
            if i - menu_since > 40 * (tries + 1):          # same menu still open: stuck in a sub-menu or a missed press
                tries += 1
                print("   battle menu stuck (char %d, try %d): cancelling" % (mc, tries), flush=True)
                for _ in range(3): self.press("B", 3, 6)
            cur = self.u8(0x42D + mc) if self.u8(0x426) & 0x80 == 0 else 0
            for _ in range(8):                             # command cursor memory: go back to the first command (Fight)
                if cur == 0: break
                self.press("UP", 3, 6); cur = self.u8(0x42D + mc)
            self.press("A", 3, 8)                          # Fight
            self.wait(10)
            if self.battle_state()[2] == mc: self.press("A", 3, 8)      # confirm the (first) target
            self.wait(20)
        return False

    def busy(self):
        """A message box is open ($B57), a battle is on ($18) or the party is being moved by a script (position changing)."""
        b = self.read(0xB57, 1)[0], self.read(0x18, 1)[0]
        if any(b): return True
        p = self.fpos(); self.wait(8)
        return self.fpos() != p

    def dialog(self, n=40):
        """Mash A while a message box is open or a script is running; returns presses used. Non-battle cutscenes run fast-forwarded
        (fast_forward switches itself off again before battles, name entry and when the scene is over, so walking keeps real-time timing)."""
        k = quiet = 0
        try:
            while k < n:
                if self.u8(0x18) == 1: self.fast_forward(False); self.fight()
                if self.u16(0xAD4) & 0xFFF == 13: self.fast_forward(False); self.name_screen(); continue
                if not self.busy():
                    quiet += 1
                    if quiet >= 4: break          # idle for a few polls: the scene is over
                    self.wait(4); continue
                quiet = 0
                self.fast_forward(True)           # busy and not in battle: a cutscene
                self.press("A", 3, 3); k += 1
        finally:
            self.fast_forward(False)
        return k

    def settle(self, limit=400):
        """Run scenes to the end: keep pressing A until nothing is running for ~2 s."""
        for _ in range(limit):
            self.dialog(60)
            self.wait(10)
            if not self.busy():
                self.wait(20)
                if not self.busy(): return True
        return False

    def name_screen(self):
        """Name entry (map 13): the default name is kept; UP to the header row, RIGHT x4 to End, A."""
        self.press("UP", 4, 8)
        for _ in range(4): self.press("RIGHT", 4, 8)
        self.press("A", 4, 30); self.wait(60)

    # ---- movement ----
    def run_dirs(self, dirs, start, per_tile=24):
        """Walk a chain of directions with the key held continuously, polling the position every frame (60 Hz). The pad is latched, so the
        next direction is sent the moment the party reaches the previous tile (no stop between tiles); the last key is released as soon
        as the final tile is reached so it cannot overshoot. Returns (tiles walked, pos)."""
        m0, cx, cy = self.fpos(); held = None; done = 0
        for d in dirs:
            dx, dy = DIRS[d]; exp = (cx + dx, cy + dy)
            if held != d:
                if held: self._send(IDS[held], False)
                self._send(IDS[d], True); held = d
            for _ in range(per_tile):
                p = self.fpos()                 # one RAM read per emulator frame: the read itself paces the loop at 60 Hz
                if p[0] != m0 or p[1:] == exp: break
            if p[0] != m0 or p[1:] != exp: break
            cx, cy = exp; done += 1
        if held: self._send(IDS[held], False)
        last = None
        for _ in range(10):                     # let the last tile finish
            cur = self.fpos()
            if cur == last: break
            last = cur
        return done, self.pos()

    def on_tile(self):
        """True when the party stands exactly on a tile: the engine only reads the d-pad then (CheckPlayerMove returns while BG1 x/y scroll
        $61/$63 is partway between tiles)."""
        b = self.read(0xB61, 3)
        return not (b[0] & 0x1f) and not (b[2] & 0x1f)

    def wait_tile(self, limit=60):
        for _ in range(limit * 2):
            if self.on_tile(): return True
            self.wait(0.5)                      # poll at 120 Hz
        return False

    def step(self, d, frames=14):
        """One tile in direction d. The engine reads the d-pad only when the party stands exactly on a tile and keeps walking while the key
        is down, so: wait until the party is on a tile, tap the key for a few frames, and only try a longer tap after the party is back on
        a tile and still has not moved (a late-read tap followed by a second tap would walk two tiles)."""
        from play import IDS
        before = self.fpos()
        moved = False
        for hold in HOLDS:
            self.wait_tile()
            self._send(IDS[d], True)
            for i in range(hold):
                self.wait(1)
            self._send(IDS[d], False)
            for _ in range(40):                 # poll at 120 Hz
                self.wait(0.5)
                if self.fpos() != before or not self.on_tile(): moved = True; break
            if moved: break
            self.wait(4)
            if self.fpos() != before: moved = True; break
        if moved:
            self.wait_tile()
            last = None
            for _ in range(10):                 # let the walk animation finish before the next command
                self.wait(2); cur = self.fpos()
                if cur == last and self.on_tile(): break
                last = cur
        else:
            self.wait(2)
        return moved, self.pos()

    def local_grid(self):
        ids = self.read(0x10000, 4096)
        props = self.read(0x1186, 512)   # 2 bytes per tile (byte 1, byte 2), matches the engine mirror at $10f2
        return ids, props

    SIDE = {"UP": 0x08, "RIGHT": 0x01, "DOWN": 0x04, "LEFT": 0x02}
    def can_move(self, ids, props, c, n, d):
        """Engine rule (CheckPartyCanMove): the tile you leave must open its side in direction d (properties byte 2),
        and the destination's z-level bits (byte 1) must match the current tile's z-level or be a bridge."""
        if max(c[1] * 64 + c[0], n[1] * 64 + n[0]) >= len(ids): return False
        tc, tn = ids[c[1] * 64 + c[0]], ids[n[1] * 64 + n[0]]
        if 2 * max(tc, tn) + 1 >= len(props): return False
        b1c, b2c = props[2 * tc], props[2 * tc + 1]
        b1n = props[2 * tn]
        if not (b2c & self.SIDE[d]) and not (b2c & 0x40): return False
        return bool(b1n & ((b1c & 3) | 4))

    def void_id(self, map_id, ids, props):
        """The filler tile that covers most of a cave/mountain map (black rock). Its props say open, but it is outside the playable area."""
        v = self._void.get(map_id)
        if v is None:
            import collections
            t, n = collections.Counter(bytes(ids[:4096])).most_common(1)[0]
            v = t if n > 1500 and 2 * t < len(props) and props[2 * t] & 0x08 else -1   # grass fills big maps too, but its props lack bit 0x08
            self._void[map_id] = v
        return v

    def can_move_m(self, map_id, ids, props, c, n, d):
        v = self.void_id(map_id, ids, props)
        if v >= 0 and max(c[1] * 64 + c[0], n[1] * 64 + n[0]) < len(ids) and ids[n[1] * 64 + n[0]] == v and (ids[c[1] * 64 + c[0]] == v or n not in self.avoid.get(map_id, ())): return False   # doors sit on the filler tile
        return self.can_move(ids, props, c, n, d)

    def hop_edges(self, map_id, c):
        """Forced-move facing triggers (events 840-843) hop the party two tiles over a deep-water gap: [(direction, target tile)]."""
        fm = self.free.get(map_id)
        if not fm or c not in fm: return []
        return [(d, (c[0] + 2 * DIRS[d][0], c[1] + 2 * DIRS[d][1])) for d in fm[c]]

    def plan_local(self, start, goal, ids, props, map_id):
        import heapq
        goals = goal if isinstance(goal, (set, frozenset)) else {goal}
        found = None
        bl = self.blocked.setdefault(map_id, set())
        av = self.avoid.get(map_id, ())
        dist, prev, pq = {start: 0}, {start: None}, [(0, start)]
        while pq:
            dcur, c = heapq.heappop(pq)
            if c in goals: found = c; break
            if dcur > dist[c]: continue
            for d, (dx, dy) in DIRS.items():
                n = (c[0] + dx, c[1] + dy)
                if not (0 <= n[0] < 64 and 0 <= n[1] < 64) or (c, d) in bl: continue
                if not self.can_move_m(map_id, ids, props, c, n, d): continue
                w = 1 + (60 if n in av and n not in goals else 0)   # stepping on another map's entrance leaves the map: only if unavoidable
                if n not in dist or dcur + w < dist[n]:
                    dist[n] = dcur + w; prev[n] = (c, d); heapq.heappush(pq, (dist[n], n))
            for d, n in self.hop_edges(map_id, c):
                if not (0 <= n[0] < 64 and 0 <= n[1] < 64) or (c, d) in bl: continue
                if n not in dist or dcur + 1 < dist[n]:
                    dist[n] = dcur + 1; prev[n] = (c, d); heapq.heappush(pq, (dist[n], n))
        if found is None: return None
        path, c = [], found
        while prev[c]: c, d = prev[c][0], prev[c][1]; path.append(d)
        return path[::-1]

    def plan_world(self, w, veh, start, goal, mask):
        """BFS over (tile, vehicle) states. The ship disembarks onto any walkable shore tile; the other vehicles use their landing rules."""
        mk = self.world.mod_key(w, mask)
        bl = self.blocked.setdefault((w, "w"), set())
        s0 = (start, veh or 0)
        q, prev = deque([s0]), {s0: None}
        end = None
        while q:
            st = q.popleft(); c, v = st
            if c == goal: end = st; break
            for d, (dx, dy) in DIRS.items():
                n = ((c[0] + dx) % 256, (c[1] + dy) % 256)
                if (c, d) in bl: continue
                if v and self.world.passable(w, v, n[0], n[1], mk): nv = v
                elif self.world.passable(w, 0, n[0], n[1], mk) and (v == 0 or v == 5 or self.world.can_land(w, v, n[0], n[1], mk)): nv = 0
                elif n == goal and v: nv = v
                else: continue
                ns = (n, nv)
                if ns in prev: continue
                prev[ns] = (st, d); q.append(ns)
        if end is None: return None
        path, c = [], end
        while prev[c]: c, d = prev[c][0], prev[c][1]; path.append(d)
        return path[::-1]

    def goto(self, x, y, veh=None, max_steps=2500, want_map=None):
        """Walk to tile (x, y) of the current map. Returns 'arrived', 'mapchange', 'stuck' or 'nopath'."""
        m0, cx, cy, local = self.stable()
        self.blocked.pop(m0 if local else (m0, "w"), None)   # bump memory is per attempt (NPCs and scenes move)
        stuck = nopath = 0
        for _ in range(max_steps):
            if self.u8(0x18) == 1: self.fight()
            if self.u8(0xB57): self.dialog();
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            if (cx, cy) == (x, y): return "arrived"
            if local:
                ids, props = self.local_grid()
                path = self.plan_local((cx, cy), (x, y), ids, props, m)
            else:
                v = veh if veh is not None else self.cur_veh()    # $0ADC = current vehicle (0 foot, 1 chocobo, ... as in world_model)
                path = self.plan_world(m, v, (cx, cy), (x, y), self.flags256() << 0)
            if not path:
                nopath += 1
                if nopath > 6: return self.probe(x, y, m0) or "nopath"
                for _ in range(3): self.press("A", 4, 14)    # result screens ($18 is already 0) need A presses
                self.settle(30); continue   # battle result screens and scripts leave the RAM grid unusable for a while
            chain = path[:12]
            k, p = self.run_dirs(chain, (cx, cy))
            if p[0] != m0: return "mapchange"
            cx, cy = p[1], p[2]
            if k == len(chain): continue      # whole chain walked: replan from here
            for d in chain[k:]:
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved:
                    if self.u8(0x18) == 1: self.fight()
                    elif self.u8(0xB57): self.dialog()
                    else:
                        # maybe an NPC is in the way (Boco, guards): talk to it, then retry once before treating the edge as blocked
                        for _ in range(8):                      # also clears battle result screens ($18 is already 0 there)
                            self.press("A", 4, 20)
                            moved, p = self.step(d)
                            if moved or p[0] != m0: break
                            if self.u8(0x18) == 1: self.fight()
                        if p[0] != m0: return "mapchange"
                        if not moved:
                            self.blocked.setdefault(m if local else (m, "w"), set()).add(((cx, cy), d)); stuck += 1
                    break
                dx, dy = DIRS[d]
                if not local and self.cur_veh() != v:
                    cx, cy = p[1], p[2]; break         # boarded or disembarked: replan with the new vehicle
                if (p[1], p[2]) != (cx + dx, cy + dy) and local:
                    cx, cy = p[1], p[2]; break         # overshoot or a scripted move: replan from where we are
                cx, cy = p[1], p[2]
            if stuck > 60: return "stuck"
        return "stuck"

    def probe(self, x, y, m0, limit=40):
        """Fallback when the grid says there is no path (tile properties can lie, e.g. before a scene rewrites the room):
        feel toward the goal with real steps, preferring the longer axis. Returns 'arrived'/'mapchange' or None."""
        for _ in range(limit):
            if self.u8(0x18) == 1: self.fight()
            if self.u8(0xB57): self.dialog()
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            if (cx, cy) == (x, y): return "arrived"
            opts = []
            if cx != x: opts.append(("RIGHT" if x > cx else "LEFT", abs(x - cx)))
            if cy != y: opts.append(("DOWN" if y > cy else "UP", abs(y - cy)))
            moved = False
            for d, _n in sorted(opts, key=lambda o: -o[1]):
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if moved: break
            if not moved: return None
        return None

    def leave_by_door(self, m0):
        """Maps whose way out has no exit-tile property (e.g. Wind Shrine front room): walk to the tile the world entrance
        drops you on, then keep walking outward (down, then the other directions) until the map changes."""
        door = next((e for wm in (0, 1, 2) for e in self.rom_entrances(wm) if e["map"] == m0), None)
        if not door: return None
        if self.goto(door["dx"] & 0x3F, door["dy"] & 0x3F) == "mapchange": return "mapchange"
        for d in ("DOWN", "LEFT", "RIGHT", "UP"):
            for _ in range(3):
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved: break
        return None

    def exit_tiles(self, ids, props):
        """Map exit tiles (engine: tile properties byte 2 == 0 on a passable tile leaves to the parent map)."""
        return {(x, y) for y in range(64) for x in range(64)
                if props[2 * ids[y * 64 + x] + 1] == 0 and props[2 * ids[y * 64 + x]] & 3}

    def leave_map(self, max_steps=800):
        """Walk to the nearest map exit tile; returns goto-style result."""
        m0, cx, cy, local = self.stable()
        ids, props = self.local_grid()
        goals = self.exit_tiles(ids, props) - self.avoid.get(m0, set())
        for _ in range(12):
            m, cx, cy, local = self.pos()
            if m != m0: return "mapchange"
            ids, props = self.local_grid()
            path = self.plan_local((cx, cy), goals, ids, props, m0)
            if not path: return self.leave_by_door(m0) or "nopath"
            for d in path[:10]:
                moved, p = self.step(d)
                if p[0] != m0: return "mapchange"
                if not moved:
                    if self.u8(0x18) == 1: self.fight()
                    elif self.u8(0xB57): self.dialog()
                    else: self.press("A", 4, 20); self.dialog(30)
                    break
        return "stuck"
