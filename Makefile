.PHONY: setup seed validate dev api web test test-py test-web gen-types eval readme-gif clean

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

# Deliberately separate from `eval`: analytics/report.py would write this
# figure's name into RESULTS.md, and CI fails if RESULTS.md moves a byte.
readme-gif:
	python scripts/make_readme_gif.py

clean:
	rm -f shoppertwin.db
	rm -rf .pytest_cache __pycache__
