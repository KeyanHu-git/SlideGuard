# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


PROJECT_ROOT = Path(SPEC).resolve().parents[1]

datas = collect_data_files(
    "slideguard",
    includes=["resources/*.ps1", "schemas/*.json"],
)
for distribution in (
    "CairoSVG",
    "jsonschema",
    "lxml",
    "numpy",
    "Pillow",
    "pypdf",
    "PySide6",
    "scikit-image",
):
    datas += copy_metadata(distribution)
hiddenimports = [
    "slideguard.gui",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    *collect_submodules("skimage.metrics"),
]

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "portable_entry.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "IPython", "matplotlib.tests", "numpy.tests", "scipy.tests"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SlideGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    name="SlideGuard",
)
