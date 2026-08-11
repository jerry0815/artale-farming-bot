@echo off
:start
python auto_train.py
if %errorlevel% neq 0 (
    echo Python script crashed. Restarting in 5 seconds...
    timeout /t 5 /nobreak
    goto start
)
echo Python script finished successfully.
pause