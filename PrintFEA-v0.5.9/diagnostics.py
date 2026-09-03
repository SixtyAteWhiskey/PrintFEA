"""Lightweight PrintFEA diagnostics/logging helpers."""

import os
import traceback
from datetime import datetime
import FreeCAD as App

try:
    from version import __version__ as PRINTFEA_VERSION
except Exception:
    PRINTFEA_VERSION = "unknown"


def diagnostics_dir():
    base = App.getUserAppDataDir()
    path = os.path.join(base, "PrintFEA")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def log_path():
    return os.path.join(diagnostics_dir(), "printfea.log")


def _write(level, message):
    text = str(message or "").rstrip()
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {level}: {text}\n"
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        if level == "ERROR":
            App.Console.PrintError("PrintFEA: " + text + "\n")
        elif level == "WARNING":
            App.Console.PrintWarning("PrintFEA: " + text + "\n")
        else:
            App.Console.PrintMessage("PrintFEA: " + text + "\n")
    except Exception:
        pass


def log_info(message):
    _write("INFO", message)


def log_warning(message):
    _write("WARNING", message)


def log_error(message):
    _write("ERROR", message)


def log_exception(context, exc=None):
    detail = traceback.format_exc()
    if detail.strip() == "NoneType: None" and exc is not None:
        detail = repr(exc)
    _write("ERROR", f"{context}\n{detail}")


def environment_summary():
    version = "unknown"
    try:
        version = ".".join(str(x) for x in App.Version()[:3])
    except Exception:
        pass
    return {
        "printfea_version": PRINTFEA_VERSION,
        "freecad_version": version,
        "user_data_dir": App.getUserAppDataDir(),
        "log_path": log_path(),
    }
