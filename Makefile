PYTHON ?= python3

.PHONY: run test check firmware
run:
	PYTHONPATH=companion $(PYTHON) -m respaw

test:
	PYTHONPATH=companion $(PYTHON) -m unittest discover -s tests -v

check: test
	node --check companion/respaw/web/app.js
	git diff --check

firmware:
	sh tools/arduino.sh compile --profile mega mega2560/source/respaw-v2
