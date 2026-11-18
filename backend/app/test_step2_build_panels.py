"""Step 2 Build brings the ticked reference databases into the comparison set.

From PR 14. The page used to post import-vcfs for the ticked databases right
after Build; a caller of the API alone then had Run refuse every panel name as
"not in the comparison set". Build now copies them itself.

Pinned here:
  * an enabled database tagged with the project's reference is copied in;
  * a disabled one, one tagged with another reference, and an untagged one
    are not (the importer asks before it mixes references);
  * a name already in the set is kept: a project sample wins over a panel copy;
  * the answer reports how many came in (panel_copied).

    env/bin/python backend/app/test_step2_build_panels.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="step2_build_panels_")).resolve()
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "xdg")
os.environ["VSNP_GUI_SITE_ROOT"] = str(_TMP / "site")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as m  # noqa: E402

REF = "Mycobacterium_AF2122"
FAILED = 0


def check(label, cond):
    global FAILED
    print(f"  {'OK ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILED += 1


def vcf(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("##fileformat=VCFv4.2\n" + text)


def main() -> int:
    locs = _TMP / "refs"
    (locs / REF).mkdir(parents=True)
    (locs / REF / f"{REF}.fasta").write_text(">NC_002945.4\nACGT\n")
    dbs = _TMP / "dbs"
    vcf(dbs / "on" / "PANEL-1_zc.vcf")
    vcf(dbs / "on" / "PANEL-2_zc.vcf")
    vcf(dbs / "on" / "TX-0001_zc.vcf", "##panel copy\n")
    vcf(dbs / "off" / "OFF-1_zc.vcf")
    vcf(dbs / "other" / "OTHER-1_zc.vcf")
    vcf(dbs / "untagged" / "UNTAGGED-1_zc.vcf")
    projects = _TMP / "projects"
    project = projects / "p"
    (project / "step1").mkdir(parents=True)
    db = project / "step2" / "vcf_database"
    vcf(db / "TX-0001_zc.vcf", "##project sample\n")
    m.update_project_meta(project, {"name": "p", "reference": REF})
    cfg = {
        "projects_root": str(projects),
        "reference_locations": [str(locs)],
        "vcf_db_folders": [
            {"path": str(dbs / "on"), "enabled": True, "reference": REF, "name": "on"},
            {"path": str(dbs / "off"), "enabled": False, "reference": REF, "name": "off"},
            {"path": str(dbs / "other"), "enabled": True, "reference": "Brucella_suis1", "name": "other"},
            {"path": str(dbs / "untagged"), "enabled": True, "name": "untagged"},
        ],
        "vcf_db_folders_root": str(_TMP / "no_shared_root"),
    }
    saved = m.load_config
    m.load_config = lambda: dict(cfg)
    try:
        res = m.step2_setup("p")
        names = sorted(p.name for p in db.glob("*_zc.vcf"))
        check("enabled database for this reference copied in",
              "PANEL-1_zc.vcf" in names and "PANEL-2_zc.vcf" in names)
        check("disabled, other-reference and untagged databases left out",
              not {"OFF-1_zc.vcf", "OTHER-1_zc.vcf", "UNTAGGED-1_zc.vcf"} & set(names))
        check("a project sample wins over a panel copy of the same name",
              "##project sample" in (db / "TX-0001_zc.vcf").read_text())
        check("panel_copied counts what came in", res.get("panel_copied") == 2)
        res2 = m.step2_setup("p")
        check("a second Build copies nothing new", res2.get("panel_copied") == 0)
    finally:
        m.load_config = saved
        shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll Step 2 Build panel tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
