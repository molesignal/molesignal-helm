CHART := charts/molesignal

# Never let a helm call reach a real cluster from here: nothing in this repository needs one.
export KUBECONFIG := /dev/null

.PHONY: help check lint test mutate docs docs-check package clean

help:
	@echo "make check       everything CI runs without a cluster: lint, docs-check, test, mutate"
	@echo "make lint        helm lint --strict for the defaults, every ci/ and examples/ values file"
	@echo "make test        render the chart for a matrix of values and check the manifests"
	@echo "make mutate      break the chart on purpose and check that the tests notice"
	@echo "make docs        regenerate the values table of the chart README"
	@echo "make docs-check  fail if that table is out of date"
	@echo "make package     helm package into ./dist"

check: lint docs-check test mutate

lint:
	helm lint $(CHART) --strict
	@for f in $(CHART)/ci/*.yaml examples/*.yaml; do \
	  echo "==> $$f"; helm lint $(CHART) --strict --values $$f || exit 1; \
	done

test:
	python3 tests/run.py

mutate:
	python3 tests/mutate.py

docs:
	python3 scripts/gen-values-doc.py

docs-check:
	python3 scripts/gen-values-doc.py --check

package:
	helm package $(CHART) --destination dist

clean:
	rm -rf dist .cr-release-packages .cr-index
