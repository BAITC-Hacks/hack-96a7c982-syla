PYTHON ?= python

.PHONY: test eval benchmark report submission verify

test:
	$(PYTHON) -m unittest discover -s tests

eval:
	$(PYTHON) local_eval.py --runs 10

report:
	$(PYTHON) pitch_report.py

submission:
	$(PYTHON) make_submission.py

benchmark:
	git show main:agent.py > /tmp/main_agent.py
	cp agent.py /tmp/experiment_agent.py
	$(PYTHON) benchmark_vs_main.py /tmp/main_agent.py /tmp/experiment_agent.py

verify: test eval report submission
