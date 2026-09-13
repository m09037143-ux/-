"""Entry point for both `python main.py` (dev) and the PyInstaller build."""
import os
import sys

if sys.platform == "win32":
    # WeasyPrint's native libraries (Pango/GObject/HarfBuzz/FontConfig) are
    # loaded via ctypes/cffi at runtime, not a normal Python import. Since
    # Python 3.8, ctypes-based DLL loading on Windows no longer searches
    # PATH-listed directories for a loaded DLL's OWN dependencies (bpo-36085)
    # -- os.add_dll_directory() must be called explicitly, or WeasyPrint
    # silently can't load its dependency chain even when every DLL it needs
    # is sitting right there (this produced "cannot load library
    # 'libgobject-2.0-0': error 0x7e" both for a locally-installed GTK/MSYS2
    # runtime AND for the DLLs PyInstaller bundles into the .exe -- the two
    # cases are the same bug, just with a different DLL directory). Widen
    # the search unconditionally, before anything imports weasyprint:
    #   - when frozen (the built .exe), the bundled DLLs land at the root
    #     of the onefile self-extraction directory, sys._MEIPASS;
    #   - when running from source, packaging/BUILD.md's GTK_RUNTIME_BIN
    #     env var points at a local GTK/MSYS2 install for the same purpose.
    # See docs/REVERSE_ENGINEERING.md and packaging/BUILD.md for the full
    # story (several earlier, incomplete attempts at this exact fix).
    _dll_dir = getattr(sys, "_MEIPASS", None) or os.environ.get("GTK_RUNTIME_BIN")
    if _dll_dir:
        try:
            os.add_dll_directory(_dll_dir)
        except OSError:
            pass  # directory doesn't exist / already added -- not fatal here
        # WeasyPrint also reads this env var itself as a belt-and-suspenders
        # measure (some code paths re-resolve libraries lazily rather than
        # only at the initial import).
        os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", _dll_dir)

from repair_report.ui.app import main

if __name__ == "__main__":
    main()
