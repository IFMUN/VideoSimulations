# The research loop, in the order you should actually run it.
CONFIG ?= configs/quick.yaml
PY     ?= python3

.PHONY: help install test data signals backtest ablation ablation-robust stress sweep walkforward leaderboard research clean

help:           ## list the available targets
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}' 

install:        ## install dependencies
	$(PY) -m pip install -r requirements.txt

test:           ## run the test suite
	$(PY) -m pytest tests/ -q

data:           ## load the panel and describe the investable universe
	$(PY) -m equity_lab.cli data -c $(CONFIG)

signals:        ## information coefficients, before any portfolio construction
	$(PY) -m equity_lab.cli signals -c $(CONFIG) --all

backtest:       ## one backtest, with a self-contained HTML report
	$(PY) -m equity_lab.cli backtest -c $(CONFIG)

ablation:       ## disable each asymmetry in turn and measure what breaks
	$(PY) -m equity_lab.cli ablation -c $(CONFIG)

ablation-robust: ## repeat the ablation on independent panels; report sign consistency
	$(PY) -m equity_lab.cli ablation -c $(CONFIG) --seeds 11,23,41

stress:         ## vary the generative market; see which mechanisms track the hazard
	$(PY) -m equity_lab.cli stress -c $(CONFIG)

sweep:          ## grid search, with the cost of searching accounted for
	$(PY) -m equity_lab.cli sweep -c $(CONFIG) -g configs/grid_asymmetry.yaml \
		--sensitivity portfolio.asymmetry.long_convexity

walkforward:    ## purged, embargoed out-of-sample validation
	$(PY) -m equity_lab.cli walkforward -c $(CONFIG)

leaderboard:    ## compare every run written to disk
	$(PY) -m equity_lab.cli leaderboard

research: data signals backtest ablation stress sweep walkforward leaderboard  ## the whole loop

clean:          ## remove caches and run artifacts
	rm -rf .cache runs .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
