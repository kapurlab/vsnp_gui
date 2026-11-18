# VCF Lite Pack (MTBC0)

This folder contains a small set of ZC VCFs for trying Step 2 without running Step 1.

Reference: **MTBC0_v1** (use `mtbc0_v1.1` in the GUI)

To use it: create a project with reference `mtbc0_v1.1`, then in Step 2 → **Build the comparison set** → box 2 → **More options**, add this folder's full path. It appears as **vcf_lite (8 samples)**; press **Build comparison set**, then **Run**. Remove it afterwards with the **x** next to it, or it stays ticked in every `mtbc0_v1.1` project. Step-by-step: [docs/QUICKSTART.md](../../docs/QUICKSTART.md), Part 3.

Included samples:
- ERR2704680 (L1_human_India)
- ERR2704686 (L5_human_Ghana)
- ERR2704693 (L3_human_Afghanistan)
- ERR2704702 (L2_human_China)
- ERR2704709 (L4_human_Germany)
- SRR1791695 (bovis_bovine_USA)
- SRR5642711 (orygis_human_USA)
- SRR7617662 (caprae_goat_New_Zealand)

All files are `_zc.vcf` and are suitable for Step 2 testing and demo runs.
