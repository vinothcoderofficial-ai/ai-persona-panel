@echo off
REM Windows helper. Usage: make.bat setup ^| seed ^| validate ^| api ^| web ^| test ^| eval ^| collect ^| readme-gif
if "%1"=="setup" (
    python -m pip install -r requirements.txt
    npm install
    python scripts\copy_mediapipe_assets.py
    python scripts\make_seed_data.py
    python scripts\validate_data.py
    goto :eof
)
if "%1"=="seed" ( python scripts\make_seed_data.py & goto :eof )
if "%1"=="validate" ( python scripts\validate_data.py & goto :eof )
if "%1"=="gen-types" (
    npm run gen:types
    python scripts\gen_schemas.py
    goto :eof
)
if "%1"=="api" ( uvicorn api.app.main:app --reload --port 8000 & goto :eof )
if "%1"=="web" ( npm run dev & goto :eof )
if "%1"=="test" ( pytest & npm test & goto :eof )
if "%1"=="eval" ( python scripts\eval.py & goto :eof )
REM The collection loop in one command: export the live database into the
REM committed corpus, then regenerate RESULTS.md from it. Deliberately a
REM terminal command and not a button - it writes committed evidence.
if "%1"=="collect" (
    python scripts\anonymise_sessions.py
    python scripts\eval.py
    goto :eof
)
if "%1"=="readme-gif" ( python scripts\make_readme_gif.py & goto :eof )
REM A minute of aisle to point #/vision at. A rendering of the seed
REM planogram, not footage of a shelf; the output is gitignored.
if "%1"=="sample-video" (
    python scripts\make_vision_fixture.py --seconds 60 --out data\vision\demo_aisle_60s.mp4
    goto :eof
)
echo Unknown target: %1
echo Targets: setup seed validate gen-types api web test eval collect readme-gif sample-video
