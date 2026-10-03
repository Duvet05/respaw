PYTHON ?= python3

.PHONY: run test check check-link firmware firmware-autonomous
run:
	PYTHONPATH=companion $(PYTHON) -m respaw

test:
	PYTHONPATH=companion $(PYTHON) -m unittest discover -s tests -v

check: test
	node --check companion/respaw/web/app.js
	git diff --check

check-link:
	$(PYTHON) -c 'import websockets; assert websockets.__version__ == "15.0.1"'
	PYTHONPATH=companion $(PYTHON) -m unittest discover -s tests -p test_robot_link.py -v

firmware:
	sh tools/arduino.sh compile --profile mega mega2560/source/respaw-v2

firmware-autonomous:
	sh tools/arduino.sh compile --profile mega mega2560/source/respaw-autonomo
