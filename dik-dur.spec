# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules

datas = [('models', 'models')]
binaries = []
hiddenimports = []
datas += collect_data_files('mediapipe')
datas += collect_data_files('customtkinter')
binaries += collect_dynamic_libs('mediapipe')
hiddenimports += collect_submodules('mediapipe.tasks')


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'transformers', 'scipy', 'pandas', 'jax', 'tensorflow', 'sounddevice', 'hf_xet', 'huggingface_hub'],
    noarchive=False,
    optimize=0,
)
# kamera DirectShow ile açılıyor; OpenCV'nin 30 MB'lık ffmpeg video dll'i gereksiz
a.binaries = [b for b in a.binaries if 'opencv_videoio_ffmpeg' not in b[0]]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dik-dur',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/icon.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='dik-dur',
)
