extends SceneTree
## Offline: feeds fake states to the advisor and checks it re-plans.

func _initialize() -> void:
	var adv := FFVAdvisor.new()
	assert(adv.load_route())
	var s := FFVGameState.new()
	s.gil = 500
	s.party = [{"level": 1}, {"level": 1}, {"level": 1}, {"level": 3}]
	print(adv.describe(s))
	adv.confirmed["meteor_site"] = true
	adv.confirmed["first_battle"] = true
	print("---\n", adv.describe(s))
	s.gil = 1500
	s.party = [{"level": 5}, {"level": 5}, {"level": 6}, {"level": 7}]
	print("--- ahead of the route:\n", adv.describe(s))
	adv.fiesta.enabled = true
	adv.fiesta.randomize = true
	adv.confirmed.clear()
	adv.confirmed["first_battle"] = true
	s.gil = 500
	s.party = [{"level": 1}]
	print("---\n", adv.describe(s))
	var c := FFVAdvisor.new()
	c.load_route()
	var t := FFVGameState.new()
	t.map_id = 122
	t.party = [{"level": 1}]
	c.next_goal(t)
	t.map_id = 1
	print("--- left crater, latched:\n", c.describe(t))
	quit()
