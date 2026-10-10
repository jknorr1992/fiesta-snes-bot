class_name FFVAdvisor
extends RefCounted
## Picks the next goal from data/route.json given the live game state. Goals the player has already
## satisfied (by RAM condition or manual confirmation) are skipped, so ignoring advice or racing ahead
## just changes the answer. `lookahead(n)` returns the following goals as an expandable tree.

var goals: Array = []
var confirmed := {}        # goal id -> true, set by the player for goals with no RAM condition
var fiesta := FiestaConfig.new()


func load_route(path := "res://data/route.json") -> bool:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return false
	var data = JSON.parse_string(f.get_as_text())
	if typeof(data) != TYPE_DICTIONARY:
		return false
	goals = data.get("goals", [])
	return true


func is_done(goal: Dictionary, state: FFVGameState) -> bool:
	if confirmed.get(goal.id, false):
		return true
	var w: Dictionary = goal.get("when", {})
	if w.is_empty():
		return false
	if w.has("min_gil") and state.gil < int(w.min_gil):
		return false
	if w.has("min_level_all"):
		for m in state.party:
			if int(m.level) < int(w.min_level_all):
				return false
	return true


## Later goals satisfied by RAM also complete everything they need (prerequisites are implied).
func _done_set(state: FFVGameState) -> Dictionary:
	var done := {}
	for g in goals:
		if is_done(g, state):
			done[g.id] = true
	var changed := true
	while changed:
		changed = false
		for g in goals:
			if done.has(g.id):
				for n in g.get("needs", []):
					if not done.has(n):
						done[n] = true
						changed = true
	return done


func pending(state: FFVGameState) -> Array:
	var done := _done_set(state)
	return goals.filter(func(g): return not done.has(g.id))


func next_goal(state: FFVGameState):
	var p := pending(state)
	return p[0] if not p.is_empty() else null


func lookahead(state: FFVGameState, n := 3) -> Array:
	return pending(state).slice(1, 1 + n)


func describe(state: FFVGameState) -> String:
	var g = next_goal(state)
	if g == null:
		return "Route complete."
	var s := "Next: %s%s" % [g.title, "" if g.get("verified", false) else " (unverified)"]
	if g.get("job_select", false) and fiesta.enabled:
		var names: Array[String] = []
		for j in fiesta.allowed_jobs():
			names.append(FFVGameState.job_name(j))
		s += "\n  Fiesta jobs: " + ", ".join(names)
	if g.get("safety", "") != "":
		s += "\n  Note: " + g.safety
	for l in lookahead(state):
		s += "\n  then: " + l.title
	return s
