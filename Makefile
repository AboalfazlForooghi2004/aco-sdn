.PHONY: test offline preflight controller topology live clean

PYTHON ?= python
RYU_MANAGER ?= ryu-manager

test:
	$(PYTHON) -m unittest discover -s tests -v

offline:
	$(PYTHON) -m experiments.run \
		--output results/offline_comparison.csv

preflight:
	$(PYTHON) scripts/lab_preflight.py

controller:
	$(RYU_MANAGER) --observe-links controller/main.py

topology:
	sudo $(PYTHON) -m topology.mininet_topology

live:
	$(PYTHON) scripts/lab_preflight.py
	sudo $(PYTHON) -m experiments.live_runner \
		--output results/live_comparison.csv

clean:
	sudo mn -c
