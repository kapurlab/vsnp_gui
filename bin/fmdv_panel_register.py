#!/usr/bin/env python3
"""Turn a reference's panel build into Step 2 databases the GUI lists.

fmdv_panel_build.sh leaves one folder per genome under
lib/panel_build/<ref>/<genome>/alignment_<ref>/<genome>_zc.vcf. Step 2 reads
a database as one folder of VCFs named in the GUI config, so until now the
copying and the config entry were done by hand, which is easy to get wrong:
fmdv_genome_db.py runs its GenBank genomes under the same panel_build folder,
so a plain glob picks those up too and the lineage database silently becomes
a copy of the GenBank one.

This makes two databases per reference and registers both:

  FMDV_all      the 78 other lineage genomes, every serotype, off by default
  <SEROTYPE>    the lineage genomes of the reference's own serotype, on

The panel is defined as the FASTA files in lib/genomes, so a genome that is
not one of the 79 is left out whatever is in panel_build.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("refs", nargs="+", help="reference safe names, e.g. SAT1-I_KM268899")
    ap.add_argument("--lib", default=str(Path.home() / "fmdv_refs"), help="FMDV reference library")
    ap.add_argument("--config", help="GUI config.json (default: the XDG one)")
    ap.add_argument("--no-register", action="store_true", help="copy the VCFs, leave the config alone")
    a = ap.parse_args()

    lib = Path(a.lib).expanduser()
    panel = {Path(f).stem for f in glob.glob(str(lib / "genomes" / "*.fasta"))}
    if not panel:
        raise SystemExit(f"no panel genomes under {lib}/genomes")

    cfg_path = Path(a.config).expanduser() if a.config else (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "vsnp_gui" / "config.json")
    cfg = json.loads(cfg_path.read_text()) if cfg_path.is_file() else {}
    entries = cfg.setdefault("vcf_db_folders", [])
    known = {e["path"] for e in entries if isinstance(e, dict)}

    changed = False
    for ref in a.refs:
        sero = ref.split("-")[0]
        vcfs = sorted(glob.glob(str(lib / "panel_build" / ref / "*" / f"alignment_{ref}" / "*_zc.vcf")))
        vcfs = [v for v in vcfs if Path(v).name[: -len("_zc.vcf")] in panel]
        if len(vcfs) < 10:
            print(f"{ref}: only {len(vcfs)} panel VCFs under panel_build; build them first")
            continue
        for name, same_serotype in (("FMDV_all", False), (sero, True)):
            d = lib / "vcf_db_folders" / ref / name
            d.mkdir(parents=True, exist_ok=True)
            n = 0
            for v in vcfs:
                b = Path(v).name
                if same_serotype and not b.startswith(sero + "-"):
                    continue
                shutil.copyfile(v, d / b)
                n += 1
            print(f"{ref}/{name}: {n} VCFs")
            if not a.no_register and str(d) not in known:
                entries.append({"path": str(d), "reference": ref, "enabled": bool(same_serotype)})
                known.add(str(d))
                changed = True

    if changed:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, indent=1, sort_keys=True))
        print(f"registered in {cfg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
