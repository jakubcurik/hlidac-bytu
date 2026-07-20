@echo off
REM Spouštěč hlídače nájemních bytů (Windows).
REM Použij v Plánovači úloh — postará se o správný adresář i virtuální prostředí.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python main.py %*
