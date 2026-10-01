@echo off
rem Temiz, sabitlenmis bagimliliklarla exe derler. Cikti: dist\dik-dur\dik-dur.exe
cd /d %~dp0
if not exist .venv-build (
  python -m venv .venv-build || exit /b 1
  rem --no-deps: mediapipe'in istedigi tam OpenCV (contrib) ve ses kutuphanesi gereksiz
  .venv-build\Scripts\python.exe -m pip install --no-deps -r requirements-lock.txt pyinstaller==6.22.3 pyinstaller-hooks-contrib altgraph pefile pywin32-ctypes setuptools || exit /b 1
)
.venv-build\Scripts\python.exe test_posture.py || exit /b 1
.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean dik-dur.spec || exit /b 1
echo Tamam: dist\dik-dur\dik-dur.exe
