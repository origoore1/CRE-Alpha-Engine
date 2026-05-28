"""
rent_roll_parser.py  —  Shim for test_engine.py compatibility
==============================================================
test_engine.py uses `import rent_roll_parser as rrp`.
The active implementation lives in `rent_roll_parser 7.py` (space in name).
This shim loads v7 via importlib and re-exports everything.
"""
import importlib.util
import pathlib
import sys

_path = pathlib.Path(__file__).parent / "rent_roll_parser 7.py"
_spec = importlib.util.spec_from_file_location("rent_roll_parser_v7", _path)
_mod  = importlib.util.module_from_spec(_spec)
sys.modules["rent_roll_parser_v7"] = _mod
_spec.loader.exec_module(_mod)

# Re-export all public names into this module's namespace
globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__")})
