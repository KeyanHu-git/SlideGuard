# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

PROJECT_ROOT = Path(SPEC).resolve().parents[1]
datas = collect_data_files("slideguard", includes=["resources/*.ps1", "schemas/*.json"])
for distribution in ("CairoSVG", "jsonschema", "lxml", "numpy", "Pillow", "pypdf", "scikit-image"):
    datas += copy_metadata(distribution)

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "desktop_worker_entry.py")],
    pathex=[str(PROJECT_ROOT / "src")], binaries=[], datas=datas,
    hiddenimports=collect_submodules("skimage.metrics"),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6", "PyQt6", "PyQt5", "shiboken6", "tkinter", "pytest", "IPython",
        "matplotlib", "pandas", "torch", "tensorflow", "cv2", "numpy.tests", "scipy.tests"],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="slideguard-worker",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="worker")
