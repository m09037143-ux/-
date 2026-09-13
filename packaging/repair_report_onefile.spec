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
import glob
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("../repair_report/config", "repair_report/config"),
    ("../repair_report/render/templates", "repair_report/render/templates"),
]
datas += collect_data_files("weasyprint")

hiddenimports = []
hiddenimports += collect_submodules("weasyprint")
hiddenimports += ["PySide6.QtSvg"]  # matplotlib/Qt occasionally need this pulled in explicitly

# WeasyPrint loads Pango/GObject/HarfBuzz/FontConfig via ctypes at RUNTIME
# (ctypes.util.find_library), not a normal Python import -- PyInstaller's
# static analysis can't see that dependency and won't bundle it on its
# own. Without this, the built .exe fails on a clean machine with
# "cannot load library 'libgobject-2.0-0'" even though it built and ran
# fine on the machine that has GTK installed. Set GTK_RUNTIME_BIN to the
# GTK3 runtime's bin/ directory (see packaging/BUILD.md) before running
# PyInstaller to bundle its DLLs into the .exe itself, so an end user's
# machine doesn't need GTK installed separately. The GitHub Actions
# workflow (.github/workflows/build-windows-exe.yml) sets this
# automatically; building by hand on Windows needs it set manually.
binaries = []
gtk_bin = os.environ.get("GTK_RUNTIME_BIN")
if gtk_bin and os.path.isdir(gtk_bin):
    binaries += [(dll, ".") for dll in glob.glob(os.path.join(gtk_bin, "*.dll"))]
    print(f"[repair_report_onefile.spec] Bundling {len(binaries)} GTK runtime DLL(s) from {gtk_bin}")
else:
    print(
        "[repair_report_onefile.spec] WARNING: GTK_RUNTIME_BIN not set/found -- "
        "PDF export (WeasyPrint) in the built .exe will likely fail on a machine "
        "without the GTK3 runtime installed separately. See packaging/BUILD.md."
    )

a = Analysis(
    ["../main.py"],
    pathex=[],
    binaries=binaries,
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
