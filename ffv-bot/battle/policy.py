"""Battle policy for the campaign simulation: what a sensible player does with the commands each job has.

    SmartPolicy(data)   callable(sim, char) -> Command        (direct commands, see sim.set_direct)

  * a healer with Cure-type magic heals the most hurt living ally below `heal_below` of max HP (the weakest-first spell that fills the gap)
  * a caster with attack magic casts its strongest affordable offensive spell on the healthiest-looking... first living monster
  * anybody else, or a caster out of MP, attacks the first living monster
  * with no healer, Potions go to an ally below `potion_below`
It records whether any party member was ever seen dead (`died`), so the campaign can demand a flawless win."""
from sim import Command, CHAR, ACTIVE, CMD_FIGHT as CMD_FIGHT_ID

MAGIC_COMMAND = 0x2C
CHAR_SPELLS = 0x2D34                 # battle RAM: CharSpells per character (650 bytes): ID[130], Level[130], MP[130], Targetting[130], Flags[130]
SPELL_SLOT = 650
HEAL_NAMES = ("Cure", "Cure2", "Cure3", "Cure4")
ATTACK_NAMES = ("Fire", "Ice", "Bolt", "Fire2", "Ice2", "Bolt2", "Fire3", "Ice3", "Bolt3")   # in rough order of strength (ids resolved per ROM)
POTIONS = ("Potion", "Hi-Potion", "X-Potion", "Elixir")

class SmartPolicy:
    mode = "direct"
    def __init__(self, data, heal_below=0.5, potion_below=0.35):
        self.d = data; self.heal_below = heal_below; self.potion_below = potion_below
        white = {data.spell_name(i): i for i in range(96) if data.spell_name(i) and data.spell_icon(i) == "white"}
        black = {data.spell_name(i): i for i in range(96) if data.spell_name(i) and data.spell_icon(i) == "black"}
        self.heal_ids = [white[n] for n in HEAL_NAMES if n in white]
        self.attack_ids = [black[n] for n in ATTACK_NAMES if n in black]
        self.life_ids = [white[n] for n in ("Life2", "Life") if n in white]
        self.pd_id = data.item_id("PhnxDown")
        self.potion_ids = [data.item_id(n) for n in ("Hi-Potion", "Potion")]
        self.died = False
        self.log = []

    def threshold(self, c): return self.heal_below

    def _known(self, sim, char, spell):
        b = CHAR_SPELLS + SPELL_SLOT * char
        w = sim.w
        if w[b + spell] != spell: return None
        flags = w[b + 520 + spell]
        if flags & 0x80: return None                       # disabled (not enough MP / muted)
        return w[b + 260 + spell]                          # MP cost

    def __call__(self, sim, char):
        party = sim.party
        if any(c.dead for c in party): self.died = True
        me = party[char]
        living_mon = [m for m in sim.monsters if not m.dead and m.hp > 0]
        target = (living_mon[0].i - 4) if living_mon else 0
        # revive a fallen ally: Life (a healer's spell) first, a Phoenix Down otherwise
        fallen = [c.i for c in party if c.dead and c.i != char]
        if fallen:
            ally = fallen[0]
            for sp in self.life_ids:
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp and not me.dead:
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
            slot = next((sl for sl, it in sim.inventory_slots.items() if it == self.pd_id), None)
            if slot is not None: return Command.item(slot, party=(ally,))
        # heal
        hurt = [(c.hp / max(1, c.max_hp), c.i) for c in party if not c.dead and c.max_hp and c.hp > 0 and c.hp / c.max_hp < self.threshold(c) and c.row & 0x40 == 0]
        if hurt:
            _, ally = min(hurt)
            for sp in reversed(self.heal_ids):
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp:
                    if sp != self.heal_ids[0] and (party[ally].max_hp - party[ally].hp) < 40 * (self.heal_ids.index(sp) + 1): continue
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
        # potions (healer out of MP, or a fighter): the most hurt ally below potion_below
        low = [(c.hp / max(1, c.max_hp), c.i) for c in party if not c.dead and c.hp > 0 and c.max_hp and c.hp / c.max_hp < self.potion_below]
        if low:
            _, ally = min(low)
            inv = sim.inventory_slots
            for item in self.potion_ids:
                slot = next((sl for sl, it in inv.items() if it == item), None)
                if slot is not None: return Command.item(slot, party=(ally,))
        # attack magic
        for sp in reversed(self.attack_ids):
            cost = self._known(sim, char, sp)
            if cost is not None and cost <= me.mp and living_mon:
                return Command(MAGIC_COMMAND, 0x20, Command.monster_mask(target), 0, sp)
        return Command.fight(target)


SPELL_TABLE = 0x110B80       # ROM: 8-byte spell records: flags, target, ?, MP cost, formula, ?, power, element
EL_OFF = {"absorb": 0x30, "block": 0x31, "immune": 0x32, "half": 0x33, "weak": 0x34}      # battle CharStruct element defences (bit per element: 1 fire, 2 ice, 4 bolt, ...)
BUFFS = ("Protect", "Shell", "Haste")                       # cast once on each member that can use it, in hard fights only
ATTACK_FORMULA = 0x06                                       # damaging black magic (Fire/Ice/Bolt 1-3); Holy uses it too
SUMMON_ROW_OFFSET = 23

class TacticalPolicy(SmartPolicy):
    """SmartPolicy plus what a careful player does:
      * revive first, then heal (Cure sized to the gap), then (hard fights) buff once: Protect / Shell / Haste;
      * attack magic by the monsters' real element table (read live from battle RAM): weakness x2, never absorbed / immune elements;
        the cheapest spell that is weak-hitting is preferred in ordinary fights, the strongest one in hard fights (boss, no-run);
        ordinary fights keep `mp_floor` of the caster's MP for healing / the next fights;
      * physical hits go to the living monster with the fewest HP (focus fire, no overkill spreading);
      * a character who can only fight, or has no worthwhile spell, attacks."""
    def __init__(self, data, hard=False, mp_floor=0.3, forbidden=None, **kw):
        super().__init__(data, **kw)
        self.hard = hard; self.mp_floor = mp_floor
        import jobcmds
        for mod in ("jobcmds_phys", "jobcmds_util"):                 # the plugin modules register their commands on import
            try: __import__(mod)
            except ImportError: pass
        self.forbidden = set(jobcmds.FORBIDDEN_DEFAULT if forbidden is None else forbidden)
        self.job_cmds = [c(self) for c in jobcmds.REGISTRY]
        names = {data.spell_name(i): i for i in range(96) if data.spell_name(i)}
        self.buff_ids = [names[n] for n in BUFFS if n in names]
        self.attack_all = [i for i in range(36, 54) if data.spell_icon(i) == "black" and data.r[SPELL_TABLE + 8 * i + 4] == ATTACK_FORMULA]
        # Summons: the spell record only names the summon, the damage is the record 23 further on (Shiva 75 -> 98 ... Bahamut 86 -> 109); they
        # always hit every enemy.  Phoenix (84) heals / revives and is no attack.
        self.summon_row = {i: i + SUMMON_ROW_OFFSET for i in range(75, 87) if i != 84 and data.r[SPELL_TABLE + 8 * (i + SUMMON_ROW_OFFSET) + 4] == ATTACK_FORMULA}
        self.attack_all += sorted(self.summon_row)
        self.buffed = set(); self.last_hp = {}; self.max_hit = 0; self.banned = set()

    def spell_info(self, sp):
        o = SPELL_TABLE + 8 * self.summon_row.get(sp, sp); r = self.d.r
        return dict(mp=r[o + 3], power=r[o + 6], element=r[o + 7])

    def buff_sequence(self): return self.buff_ids

    def priority(self, m): return -m.hp                   # higher = kill first (focus fire on the weakest)

    def _cure_reserve(self, sim, char):
        costs = [self._known(sim, char, sp) for sp in self.heal_ids]
        costs = [x for x in costs if x is not None]
        return min(costs) if costs else 0

    def _mon_el(self, sim, m, which): return sim.w[m.b + EL_OFF[which]]

    def __call__(self, sim, char):
        party = sim.party; me = party[char]
        if any(c.dead for c in party): self.died = True
        mons = [m for m in sim.monsters if not m.dead and m.hp > 0]
        # biggest single drop seen so far this battle -> heal earlier when hits are big compared to the HP pools
        for c in party:
            prev = self.last_hp.get(c.i)
            if prev is not None and c.hp < prev: self.max_hit = max(self.max_hit, prev - c.hp)
            self.last_hp[c.i] = c.hp
        alive = [c.max_hp for c in party if not c.dead and c.max_hp]
        if self.hard and alive and self.max_hit: self.heal_below = min(0.85, max(0.5, 1.4 * self.max_hit / min(alive)))
        # 1-3. revive / heal / potion: the SmartPolicy order (kept: it only acts when somebody needs it)
        c = self._support(sim, char, party, me, mons)
        if c is not None: return c
        c = self._job_command(sim, me, mons, "support")
        if c is not None: return c
        # 4. buffs for hard fights
        if self.hard and mons:
            for sp in self.buff_sequence():
                cost = self._known(sim, char, sp)
                if cost is None or cost > me.mp - self._cure_reserve(sim, char): continue
                for ally in sorted((c for c in party if not c.dead and c.max_hp), key=lambda c: -c.max_hp):
                    if (ally.i, sp) in self.buffed: continue
                    self.buffed.add((ally.i, sp))
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally.i), sp)
        if not mons: return Command.fight(0)
        # 5. attack magic with the element table
        best = None
        floor = 0 if self.hard else int(self.mp_floor * me.max_mp)
        cmd = self._multi(sim, me, mons, floor)
        if cmd is not None: return cmd
        for sp in self.attack_all:
            if sp in self.summon_row: continue                  # summons hit everybody: only _multi weighs them
            cost = self._known(sim, char, sp)
            if cost is None or cost > me.mp - floor: continue
            info = self.spell_info(sp)
            for m in mons:
                el = info["element"]
                if el and (self._mon_el(sim, m, "absorb") & el or self._mon_el(sim, m, "immune") & el): continue
                if (m.i, sp) in self.banned: continue
                weak = bool(el and self._mon_el(sim, m, "weak") & el)
                if not self.hard and not weak and me.mp - cost < me.max_mp * 0.6: continue    # ordinary fight, no weakness: only spend a surplus
                dmg = info["power"] * (2 if weak else 1)
                score = (dmg, -cost, self.priority(m)) if self.hard else (weak, -cost, self.priority(m))
                if best is None or score > best[0]: best = (score, sp, m)
        if best:
            return Command(MAGIC_COMMAND, 0x20, Command.monster_mask(best[2].i - 4), 0, best[1])
        c = self._job_command(sim, me, mons, "offense")
        if c is not None: return c
        # 6. physical: the weakest living monster
        pool = [m for m in mons if (m.i, 'fight') not in self.banned] or mons
        tgt = max(pool, key=self.priority)
        return Command.fight(tgt.i - 4)

    def fight_value(self, sim, me, mons):
        """Expected damage of a plain Fight by this character on its best target (replaced by jobcmds_phys when present)."""
        return getattr(self, "_fight_value", lambda sim, me, mons: 0)(sim, me, mons)

    def _job_command(self, sim, me, mons, kind):
        have = {self.d.command_name(c) for c in sim.w[me.b + 0x16:me.b + 0x1A] if c and not c & 0x80}
        best = None
        for jc in self.job_cmds:
            if jc.kind != kind or jc.name in self.forbidden or jc.name not in have: continue
            p = jc.propose(sim, me.i, me, mons)
            if p and (best is None or p[0] > best[0]): best = p
        if best is None: return None
        if kind == "offense" and best[0] <= self.fight_value(sim, me, mons): return None
        return best[1]

    def _multi(self, sim, me, mons, floor):
        """An attack spell that can be cast on every enemy at once (flag 0x80 of its record) when that removes clearly more enemy HP than the
        best single-target cast: each target takes half (the game's multi-target rule), so the veteran counts the damage a target can take
        before it dies -- the lowest roll of the spell's power against its magic defence, Level * Magic / 256 + 4 -- never the next roll."""
        if len(mons) < 2 and not any(self._known(sim, me.i, sp) is not None for sp in self.summon_row): return None
        lvl, mag = me.level, sim.w[me.b + 0x2B]
        mult = ((lvl * mag) >> 8) + 4
        def low(sp, m, multi):
            info = self.spell_info(sp); a = info["power"]; el = info["element"]; mdef = sim.w[m.b + 0x2F]
            if el:
                if self._mon_el(sim, m, "absorb") & el or self._mon_el(sim, m, "immune") & el or self._mon_el(sim, m, "block") & el: return None
                if self._mon_el(sim, m, "half") & el: a >>= 1
                elif self._mon_el(sim, m, "weak") & el: a <<= 1; mdef = 0
            if multi and len(mons) > 1: a >>= 1
            return min(m.hp, max(0, a - mdef) * mult)
        single = multi = None
        for sp in self.attack_all:
            cost = self._known(sim, me.i, sp)
            if cost is None or cost > me.mp - floor: continue
            forced = sp in self.summon_row
            can_all = forced or bool(self.d.r[SPELL_TABLE + 8 * sp] & 0x80)
            ones = [low(sp, m, False) for m in mons] if not forced else []
            ones = [x for x in ones if x is not None]
            if ones:
                v = (max(ones), -cost)
                if single is None or v > single: single = v
            if not can_all or any((m.i, sp) in self.banned for m in mons): continue
            alls = [low(sp, m, True) for m in mons]
            if any(x is None for x in alls): continue               # one of them would absorb / shrug it off: not a spell for everybody
            v = (sum(alls), -cost, sp)
            if multi is None or v > multi: multi = v
        if multi is None or multi[0] <= 0: return None
        if single is not None and multi[0] < 1.25 * single[0]: return None
        mask = 0
        for m in mons: mask |= 0x80 >> (m.i - 4)
        return Command(MAGIC_COMMAND, 0x20, mask, 0, multi[2])

    def _support(self, sim, char, party, me, mons):
        """Revive / heal / Potion exactly as SmartPolicy decides them; None when nothing is needed."""
        saved = self.attack_ids
        self.attack_ids = []                      # SmartPolicy.__call__ would otherwise choose attack magic / Fight itself
        try: cmd = SmartPolicy.__call__(self, sim, char)
        finally: self.attack_ids = saved
        return None if cmd.command == CMD_FIGHT_ID else cmd       # a fall-through to Fight means nothing to support


class VeteranPolicy(TacticalPolicy):
    """TacticalPolicy plus a seasoned player's knowledge of the monsters in front of it:
      * the AI script of every living monster (which attacks it can choose, under which conditions) and the
        minimum / maximum its damage formulas can produce on each party member (knowledge.py); never the next roll;
      * healing is sized per member: heal whoever the strongest possible hit could kill, or whose margin is thinnest;
      * buffs follow the threat: Protect only against physical attackers, Shell only against casters, Protect/Shell first
        for whichever kind of damage dominates;
      * counterattack scripts (react-to-command / element / magic / item / damage) are respected: an action that would
        set off a lethal counter is replaced by another target or spell when one exists."""
    def __init__(self, data, bestiary, monster_ids, **kw):
        super().__init__(data, **kw)
        self.B = bestiary; self.ids = monster_ids; self.threat = {}; self.kinds = (0, 0); self.mthreat = {}; self.pending = {}
        try: self.elixir_id = data.item_id("Elixir")
        except Exception: self.elixir_id = None

    # ---- threat model -------------------------------------------------------------------------
    def _moves(self, sim, m, mons):
        """Attack ids monster `m` can pick right now (HP-gated blocks only when its HP is under the limit)."""
        mid = self.ids[m.i - 4] if m.i - 4 < len(self.ids) else None
        if mid is None: return []
        out = []
        for e in self.B.script(mid):
            if not e.attacks: continue
            ok = True
            for c in e.conds:
                if c[0] == 2 and not (m.hp < (c[2] | c[3] << 8)): ok = False
                elif c[0] == 4 and c[2] == 0 and len(mons) > 1: ok = False        # "alone"
            if ok:
                out += e.attacks
                for k in e.shows:                       # it may turn into another form: that form's attacks come with it
                    if k < len(self.ids) and self.ids[k] is not None and self.ids[k] != mid:
                        out += [a for f in self.B.script(self.ids[k]) if not f.reactive for a in f.attacks]
        return sorted(set(out)), mid

    def _scan(self, sim, party, mons):
        """-> {member: (lo, hi)} strongest single action on each living member, and (physical, magic) hi totals."""
        from knowledge import damage_range
        w = bytes(sim.w); thr = {c.i: [0, 0] for c in party if not c.dead}
        phys = mag = 0; self.mthreat = {}
        for m in mons:
            mv = self._moves(sim, m, mons)
            if not mv: continue
            attacks, mid = mv; dm = self.d.monster(mid)
            for a in attacks:
                A = self.B.attack(a)
                for t in thr:
                    r = damage_range(w, dm, m.i, t, A, crit=A["formula"] in (1, 2))
                    if not r: continue
                    thr[t][0] = max(thr[t][0], r[0]); thr[t][1] = max(thr[t][1], r[1])
                    self.mthreat[m.i] = max(self.mthreat.get(m.i, 0), r[1])
                    if A["formula"] in (1, 2): phys = max(phys, r[1])
                    else: mag = max(mag, r[1])
        return {t: tuple(v) for t, v in thr.items()}, (phys, mag)

    def buff_sequence(self):
        phys, mag = self.kinds; seq = []
        name = self.d.spell_name
        by = {name(i): i for i in self.buff_ids}
        order = ["Protect", "Shell"] if phys >= mag else ["Shell", "Protect"]
        for n in order:
            if n in by and ((n == "Protect" and phys) or (n == "Shell" and mag)): seq.append(by[n])
        if "Haste" in by: seq.append(by["Haste"])
        return seq

    # ---- support with bookkeeping: nobody queues a second heal on a target that already has one coming ------------------------------
    def _covered(self, kind, tgt, sim, party):
        k = (kind, tgt); v = self.pending.get(k)
        if v is None: return False
        frame, hp0 = v
        done = (kind == "r" and not party[tgt].dead) or (kind == "h" and (party[tgt].hp > hp0 or party[tgt].dead)) or sim.frame - frame > 1500
        if done: del self.pending[k]; return False
        return True

    def _support(self, sim, char, party, me, mons):
        d = self.d
        # 1. revive (most valuable first: whoever heals / has the biggest MP pool), one revive per fallen ally
        fallen = sorted((c for c in party if c.dead and c.i != char and not self._covered("r", c.i, sim, party)), key=lambda c: -c.max_mp)
        if fallen and not me.dead:
            ally = fallen[0].i
            for sp in self.life_ids:
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp:
                    self.pending[("r", ally)] = (sim.frame, 0)
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
            slot = next((sl for sl, it in sim.inventory_slots.items() if it == self.pd_id), None)
            if slot is not None:
                self.pending[("r", ally)] = (sim.frame, 0)
                return Command.item(slot, party=(ally,))
        # 2. heal: the member the next top hit would hurt most
        def need(c):
            hi = self.threat.get(c.i, (0, 0))[1]
            return c.hp - hi
        hurt = [c for c in party if not c.dead and c.max_hp and c.hp > 0 and c.row & 0x40 == 0 and not self._covered("h", c.i, sim, party)
                and (c.hp / c.max_hp < self.threshold(c) or (self.hard and c.hp <= 0.8 * self.threat.get(c.i, (0, 0))[1]))]
        if hurt:
            ally = min(hurt, key=lambda c: (need(c) if self.hard else c.hp / c.max_hp)).i
            a = party[ally]
            for sp in reversed(self.heal_ids):
                cost = self._known(sim, char, sp)
                if cost is not None and cost <= me.mp:
                    if sp != self.heal_ids[0] and (a.max_hp - a.hp) < 40 * (self.heal_ids.index(sp) + 1): continue
                    self.pending[("h", ally)] = (sim.frame, a.hp)
                    return Command(MAGIC_COMMAND, 0x20, 0, Command.party_mask(ally), sp)
            # no spell from me: a healer who can still cast takes the job, an item only when the hit that kills is close
            healer_ready = any(not c.dead and c.i != char and any((self._known(sim, c.i, sp) or 99999) <= c.mp for sp in self.heal_ids) for c in party)
            danger = a.hp <= self.threat.get(ally, (0, 0))[1] or a.hp / a.max_hp < self.potion_below
            if danger or not healer_ready:
                inv = sim.inventory_slots
                deficit = a.max_hp - a.hp
                order = ([self.elixir_id] if self.elixir_id and deficit > 250 else []) + self.potion_ids
                for item in order:
                    slot = next((sl for sl, it in inv.items() if it == item), None)
                    if slot is not None:
                        self.pending[("h", ally)] = (sim.frame, a.hp)
                        return Command.item(slot, party=(ally,))
        return None

    def priority(self, m):
        # kill first what hurts most per HP left (its best possible hit / its remaining HP); equal threats -> weakest first
        return (self.mthreat.get(m.i, 0) + 1) / (m.hp + 1)

    def threshold(self, c):
        hi = self.threat.get(c.i, (0, 0))[1]
        return self.heal_below            # measured: healing earlier than TacticalPolicy's rule only wastes turns and MP

    def __call__(self, sim, char):
        party = sim.party
        mons = [m for m in sim.monsters if not m.dead and m.hp > 0]
        if mons: self.threat, self.kinds = self._scan(sim, party, mons)
        self.banned = set()
        for _ in range(4):
            cmd = super().__call__(sim, char)
            bad = self._counter_risk(sim, char, cmd, mons)
            if bad is None: return cmd
            self.banned.add(bad)
        return cmd

    def _counter_risk(self, sim, char, cmd, mons):
        """(monster slot, 'fight' | spell id) when the attack in `cmd` would set off a counter that can kill the
        actor (or take more than 60% of its max HP) and the monster could be left alone or hit otherwise; else None."""
        from knowledge import damage_range
        if cmd.command not in (CMD_FIGHT_ID, MAGIC_COMMAND) or not cmd.monsters: return None
        slot = next((m for m in mons if cmd.monsters & (0x80 >> (m.i - 4))), None)
        if slot is None or slot.i - 4 >= len(self.ids) or self.ids[slot.i - 4] is None: return None
        me = sim.party[char]; w = bytes(sim.w)
        fight = cmd.command == CMD_FIGHT_ID
        kind = "fight" if fight else "magic"
        A = None if fight else self.B.attack(cmd.selected)
        for e in self.B.counters(self.ids[slot.i - 4]):
            lim = e.hp_limit
            if lim and not slot.hp < lim[1]: continue
            if not self.B.reaction_matches(e, kind, element=A["element"] if A else 0, spell=None if fight else cmd.selected,
                                           category=0x80 if fight else A["category"]): continue
            worst = 0
            for a in e.attacks:
                r = damage_range(w, self.d.monster(self.ids[slot.i - 4]), slot.i, me.i, self.B.attack(a), crit=True)
                if r: worst = max(worst, r[1])
            if worst >= me.hp or worst > 0.6 * me.max_hp:
                return (slot.i, "fight" if fight else cmd.selected)
        return None


class FleePolicy:
    """Run away: the real game flees while L+R are held (the caller holds them); a ready character just guards."""
    mode = "direct"
    def __init__(self): self.died = False
    def __call__(self, sim, char):
        if any(c.dead for c in sim.party): self.died = True
        return Command.guard()
