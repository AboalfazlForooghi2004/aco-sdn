.PHONY: test offline controller topology live clean

PYTHON ?= python
RYU_MANAGER ?= ryu-manager

test:
	$(PYTHON) -m unittest discover -s tests -v

offline:
	$(PYTHON) -m experiments.run \
		--output results/offline_comparison.csv

controller:
	$(RYU_MANAGER) --observe-links controller/main.py

topology:
	sudo $(PYTHON) -m topology.mininet_topology

live:
	sudo $(PYTHON) -m experiments.live_runner \
		--output results/live_comparison.csv

clean:
	sudo mn -c
