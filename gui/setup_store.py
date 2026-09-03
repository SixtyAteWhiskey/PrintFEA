"""Persist/reload PrintFEA wizard setups inside the active FreeCAD document."""

import json
from datetime import datetime
import FreeCAD as App


SETUP_SCHEMA_VERSION = 1


def saved_setup_objects(doc=None):
    doc = doc or App.ActiveDocument
    if doc is None:
        return []
    out = []
    for obj in getattr(doc, "Objects", []):
        try:
            if hasattr(obj, "PrintFEASetupSchema") and int(obj.PrintFEASetupSchema) >= 1 and hasattr(obj, "SetupJSON"):
                out.append(obj)
        except Exception:
            pass
    out.sort(key=lambda o: str(getattr(o, "SavedAt", "")), reverse=True)
    return out


def save_setup(doc, name, payload):
    if doc is None:
        raise ValueError("No active FreeCAD document.")
    name = (name or "Setup").strip() or "Setup"
    obj = doc.addObject("App::FeaturePython", "PrintFEA_Setup")
    obj.Label = f"PrintFEA Setup — {name}"
    obj.addProperty("App::PropertyInteger", "PrintFEASetupSchema", "PrintFEA", "PrintFEA saved-setup schema version")
    obj.addProperty("App::PropertyString", "SetupName", "PrintFEA", "User-facing setup name")
    obj.addProperty("App::PropertyString", "SavedAt", "PrintFEA", "Local time when setup was saved")
    obj.addProperty("App::PropertyString", "ModelObjectName", "PrintFEA", "FreeCAD object name used by the setup")
    obj.addProperty("App::PropertyString", "SetupJSON", "PrintFEA", "Serialized PrintFEA setup")
    obj.PrintFEASetupSchema = SETUP_SCHEMA_VERSION
    obj.SetupName = name
    obj.SavedAt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    obj.ModelObjectName = str(payload.get("model_name") or "")
    obj.SetupJSON = json.dumps(payload, sort_keys=True)
    try:
        obj.ViewObject.Visibility = False
    except Exception:
        pass
    doc.recompute()
    return obj


def load_payload(obj):
    if obj is None or not hasattr(obj, "SetupJSON"):
        raise ValueError("That object is not a saved PrintFEA setup.")
    data = json.loads(str(obj.SetupJSON))
    if int(data.get("schema", 0)) != SETUP_SCHEMA_VERSION:
        raise ValueError(f"Unsupported PrintFEA setup schema {data.get('schema')!r}.")
    return data
