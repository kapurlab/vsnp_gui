"""The Kraken target names in the vSNP GUI are the Kraken ID Parse GUI's list.

Both GUIs show one "Target taxon" list: the names that ship in
kraken_id_parse_gui/config/taxa.yaml plus the names users save, kept in
~/.config/kraken_id_parse_gui/target_names.yaml — outside both checkouts, so
saving a name no longer dirties the Kraken checkout (which made `bdtools
update` refuse to move it) and works on a site install whose checkout the user
cannot write.

Pinned here:
  - kraken_target_names.py is byte-identical to the sibling checkout's
    backend/app/target_names.py (skipped when no sibling is installed);
  - a name saved through this GUI's endpoint lands in the shared saved file,
    leaves the shipped file alone, and is what the list endpoint returns;
  - a shipped name cannot be removed; a saved one can, notes kept;
  - report-taxa lists the names in the project's Kraken reports.

Run directly:  PYTHONPATH=<checkout>/backend <env>/bin/python backend/app/test_kraken_target_names.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

TMP = Path(tempfile.mkdtemp(prefix="vsnp_ktn_"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "xdg")   # before app.main reads any config

from app import main as M  # noqa: E402
from app import kraken_target_names as K  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def main() -> int:
    print("[the copy matches Kraken ID Parse's module]")
    sibling = M._KRAKEN_GUI_ROOT / "backend" / "app" / "target_names.py"
    if sibling.is_file():
        check("kraken_target_names.py == kraken_id_parse_gui/backend/app/target_names.py",
              sibling.read_bytes() == (HERE / "kraken_target_names.py").read_bytes())
    else:
        print(f"  SKIP  no sibling checkout at {sibling.parent}")

    print("[save / list / remove through this GUI's endpoints]")
    root = TMP / "kraken_id_parse_gui"
    (root / "config").mkdir(parents=True)
    shipped = root / "config" / "taxa.yaml"
    shipped.write_text("# shipped\n- Mycobacterium tuberculosis complex\n- Orbivirus\n", encoding="utf-8")
    before = shipped.read_bytes()
    M._KRAKEN_GUI_ROOT = root

    d = M.kraken_taxa_add(M.KrakenTaxonPayload(name="Brucella abortus"))
    saved = TMP / "xdg" / "kraken_id_parse_gui" / "target_names.yaml"
    check("the name is saved in the shared per-user file", saved.is_file() and "- Brucella abortus" in saved.read_text())
    check("the shipped list is not touched", shipped.read_bytes() == before)
    check("the list shows shipped then saved",
          M.kraken_taxa()["taxa"] == ["Mycobacterium tuberculosis complex", "Orbivirus", "Brucella abortus"]
          and [e["source"] for e in d["entries"]] == ["shipped", "shipped", "saved"])
    check("a duplicate is not added", M.kraken_taxa_add(M.KrakenTaxonPayload(name="orbivirus"))["added"] is False)
    saved.write_text(saved.read_text() + "# keep until the season ends\n", encoding="utf-8")
    try:
        M.kraken_taxa_remove(M.KrakenTaxonPayload(name="Orbivirus"))
        check("a shipped name cannot be removed", False)
    except HTTPException as exc:
        check("a shipped name cannot be removed", exc.status_code == 400)
    M.kraken_taxa_remove(M.KrakenTaxonPayload(name="Brucella abortus"))
    check("a saved name can be removed, the note kept",
          "Brucella" not in saved.read_text() and "# keep until the season ends" in saved.read_text())

    print("[names from the project's Kraken reports]")
    proj = TMP / "projects" / "p1"
    kd = proj / "kraken" / "S1" / "kraken"
    kd.mkdir(parents=True)
    (kd / "S1_reportkraken.txt").write_text(
        " 97.00\t970\t0\tR\t1\troot\n"
        " 95.00\t950\t950\tG1\t77643\t        Mycobacterium tuberculosis complex\n"
        "  1.00\t10\t10\tS\t9606\t      Homo sapiens\n", encoding="utf-8")
    real_dir_for = M._project_dir_for
    M._project_dir_for = lambda cfg, name: proj
    try:
        r = M.kraken_report_taxa("p1")
    finally:
        M._project_dir_for = real_dir_for
    check("report names come back best first, with the sample that showed them",
          [x["name"] for x in r["names"]] == ["Mycobacterium tuberculosis complex", "Homo sapiens"]
          and r["names"][0]["best_sample"] == "S1")

    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
