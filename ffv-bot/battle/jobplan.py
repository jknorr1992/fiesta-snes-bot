"""Job planning for the campaign: what a veteran does with the jobs the crystals have handed out.

The party has four ROLE SLOTS, not four fixed jobs (user, 2026-10-12): front line, nuker, healer, support.  Every job is classified by the
roles it can fill; which job fills a slot is decided from

  * foresight: what the next areas throw at the party (enemy count, HP, magic defence, weaknesses, bosses, who hits hard with what);
  * the job's real contribution right now: its derived stats and the spells the member can cast in it (probed in a battle setup, i.e. with the
    game's own derivation, never a guess at formulas);
  * ABP: abilities are learned per character and stay learned, ABP is earned by the CURRENT job only, so a job whose next abilities are
    worth a lot for little ABP is a good choice when the fights allow it (the "funnel").

All members start from the same theoretical worst-case base character (jobs.py), so the member only matters through what it has already
learned: the member with the most progress in a job gets it.  No assignment is tried in the simulator to find the best one; the plan is
reasoned, and a boss that still wins against it is answered by changing the one role the failure points at."""
import itertools
import json
import os

ROLES = ("front", "nuker", "healer", "support")
HERE = os.path.dirname(os.path.abspath(__file__))

# What each job is for, 0-10 per role (veteran knowledge, before the game state is looked at).
FIT = {
    "Freelancer": dict(front=3, nuker=2, healer=1, support=2),
    "Knight": dict(front=9, support=3),
    "Monk": dict(front=9, healer=1),
    "Thief": dict(front=5, support=5),
    "Dragoon": dict(front=9),
    "Ninja": dict(front=9, support=4),
    "Samurai": dict(front=8),
    "Berserker": dict(front=6),
    "Ranger": dict(front=6, support=3),
    "SpellBlade": dict(front=7, nuker=4),
    "WhiteMage": dict(healer=10, support=5),
    "BlackMage": dict(nuker=10),
    "TimeMage": dict(support=9, nuker=3),
    "Summoner": dict(nuker=8),
    "BlueMage": dict(nuker=6, healer=3, support=3),
    "RedMage": dict(nuker=6, healer=6, support=6, front=3),
    "Tamer": dict(support=4, front=3),
    "Geomancer": dict(front=5, support=4),
    "Bard": dict(support=5),
    "Dancer": dict(support=3, front=4),
    "Chemist": dict(healer=7, support=6),
    "Mime": dict(front=6, support=3),
}

# command groups: first command id of each magic type (levels follow)
GROUP_START = {"MgcSwrd": 44, "White": 50, "Black": 56, "Time": 62, "Summon": 68, "Red": 73}
# how much a magic level is worth to the run (index = level - 1)
LEVEL_VALUE = {"White": (6, 8, 10, 7, 7, 9), "Black": (6, 9, 10, 8, 8, 9), "Time": (6, 6, 5, 4, 4, 4), "Summon": (5, 8, 8, 7, 6),
               "Red": (5, 6, 6), "MgcSwrd": (3, 3, 3, 3, 3, 3)}
COMMAND_VALUE = {"Steal": 3, "Mug": 5, "Jump": 6, "Lance": 6, "Throw": 6, "Gil Toss": 2, "Chakra": 4, "Focus": 4, "Kick": 2, "Guard": 3,
                 "Aim": 4, "Animals": 4, "Drink": 5, "Mix": 6, "Recover": 4, "Revive": 4, "Sing": 3, "Dance": 3, "Catch": 4, "Release": 4,
                 "Control": 4, "Calm": 2, "Terrain": 4, "Mimic": 5, "Slash": 5, "SwdSlap": 3, "Smoke": 2, "Image": 2, "Check": 1,
                 "Analyze": 1, "Flirt": 1, "Hide": 1, "Escape": 1, "Row": 0, "Blue": 4, "DuCast": 8, "Call": 3}
PASSIVE_VALUE = {"Barrier": 4, "Cover": 4, "Counter": 3, "Evade": 3, "Pharmacy": 3, "HP +30%": 10, "HP +20%": 7, "HP +10%": 4, "MP +30%": 5,
                 "MP +10%": 2, "Barehand": 2, "Dbl.Grip": 2, "A.Dodger": 1, "DualWield": 3}


def load_unlocks(path=None):
    path = path or os.path.join(HERE, "..", "data", "job_unlocks.json")
    return json.load(open(path))["unlocks"]


class Foresight:
    """What the coming fights look like (a few stages ahead), from the encounter tables and the monsters' own data."""
    def __init__(self, data, bestiary, fights):
        """`fights`: [(battle id, background, label, scripted)] as campaign.schedule() lists them."""
        d = data
        counts = []; hps = []; defs = []; mdefs = []; weak = {}; bosses = []; phys = mag = 0.0; n = 0
        if True:
            for enc, bg, label, scripted in fights:
                b = d.battle(enc); ids = [m for m in b["monster_ids"] if m is not None]
                mons = [d.monster(m) for m in ids]
                if not mons: continue
                n += 1; counts.append(len(mons)); hps.append(sum(m["hp"] for m in mons))
                defs.append(sum(m["defense"] for m in mons) / len(mons)); mdefs.append(sum(m["mdefense"] for m in mons) / len(mons))
                if b["boss"] or scripted: bosses.append(max(m["hp"] for m in mons))
                for m in mons:
                    for bit in range(8):
                        if m["weak"] & (1 << bit): weak[bit] = weak.get(bit, 0) + 1
                    for e in bestiary.script(m["id"]):
                        for a in e.attacks:
                            f = bestiary.attack(a)["formula"]
                            if f in (1, 2): phys += 1
                            elif f: mag += 1
        n = max(1, n)
        self.fights = n
        self.avg_count = sum(counts) / n if counts else 1.0
        self.avg_hp = sum(hps) / n if hps else 100.0
        self.avg_def = sum(defs) / n if defs else 0.0
        self.avg_mdef = sum(mdefs) / n if mdefs else 0.0
        self.weak = weak
        self.boss_hp = max(bosses) if bosses else 0
        self.bosses = len(bosses)
        tot = max(1.0, phys + mag)
        self.phys_share = phys / tot

    def __repr__(self):
        return (f"<Foresight {self.fights} fights, {self.avg_count:.1f} enemies, hp {self.avg_hp:.0f}, def {self.avg_def:.0f}/{self.avg_mdef:.0f}, "
                f"boss hp {self.boss_hp}, physical share {self.phys_share:.0%}>")


def command_value(abil, ab):
    """Worth of learning ability index `ab` (command or passive) for the run."""
    d = abil.d
    if ab >= 0x4E:
        return PASSIVE_VALUE.get(abil.passive_name(ab - 0x4E), 1)
    name = d.command_name(ab)
    if name in GROUP_START:
        lvl = ab - GROUP_START[name]
        vals = LEVEL_VALUE[name]
        return vals[min(lvl, len(vals) - 1)]
    return COMMAND_VALUE.get(name, 1)


class Planner:
    """Chooses the job of every role slot and gives the jobs to the members.  `probe(job)` returns {member index: facts} for every member
    wearing that job (facts: hp, mp, str, vit, mag, agi, atk, castable {spell id: mp}); `states` gives job levels / ABP."""
    def __init__(self, data, abil, switcher, unlocks=None):
        self.d, self.abil, self.js = data, abil, switcher
        self.unlocks = unlocks if unlocks is not None else load_unlocks()
        self.last_plan = None

    # ---- ABP funnel
    def learn_value(self, wram, char, job, horizon=3):
        """Value per ABP of what the member would learn next in `job`, over the next `horizon` abilities (0 when mastered)."""
        job = self.d.job_id(job)
        lst = self.abil.job_list(job)
        lvl, abp = self.js.job_state(wram, char).get(job, (0, 0))
        known = self.abil.known(wram, char)
        total = 0.0; first = True
        for k in range(lvl, min(len(lst), lvl + horizon)):
            cost, ab = lst[k]
            if ab in known: continue                       # already learned through another job: the ABP is still needed to pass it
            need = max(1, cost - (abp if first else 0)); first = False
            total += command_value(self.abil, ab) / need
        return total

    def progress(self, wram, char, job):
        lvl, abp = self.js.job_state(wram, char).get(self.d.job_id(job), (0, 0))
        return lvl * 1000 + abp

    # ---- fit of a job for a role from the member's real numbers
    def fit(self, role, job, facts, fs):
        base = FIT.get(job, {}).get(role, 0)
        if not base: return 0.0
        if role == "nuker":
            best = 0
            for sp, mp in facts.get("castable", {}).items():
                if sp in facts.get("attack_power", {}):
                    pw = facts["attack_power"][sp]
                    aoe = fs.avg_count if facts["attack_aoe"].get(sp) else 1.0
                    best = max(best, pw * (1 + 0.5 * (min(aoe, 3) - 1)))
            # no attack spell yet: the job is only a nuker on paper
            return base * min(1.0, best / 60.0) if best else base * 0.25
        if role == "healer":
            heals = facts.get("heal", 0)
            return base * (1.0 if heals else 0.3)
        if role == "support":
            return base * (0.5 + 0.5 * min(1.0, facts.get("buffs", 0) / 2.0))
        return float(base)          # front: the job's stats decide below

    def plan(self, wram, names, avail, fs, probe, current, comfort=0.0, hard=False, avoid=()):
        """-> {member name: job}.  `names` are the in-party members (slot index = SLOT order), `current` their current jobs, `comfort` in
        [0, 1]: how much slack the last fights left (1 = effortless); `hard` ignores the funnel; `avoid` is a set of (role, job) not to pick."""
        roles = list(ROLES[:len(names)]) if len(names) < 4 else list(ROLES)
        jobs = [j for j in avail if j in FIT]
        facts = {j: probe(j) for j in jobs}
        lam = 0.0 if hard else (0.2 + 0.6 * comfort) * (0.5 if fs.bosses else 1.0)
        # score of member m in job j for a role: what the job does for the role now (the member's own numbers) plus the funnel, i.e. the value
        # per ABP of what this member would learn next in the job, relative to the best that any member/job offers for this role
        F, L = {}, {}
        for role in roles:
            F[role], L[role] = {}, {}
            raw = {}
            for j in jobs:
                if (role, j) in avoid or not facts[j] or not FIT[j].get(role): continue
                for m, f in facts[j].items():
                    fit = self.fit(role, j, f, fs)
                    if role == "front": fit *= 0.6 + 0.4 * min(1.0, (f["str"] + f["vit"]) / 100.0)
                    raw[(m, j)] = (fit / 10.0, self.learn_value(wram, m, j))
            top_lv = max((v[1] for v in raw.values()), default=0.0)
            for k, (fit, lv) in raw.items():
                F[role][k] = fit; L[role][k] = lv / top_lv if top_lv > 0 else 0.0
        # every way to give the roles to members (with each one's best job) -> (fit total, funnel total, picks)
        options = []
        for perm in itertools.permutations(range(len(names))):         # perm[k] = the member of role k
            for weight in (0.0, lam):
                taken = []; fit_t = lv_t = 0.0; pick = []
                for k, role in enumerate(roles):
                    m = perm[k]
                    opts = sorted(((F[role][(mm, j)] + weight * L[role][(mm, j)] - (0.15 if j in taken else 0.0) + (0.05 if current[m] == j else 0.0), j)
                                   for (mm, j) in F[role] if mm == m), reverse=True)
                    if not opts: break
                    j = opts[0][1]; fit_t += F[role][(m, j)]; lv_t += L[role][(m, j)]; taken.append(j); pick.append(j)
                else:
                    options.append((fit_t, lv_t, perm, pick))
        top_fit = max(o[0] for o in options)
        floor = top_fit if hard else top_fit * (0.97 - 0.12 * comfort)       # the funnel may cost a little strength when the last fights were easy
        ok = [o for o in options if o[0] >= floor] or options
        fit_t, lv_t, perm, combo = max(ok, key=lambda o: o[0] + lam * o[1])
        scores = {r: F[r].get((perm[k], combo[k]), 0.0) for k, r in enumerate(roles)}
        plan = {names[perm[k]]: combo[k] for k in range(len(roles))}
        self.last_plan = dict(roles=dict(zip(roles, combo)), plan=plan, scores=scores, fit=round(fit_t, 2), best_fit=round(top_fit, 2), funnel=round(lv_t, 2))
        return plan
