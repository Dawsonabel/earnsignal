PY ?= .venv/bin/python
ENV = set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: setup init collect backtest score daily dashboard status test schedule unschedule

setup:            ## create .venv and install dependencies
	python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip -q
	.venv/bin/pip install -r requirements.txt -q
	@[ -f .env ] || (cp .env.example .env && echo "Created .env - put your name and email in EARNSIGNAL_CONTACT")

init:             ## create the database, load the universe and seed the research log
	$(ENV) $(PY) -m earnsignal.pipeline init

collect:          ## full collection: SEC history, prices, consensus, Wikipedia, jobs, apps, insiders, options
	$(ENV) $(PY) -m earnsignal.pipeline collect

backtest:         ## point-in-time event panel, out-of-sample evaluation, options scenarios
	$(ENV) $(PY) -m earnsignal.pipeline backtest

score:            ## rank upcoming announcements and grade past rankings
	$(ENV) $(PY) -m earnsignal.pipeline score

daily:            ## what the scheduler runs: incremental collect + score (+ Saturday backtest)
	$(ENV) $(PY) -m earnsignal.pipeline daily

dashboard:        ## open the dashboard at http://localhost:8501
	$(ENV) .venv/bin/streamlit run dashboard/app.py

status:
	$(ENV) $(PY) -m earnsignal.pipeline status

test:             ## unit, leakage and end-to-end tests on synthetic data
	$(PY) -m pytest -q

schedule:         ## install the macOS launchd job (daily 05:30 local)
	bash scripts/install_schedule.sh

unschedule:
	launchctl unload ~/Library/LaunchAgents/com.earnsignal.daily.plist 2>/dev/null || true
	rm -f ~/Library/LaunchAgents/com.earnsignal.daily.plist
