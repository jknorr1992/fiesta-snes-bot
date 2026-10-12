"""Job commands for the battle policy (Steal, Jump, Throw, Chakra, Mix, Gil Toss ...): plugin interface.

Every command is a small class registered with @register.  The policy asks the plugins of the commands the acting character really has in its
four command slots (CharStruct +0x16..0x19, command ids; names from data.command_name):

    kind "support"  asked right after revive / heal, before buffs and attacks: returns a proposal only when the command is clearly needed
                    (a status to cure, a fallen ally, a buff worth its turn ...).
    kind "offense"  asked when no attack spell was chosen: competes with a plain Fight, value = expected damage (or equivalent) of the action.

propose(sim, char, me, mons) -> None | (value, Command).  The highest value wins; an offense proposal must also beat `policy.fight_value(...)`.
A command listed in policy.forbidden is never asked (the user forbids Mix and Gil Toss for now: they are so strong that they would distort
the training; they are fully supported and switched on with `--allow-commands "Mix,Gil Toss"`).

Veteran-knowledge rule (user, 2026-10-12): formulas and their minimum / maximum may be used, never the next roll."""
FORBIDDEN_DEFAULT = ("Mix", "Gil Toss")
REGISTRY = []


def register(cls):
    REGISTRY.append(cls)
    return cls


class JobCommand:
    name = ""
    kind = "offense"

    def __init__(self, policy):
        self.p = policy
        self.d = policy.d

    def propose(self, sim, char, me, mons):
        return None


def commands_of(sim, me):
    """Command names in the acting character's four slots (passives and blanks left out)."""
    return {sim.d.command_name(c) if hasattr(sim, "d") else c for c in sim.w[me.b + 0x16:me.b + 0x1A] if c and not c & 0x80}
