# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build.

Produces two single-file executables in dist/:

  speedtest.exe         the desktop window, no console
  speedtest-logger.exe  the background logger, no console either

The measurement engine is the official Ookla client, which is NOT bundled: it
stays a separate binary so that it is not redistributed. Place it in a tools
directory next to the executables, or point OOKLA_CLI_PATH at it.

Both are console-less. That is why subprocess calls in speedtest_logger pass
CREATE_NO_WINDOW and why diagnostics go through logging instead of print.

Build with:  pyinstaller speedtest.spec --noconfirm
"""

COMMON = {
    "pathex": ["."],
    "binaries": [],
    "datas": [],
    "hiddenimports": [],
    "hookspath": [],
    "hooksconfig": {},
    "runtime_hooks": [],
    "excludes": [],
    "noarchive": False,
    "optimize": 0,
}

EXE_COMMON = {
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": True,
    "upx_exclude": [],
    "runtime_tmpdir": None,
    "console": False,
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}

gui_analysis = Analysis(["gui.py"], **COMMON)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    gui_analysis.binaries,
    gui_analysis.datas,
    [],
    name="speedtest",
    **EXE_COMMON,
)

logger_analysis = Analysis(["speedtest2.py"], **COMMON)
logger_pyz = PYZ(logger_analysis.pure)
logger_exe = EXE(
    logger_pyz,
    logger_analysis.scripts,
    logger_analysis.binaries,
    logger_analysis.datas,
    [],
    name="speedtest-logger",
    **EXE_COMMON,
)
