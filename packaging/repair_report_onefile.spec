# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for a SINGLE-FILE Windows .exe.

IMPORTANT: PyInstaller does not cross-compile. This spec MUST be run ON
Windows (10/11, x64) with a matching Python 3.11+ install and
`pip install -r requirements.txt pyinstaller` -- running it on Linux/macOS
produces a Linux/macOS binary, not a Windows .exe. See packaging/BUILD.md.

This is the --onefile counterpart of repair_report.spec (onedir). Use this
one when a single distributable .exe is a hard requirement (e.g. emailing
one file, or a USB-stick handoff) -- see BUILD.md §3 for the trade-offs
(slower first launch: everything unpacks into a temp dir on every run) and
what to double check (WeasyPrint/Cairo native-library loading, which is the
most likely thing to behave differently in onefile mode vs. onedir).

Usage (from the repo root, on Windows):
    pyinstaller packaging/repair_report_onefile.spec --distpath dist --workpath build

Result: dist/RepairReportApp.exe -- ONE file, nothing else needed next to
it (it self-extracts to a temp dir on each launch and cleans up after).
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
    a.binaries,
    a.datas,
    [],
    name="RepairReportApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # --noconsole / windowed app, per spec §4
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="app_icon.ico",  # uncomment once a real icon is supplied
)
