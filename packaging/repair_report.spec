# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the Windows .exe.

IMPORTANT: PyInstaller does not cross-compile. This spec MUST be run ON
Windows (10/11, x64) with a matching Python 3.11+ install and
`pip install -r requirements.txt pyinstaller` -- running it on Linux/macOS
produces a Linux/macOS binary, not a Windows .exe. See packaging/BUILD.md.

Usage (from the repo root, on Windows):
    pyinstaller packaging/repair_report.spec --distpath dist --workpath build

Result: dist/RepairReportApp/RepairReportApp.exe (onedir build -- see
BUILD.md for why this project uses onedir rather than onefile).
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("../repair_report/config", "repair_report/config"),
    ("../repair_report/render/templates", "repair_report/render/templates"),
]
datas += collect_data_files("weasyprint")

hiddenimports = []
hiddenimports += collect_submodules("weasyprint")
hiddenimports += ["PySide6.QtSvg"]  # matplotlib/Qt occasionally need this pulled in explicitly

a = Analysis(
    ["../main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RepairReportApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # --noconsole / windowed app, per spec §4
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="app_icon.ico",  # uncomment once a real icon is supplied
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RepairReportApp",
)
