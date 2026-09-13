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
# (ctypes.util.find_library), not a normal Python import -- PyInstaller
# can't see that dependency on its own. See the identical, longer comment
# in repair_report_onefile.spec; same fix here (set GTK_RUNTIME_BIN before
# running PyInstaller -- packaging/BUILD.md).
binaries = []
gtk_bin = os.environ.get("GTK_RUNTIME_BIN")
if gtk_bin and os.path.isdir(gtk_bin):
    binaries += [(dll, ".") for dll in glob.glob(os.path.join(gtk_bin, "*.dll"))]
    print(f"[repair_report.spec] Bundling {len(binaries)} GTK runtime DLL(s) from {gtk_bin}")
else:
    print(
        "[repair_report.spec] WARNING: GTK_RUNTIME_BIN not set/found -- "
        "PDF export (WeasyPrint) will likely fail on a machine without the "
        "GTK3 runtime installed separately. See packaging/BUILD.md."
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
