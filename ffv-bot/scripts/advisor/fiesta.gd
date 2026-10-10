class_name FiestaConfig
extends RefCounted
## Four Job Fiesta settings: four jobs (manual list or random draw at the story points that allow a change).
## Job ids match data/jobs.json.

var enabled := false
var randomize := false
var jobs: Array[int] = []   # manual choice, one per Fiesta slot
var rng := RandomNumberGenerator.new()


## Jobs the party may use at a job-select goal. Randomizing draws once and keeps the result.
func allowed_jobs() -> Array[int]:
	if not enabled:
		return []
	if randomize and jobs.is_empty():
		var pool: Array[int] = []
		for i in 21:  # every job except Freelancer
			pool.append(i)
		pool.shuffle()
		jobs = pool.slice(0, 4)
	return jobs


func allows(job_id: int) -> bool:
	return not enabled or job_id in allowed_jobs()
