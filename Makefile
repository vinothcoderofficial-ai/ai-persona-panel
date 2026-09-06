.PHONY: setup seed validate dev api web test test-py test-web gen-types eval collect readme-gif sample-video clean

setup:
	python -m pip install -r requirements.txt
	npm install
	python scripts/copy_mediapipe_assets.py
	python scripts/make_seed_data.py
	python scripts/validate_data.py

seed:
	python scripts/make_seed_data.py

validate:
	python scripts/validate_data.py

gen-types:
	npm run gen:types
	python scripts/gen_schemas.py

api:
	uvicorn api.app.main:app --reload --port 8000

web:
	npm run dev

dev:
	@echo "Run 'make api' in one terminal and 'make web' in another."

test: test-py test-web

test-py:
	pytest

test-web:
	npm test

eval:
	python scripts/eval.py

# The collection loop in one command: export what the live database holds into
# the committed corpus, then regenerate RESULTS.md from it. Deliberately not a
# button on a screen - it writes committed evidence, and that should be a
# deliberate act at a terminal. `#/home` reports whether it is needed.
collect:
	python scripts/anonymise_sessions.py
	python scripts/eval.py

# Deliberately separate from `eval`: analytics/report.py would write this
# figure's name into RESULTS.md, and CI fails if RESULTS.md moves a byte.
readme-gif:
	python scripts/make_readme_gif.py

# A minute of aisle to point #/vision at, because there is no shelf footage in
# this repository and none can be invented. It is a rendering of the seed
# planogram, not a photograph of a shelf, and the difference is real: even
# lighting, no perspective, no occlusion, no shopper's arm. The output is
# gitignored - it regenerates in seconds, and committing a video to prove a
# pipeline works on the repository's own drawing is the wrong evidence to keep.
sample-video:
	python scripts/make_vision_fixture.py --seconds 60 --out data/vision/demo_aisle_60s.mp4

clean:
	rm -f shoppertwin.db
	rm -rf .pytest_cache __pycache__
