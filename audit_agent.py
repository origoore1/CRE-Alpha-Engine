"""
audit_agent.py  —  GRU Platform Static Audit Agent
====================================================
Checks every Python file in the folder for:
  1. Version mismatches (wrong parser file imported)
  2. Hardcoded threshold values that should come from constants.py
  3. Duplicate logic across files
  4. Import path mismatches
  5. Unused imports / dead assignments (basic)

Then applies safe fixes, runs pytest, and writes audit_report_YYYYMMDD.txt.

Usage:
    python audit_agent.py
"""

import ast
import os
import re
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
REPORT_PATH = ROOT / f"audit_report_{date.today().strftime('%Y%m%d')}.txt"

# Primary files to audit (ignore old/copy files)
IGNORE_PATTERNS = [
    "עותק",          # Hebrew "copy"
    " - ",           # dash-copy naming
    "rent_roll_parser (", # numbered copies
    "app.py",        # old app (not "app 7.py")
    "dd_scanner.py",
    "deal_converter.py",
    "practice_deals 8 -",
    "rent_roll_parser.py",   # old parser — detected separately
    "__pycache__",
    "audit_agent.py",
]

def should_audit(path: Path) -> bool:
    name = path.name
    for pat in IGNORE_PATTERNS:
        if pat in name:
            return False
    return path.suffix == ".py"

def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

# ── Constants from constants.py ─────────────────────────────────────────────
# These are the numeric values that SHOULD always be referenced via constants.py
THRESHOLD_MAP = {
    0.65:  "LTV_PASS",
    0.70:  "LTV_WARN",
    1.30:  "DSCR_PASS",
    1.20:  "DSCR_WARN",
    1.75:  "ICR_PASS",
    1.50:  "ICR_WARN",
    4.0:   "WAULT_PASS",
    2.5:   "WAULT_WARN",
    0.10:  "VACANCY_PASS",
    0.15:  "VACANCY_WARN",
    0.055: "GIY_PASS",
    0.045: "GIY_WARN",
    0.075: "DY_PASS",
    0.060: "DY_WARN",
    20.0:  "VOID_HOLD_RATE",
    0.63:  "LTV_REPAIR_TARGET",
}

# Patterns that are legitimate uses of the raw number (display strings, comments, sliders)
THRESHOLD_ALLOWLIST_PATTERNS = [
    r"#",                # comment
    r'f".*{',            # inside f-string (display)
    r"st\.slider",       # slider default value
    r"st\.metric",       # metric display
    r"\.format\(",       # format string
    r'"[^"]*\b(1\.30|1\.20|0\.65|1\.75)\b[^"]*"',  # inside a string literal
    r"'[^']*\b(1\.30|1\.20|0\.65|1\.75)\b[^']*'",
]

# ── Issues collector ─────────────────────────────────────────────────────────
issues   = []   # list of dicts: {file, line, type, description, fix_applied, fix_desc}
fixes    = []   # list of applied fixes

def add_issue(file, line, itype, description, fix_applied=False, fix_desc=""):
    issues.append({
        "file":        str(file.name),
        "line":        line,
        "type":        itype,
        "description": description,
        "fix_applied": fix_applied,
        "fix_desc":    fix_desc,
    })

# ════════════════════════════════════════════════════════════════════════════
# CHECK 1 — Version mismatch: app imports rent_roll_parser (old) instead of
#           rent_roll_parser 7 (current, with all fixes).
# ════════════════════════════════════════════════════════════════════════════
def check_parser_version(files):
    app_file = ROOT / "app 7.py"
    if not app_file.exists():
        return
    src = read_file(app_file)

    if "import rent_roll_parser as rrp" in src and "rent_roll_parser 7.py" not in src[:500]:
        # Confirm the 7 version is not loaded via importlib in the import block
        import_block = src[:2000]
        if "rent_roll_parser 7" not in import_block:
            add_issue(
                app_file, 28, "VERSION_MISMATCH",
                "app 7.py line 28: `import rent_roll_parser as rrp` loads "
                "rent_roll_parser.py (old file — missing WAULT break-option fix and "
                "all other improvements). Should load rent_roll_parser 7.py via importlib.",
                fix_applied=True,
                fix_desc="Replaced `import rent_roll_parser as rrp` with importlib "
                         "loading of rent_roll_parser 7.py (same pattern as dd_scanner 7.py).",
            )
            _fix_parser_import(app_file, src)

def _fix_parser_import(app_file: Path, src: str):
    """Replace direct import with importlib-based load of rent_roll_parser 7.py."""
    old_import = "import rent_roll_parser as rrp\n"

    # Build the importlib block that mirrors the dd_scanner pattern
    new_block = (
        "# rent_roll_parser 7.py has a space — load via importlib (same as dd_scanner).\n"
        "_rrp_spec = _ilu.spec_from_file_location(\n"
        '    "rent_roll_parser",\n'
        '    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "rent_roll_parser 7.py"),\n'
        ")\n"
        "rrp = _ilu.module_from_spec(_rrp_spec)\n"
        "_rrp_spec.loader.exec_module(rrp)\n"
        "del _rrp_spec\n"
    )

    if old_import not in src:
        return  # already fixed or pattern changed

    # The existing code deletes _ilu and _os at line 45 after loading dd_scanner.
    # We must insert the rrp block BEFORE that del statement.
    del_line = "del _ilu, _os, _dd_spec, _dd_mod\n"
    if del_line not in src:
        return

    new_src = src.replace(old_import, "")           # remove the bare import
    new_src = new_src.replace(del_line,
        new_block + del_line.replace(
            "del _ilu, _os, _dd_spec, _dd_mod",
            "del _ilu, _os, _dd_spec, _dd_mod, _rrp_spec"
        ).replace("del _rrp_spec\n" + del_line.replace(
            "del _ilu, _os, _dd_spec, _dd_mod",
            "del _ilu, _os, _dd_spec, _dd_mod, _rrp_spec"
        ), "")
    )

    # Simpler, safer approach: just insert new_block before the del line
    # and remove the bare import
    new_src = src.replace(old_import, "")
    new_src = new_src.replace(
        del_line,
        new_block +
        "del _ilu, _os, _dd_spec, _dd_mod\n"
    )

    app_file.write_text(new_src, encoding="utf-8")
    fixes.append(f"FIXED [{app_file.name}]: Replaced `import rent_roll_parser as rrp` "
                 f"with importlib load of 'rent_roll_parser 7.py'")

# ════════════════════════════════════════════════════════════════════════════
# CHECK 2 — Hardcoded threshold values in non-constants files
# ════════════════════════════════════════════════════════════════════════════
SKIP_FILES_FOR_HARDCODE = {"constants.py", "test_engine.py"}

# Threshold literals to detect: only pure numeric comparisons, not in strings
HARDCODE_PATTERNS = [
    # comparisons against known thresholds
    (r"[<>]=?\s*1\.20\b",   "DSCR_WARN",  1.20),
    (r"[<>]=?\s*1\.30\b",   "DSCR_PASS",  1.30),
    (r"[<>]=?\s*1\.75\b",   "ICR_PASS",   1.75),
    (r"[<>]=?\s*1\.50\b",   "ICR_WARN",   1.50),
    (r"[<>]=?\s*0\.65\b",   "LTV_PASS",   0.65),
    (r"[<>]=?\s*0\.70\b",   "LTV_WARN",   0.70),
    (r"/ 0\.65\b",          "LTV_PASS",   0.65),   # exit_val = balance / 0.65
    (r"\* 20\.0\b",         "VOID_HOLD_RATE", 20.0),
]

def check_hardcoded_thresholds(files):
    for fpath in files:
        if fpath.name in SKIP_FILES_FOR_HARDCODE:
            continue
        src   = read_file(fpath)
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern, const_name, val in HARDCODE_PATTERNS:
                if re.search(pattern, line):
                    # Skip if line is part of a string / f-string display
                    if re.search(r'f"[^"]*' + re.escape(str(val)), line):
                        continue
                    if "constants" in line or const_name in line:
                        continue
                    add_issue(
                        fpath, i, "HARDCODED_THRESHOLD",
                        f"Literal `{val}` used directly — should reference `{const_name}` from constants.py",
                    )

# ════════════════════════════════════════════════════════════════════════════
# CHECK 3 — Duplicate WAULT logic (calculation appears outside parser)
# ════════════════════════════════════════════════════════════════════════════
WAULT_PATTERN = r"annual_rent.*unexpired_yrs|unexpired_yrs.*annual_rent"

def check_duplicate_wault(files):
    parser_file = "rent_roll_parser 7.py"
    for fpath in files:
        if fpath.name == parser_file:
            continue
        src = read_file(fpath)
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(WAULT_PATTERN, line) and "# WAULT" not in line:
                add_issue(
                    fpath, i, "DUPLICATE_LOGIC",
                    f"WAULT calculation (`annual_rent * unexpired_yrs`) duplicated outside "
                    f"rent_roll_parser 7.py. Consider calling compute_metrics() instead.",
                )

# ════════════════════════════════════════════════════════════════════════════
# CHECK 4 — Import mismatches: files importing old versions
# ════════════════════════════════════════════════════════════════════════════
OLD_FILE_IMPORTS = [
    (r"\bimport rent_roll_parser\b(?! as rrp\b)", "rent_roll_parser.py", "rent_roll_parser 7.py"),
    (r"\bimport dd_scanner\b(?! 7)", "dd_scanner.py", "dd_scanner 7.py"),
]

def check_import_mismatches(files):
    for fpath in files:
        src = read_file(fpath)
        for pat, old, new in OLD_FILE_IMPORTS:
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(pat, line) and "importlib" not in line:
                    add_issue(
                        fpath, i, "IMPORT_MISMATCH",
                        f"Imports `{old}` (old version) — should use `{new}`",
                    )

# ════════════════════════════════════════════════════════════════════════════
# CHECK 5 — Old/copy files that exist alongside current versions
# ════════════════════════════════════════════════════════════════════════════
OLD_FILES_EXPECTED = [
    "rent_roll_parser.py",
    "rent_roll_parser (1).py",
    "rent_roll_parser (2).py",
    "rent_roll_parser (3).py",
    "app.py",
    "dd_scanner.py",
]

def check_stale_files():
    for fname in OLD_FILES_EXPECTED:
        p = ROOT / fname
        if p.exists():
            add_issue(
                p, 0, "STALE_FILE",
                f"Old/copy file '{fname}' exists alongside current version. "
                f"Risk of accidental import. (Not auto-deleted — manual review required.)",
            )

# ════════════════════════════════════════════════════════════════════════════
# CHECK 6 — Functions called with wrong argument count (spot-check key fns)
# ════════════════════════════════════════════════════════════════════════════
def check_function_signatures(files):
    """
    Spot-check that run_qa_audit and generate_verdict are called with
    the right number of args everywhere.
    """
    # Determine actual signatures from rent_roll_parser 7.py
    parser_path = ROOT / "rent_roll_parser 7.py"
    if not parser_path.exists():
        return
    parser_src = read_file(parser_path)

    sig_map = {}
    for fn in ["run_qa_audit", "generate_verdict", "compute_metrics"]:
        m = re.search(rf"^def {fn}\((.*?)\):", parser_src, re.MULTILINE | re.DOTALL)
        if m:
            params = [p.strip().split(":")[0].split("=")[0].strip()
                      for p in m.group(1).split(",") if p.strip()]
            params = [p for p in params if p and p != "self"]
            sig_map[fn] = len(params)

    for fpath in files:
        if fpath.name == "rent_roll_parser 7.py":
            continue
        src = read_file(fpath)
        for fn, expected_argc in sig_map.items():
            for i, line in enumerate(src.splitlines(), 1):
                # Find calls like fn(...)
                m = re.search(rf"\b{fn}\s*\(([^)]*)\)", line)
                if m:
                    args_str = m.group(1).strip()
                    if not args_str:
                        argc = 0
                    else:
                        # rough arg count by comma splitting (not foolproof)
                        argc = len(args_str.split(","))
                    if argc != expected_argc and args_str:
                        add_issue(
                            fpath, i, "SIGNATURE_MISMATCH",
                            f"`{fn}()` called with ~{argc} args but signature expects "
                            f"{expected_argc} params. Verify manually.",
                        )

# ════════════════════════════════════════════════════════════════════════════
# CHECK 7 — Unused imports (basic: imported name never used in file body)
# ════════════════════════════════════════════════════════════════════════════
def check_unused_imports(files):
    skip = {"app 7.py"}  # too large / complex for simple scan
    for fpath in files:
        if fpath.name in skip:
            continue
        src = read_file(fpath)
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            m = re.match(r"^import\s+(\S+)(?:\s+as\s+(\S+))?", line.strip())
            if m:
                mod   = m.group(1)
                alias = m.group(2) or mod.split(".")[0]
                body  = "\n".join(lines[i:])  # rest of file after this import
                if alias not in body and mod not in body:
                    add_issue(
                        fpath, i, "UNUSED_IMPORT",
                        f"Import `{line.strip()}` — name `{alias}` not used in file body.",
                    )

# ════════════════════════════════════════════════════════════════════════════
# RUN ALL CHECKS
# ════════════════════════════════════════════════════════════════════════════
def run_all_checks():
    all_py = sorted(ROOT.glob("*.py"))
    audited = [f for f in all_py if should_audit(f)]

    print(f"Auditing {len(audited)} files...")
    for f in audited:
        print(f"  {f.name}")

    check_parser_version(audited)        # Fix 1 (auto-fix)
    check_hardcoded_thresholds(audited)  # Report only
    check_duplicate_wault(audited)       # Report only
    check_import_mismatches(audited)     # Report only (version fix above covers app 7.py)
    check_stale_files()                  # Report only
    check_function_signatures(audited)   # Report only
    check_unused_imports(audited)        # Report only

    return audited

# ════════════════════════════════════════════════════════════════════════════
# VERIFY: run pytest
# ════════════════════════════════════════════════════════════════════════════
def run_pytest():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_engine.py", "-q", "--tb=short"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout + result.stderr, result.returncode

# ════════════════════════════════════════════════════════════════════════════
# REPORT
# ════════════════════════════════════════════════════════════════════════════
def write_report(audited_files, pytest_output, pytest_rc):
    lines = []
    w = lines.append

    w("=" * 70)
    w(f"GRU PLATFORM AUDIT REPORT — {date.today().isoformat()}")
    w("=" * 70)

    w(f"\nFILES AUDITED ({len(audited_files)})")
    w("-" * 40)
    for f in audited_files:
        w(f"  {f.name}")

    # Group issues by type
    by_type = {}
    for iss in issues:
        by_type.setdefault(iss["type"], []).append(iss)

    w(f"\n\nISSUES FOUND ({len(issues)} total)")
    w("=" * 70)

    type_order = [
        "VERSION_MISMATCH",
        "IMPORT_MISMATCH",
        "STALE_FILE",
        "HARDCODED_THRESHOLD",
        "DUPLICATE_LOGIC",
        "SIGNATURE_MISMATCH",
        "UNUSED_IMPORT",
    ]
    type_labels = {
        "VERSION_MISMATCH":    "🔴 VERSION MISMATCHES",
        "IMPORT_MISMATCH":     "🔴 IMPORT MISMATCHES",
        "STALE_FILE":          "🟡 STALE FILES",
        "HARDCODED_THRESHOLD": "🟡 HARDCODED THRESHOLDS",
        "DUPLICATE_LOGIC":     "🟡 DUPLICATE LOGIC",
        "SIGNATURE_MISMATCH":  "🟠 SIGNATURE MISMATCHES",
        "UNUSED_IMPORT":       "⚪ UNUSED IMPORTS",
    }

    for t in type_order:
        group = by_type.get(t, [])
        if not group:
            continue
        w(f"\n{type_labels.get(t, t)} ({len(group)})")
        w("-" * 60)
        for iss in group:
            loc = f"{iss['file']}:{iss['line']}" if iss['line'] else iss['file']
            w(f"  [{loc}]")
            for part in textwrap.wrap(iss['description'], width=64, subsequent_indent="    "):
                w(f"    {part}")
            if iss['fix_applied']:
                w(f"    → FIX APPLIED: {iss['fix_desc']}")

    w(f"\n\nFIXES APPLIED ({len(fixes)})")
    w("=" * 70)
    if fixes:
        for f in fixes:
            w(f"  • {f}")
    else:
        w("  None")

    w(f"\n\nPYTEST RESULT")
    w("=" * 70)
    for line in pytest_output.strip().splitlines():
        w(f"  {line}")
    w(f"\n  Exit code: {pytest_rc} ({'PASS' if pytest_rc == 0 else 'FAIL'})")

    w("\n" + "=" * 70)
    w("END OF REPORT")
    w("=" * 70)

    report_text = "\n".join(lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    return report_text

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    print("GRU Audit Agent starting...\n")

    audited = run_all_checks()

    print(f"\nFound {len(issues)} issue(s). Fixes applied: {len(fixes)}")

    if fixes:
        print("\nRunning syntax check on modified files...")
        import subprocess as _sp
        for fpath in [ROOT / "app 7.py"]:
            if fpath.exists():
                r = _sp.run([sys.executable, "-m", "py_compile", str(fpath)],
                            capture_output=True, text=True)
                status = "OK" if r.returncode == 0 else f"ERROR: {r.stderr}"
                print(f"  {fpath.name}: {status}")

    print("\nRunning pytest...")
    pytest_out, pytest_rc = run_pytest()

    report = write_report(audited, pytest_out, pytest_rc)

    print("\n" + "=" * 60)
    print(report)
    print(f"\nReport saved to: {REPORT_PATH.name}")
