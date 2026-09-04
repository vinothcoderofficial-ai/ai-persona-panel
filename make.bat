@echo off
REM Windows helper. Usage: make.bat setup ^| seed ^| validate ^| api ^| web ^| test ^| eval
if "%1"=="setup" (
    python -m pip install -r requirements.txt
    npm install
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
echo Unknown target: %1
echo Targets: setup seed validate gen-types api web test eval
