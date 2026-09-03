import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform


def detect_lineage(name: str) -> str:
    n = name.lower()
    if "caprae" in n:
        return "Caprae"
    if "orygis" in n:
        return "Orygis"
    if "bovis" in n or "bcg" in n:
        return "Bovis"
    if (
        "lineage-01" in n
        or "lineage_01" in n
        or "lineage1" in n
        or "lineage-1" in n
        or "lineage_1" in n
        or n.startswith("l1")
    ):
        return "Lineage 1"
    if (
        "lineage-02" in n
        or "lineage_02" in n
        or "lineage2" in n
        or "lineage-2" in n
        or "lineage_2" in n
        or n.startswith("l2")
    ):
        return "Lineage 2"
    if (
        "lineage-03" in n
        or "lineage_03" in n
        or "lineage3" in n
        or "lineage-3" in n
        or "lineage_3" in n
        or n.startswith("l3")
    ):
        return "Lineage 3"
    if (
        "lineage-04" in n
        or "lineage_04" in n
        or "lineage4" in n
        or "lineage-4" in n
        or "lineage_4" in n
        or n.startswith("l4")
    ):
        return "Lineage 4"
    return "Unknown"


def get_lineage_colors(lineage: str) -> Dict[str, str]:
    if lineage == "Caprae":
        return {"fill": "#ADD8E6", "iqr": "#1E90FF", "median": "red"}
    if lineage == "Orygis":
        return {"fill": "#90EE90", "iqr": "#32CD32", "median": "black"}
    if lineage == "Bovis":
        return {"fill": "#E6E6FA", "iqr": "#9370DB", "median": "red"}
    if lineage == "Lineage 1":
        return {"fill": "#F0E1B3", "iqr": "#E7BD42", "median": "black"}
    if lineage == "Lineage 2":
        return {"fill": "#C1F0C1", "iqr": "#6BCE7D", "median": "black"}
    if lineage == "Lineage 3":
        return {"fill": "#B3D9FF", "iqr": "#4996D5", "median": "black"}
    if lineage == "Lineage 4":
        return {"fill": "#FEBAB3", "iqr": "#E3687C", "median": "black"}
    return {"fill": "#87CEFA", "iqr": "#1E90FF", "median": "black"}


# The filtered alignment this tool writes for a "only samples" run. Results now
# land in the group folder itself, so the working file sits in the same
# directory find_group_fasta scans — and it picks the NEWEST match, which the
# working file always is. Hidden by name, skipped by name, and deleted when the
# run ends: any one of the three would do, and a group's alignment is not the
# thing to be clever about.
FILTERED_FASTA_NAME = ".snp_analysis_input.fasta"


def find_group_fasta(group_dir: Path) -> Optional[Path]:
    candidates = []
    for ext in ("*.fasta", "*.fa", "*.fna"):
        candidates.extend(
            p for p in group_dir.glob(ext)
            if not p.name.startswith(".") and p.name != FILTERED_FASTA_NAME
        )
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1]


def normalize_header(header: str) -> str:
    name = header.strip().split()[0]
    if name.startswith(">"):
        name = name[1:]
    return name


def find_vcf_manifest(start: Path) -> Optional[Path]:
    """Locate the VCF-source manifest by walking up from a group folder.

    This is the "Include: only samples" bug. The caller used to hand over
    ``group_dir.parent`` as the step2 directory, which was true back when
    vsnp3 output sat directly in step2/<group>. Groups have lived under a dated
    run folder for a long time now, so the parent is step2/<run_id>, the
    manifest was looked for at step2/<run_id>/vcf_database/ where it has never
    been, and the allow-list came back empty — every single "only samples" run
    failed with "No step1 samples found in manifest", wrote that into stats.json
    and stopped. Walking up finds step2/ from either layout, and from any
    future one that keeps the database beside the runs.
    """
    for base in (start, *start.parents):
        for name in ("vcf_database", "vcf_source"):
            manifest = base / name / ".vcf_source_manifest.csv"
            if manifest.exists():
                return manifest
    return None


def load_step1_allowlist(step2_dir: Path) -> set:
    manifest = find_vcf_manifest(step2_dir)
    if manifest is None:
        return set()
    allowed = set()
    with manifest.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("source_type") != "step1":
                continue
            filename = (row.get("filename") or "").strip()
            if not filename:
                continue
            if filename.endswith(".gz"):
                allowed.add(filename[:-3])
            allowed.add(filename)
            if "__" in filename:
                tail = filename.split("__", 1)[1]
                allowed.add(tail)
                if tail.endswith(".gz"):
                    allowed.add(tail[:-3])
    return allowed


def filter_fasta_by_headers(input_fasta: Path, output_fasta: Path, allowlist: set) -> int:
    kept = 0
    include = False
    with input_fasta.open("r", encoding="utf-8") as src, output_fasta.open(
        "w", encoding="utf-8"
    ) as out:
        for line in src:
            if line.startswith(">"):
                header = normalize_header(line)
                include = header in allowlist
                if include:
                    kept += 1
                    out.write(line)
            else:
                if include:
                    out.write(line)
    return kept


def run_snp_dists(fasta_path: Path, output_path: Path, snp_dists_path: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out_handle:
        result = subprocess.run(
            [snp_dists_path, str(fasta_path)],
            stdout=out_handle,
            stderr=subprocess.PIPE,
            text=True,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "snp-dists failed")


def read_matrix(tab_path: Path) -> pd.DataFrame:
    df = pd.read_csv(tab_path, sep="\t")
    if df.empty:
        return df
    df = df.set_index(df.columns[0])
    return df


def remove_root(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "root" in df.index:
        df = df.drop(index="root")
    if "root" in df.columns:
        df = df.drop(columns="root")
    return df


def sanitize_matrix(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    if df.empty:
        return df, 0
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    nan_count = int(df.isna().sum().sum())
    if nan_count:
        df = df.fillna(0)
    return df, nan_count


def lower_triangle_values(df: pd.DataFrame) -> np.ndarray:
    if df.empty:
        return np.array([])
    mat = df.to_numpy()
    tri = mat[np.tril_indices_from(mat, k=-1)]
    return tri[np.isfinite(tri)]


def reorder_by_cluster(df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    if df.empty or df.shape[0] < 3:
        return df, list(df.index)
    try:
        condensed = squareform(df.to_numpy(), checks=False)
        tree = linkage(condensed, method="average")
        order_idx = leaves_list(tree)
        labels = list(df.index)
        ordered = [labels[i] for i in order_idx]
        return df.loc[ordered, ordered], ordered
    except Exception:
        return df, list(df.index)


def closest_neighbors(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    # Work on a float copy so the diagonal can be masked with NaN.
    mat = df.astype(float).copy()
    arr = mat.to_numpy(copy=True)
    np.fill_diagonal(arr, np.nan)
    mat.iloc[:, :] = arr
    return mat.min(axis=1, skipna=True)


def compute_xlim(values: np.ndarray) -> Tuple[float, float]:
    if values.size == 0:
        return (0.0, 1.0)
    max_val = float(np.nanmax(values))
    if max_val == 0:
        return (0.0, 1.0)
    p99 = float(np.nanpercentile(values, 99))
    upper = max(max_val, p99) * 1.1
    return (0.0, upper)


def plot_kdp(values: np.ndarray, lineage: str, n_sequences: int, output_prefix: Path) -> Dict[str, str]:
    colors = get_lineage_colors(lineage)
    x_min, x_max = compute_xlim(values)
    fig, ax = plt.subplots(figsize=(11, 8.5))
    kde = gaussian_kde(values)
    xs = np.linspace(x_min, x_max, 512)
    ys = kde(xs)
    ax.plot(xs, ys, color="#2c2c2c", linewidth=1.2)
    ax.fill_between(xs, 0, ys, color=colors["fill"], alpha=0.6)
    q1, q3 = np.quantile(values, [0.25, 0.75])
    mask = (xs >= q1) & (xs <= q3)
    ax.fill_between(xs[mask], 0, ys[mask], color=colors["iqr"], alpha=0.35)
    median_val = float(np.median(values))
    ax.axvline(median_val, color=colors["median"], linewidth=2.0)
    ax.set_xlim(x_min, x_max)
    ax.set_title(f"{lineage} SNP density plot (n = {n_sequences})", fontsize=16, fontweight="bold")
    ax.set_xlabel("SNP distance", fontsize=12, fontweight="bold")
    ax.set_ylabel("Density", fontsize=12, fontweight="bold")
    ax.grid(True, color="#eee")
    stats_text = f"Min: {np.min(values):.0f}\nMax: {np.max(values):.0f}\nMedian: {median_val:.0f}"
    ax.text(x_max * 0.7, max(ys) * 0.8, stats_text, fontsize=11)
    pdf_path = output_prefix.with_suffix(".pdf")
    png_path = output_prefix.with_suffix(".png")
    fig.tight_layout()
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    return {"pdf": str(pdf_path), "png": str(png_path)}


def plot_closest_neighbor(values: pd.Series, lineage: str, output_prefix: Path) -> Dict[str, str]:
    colors = get_lineage_colors(lineage)
    vals = values.dropna().to_numpy()
    x_min, x_max = compute_xlim(vals)
    fig, ax = plt.subplots(figsize=(11, 8.5))
    bins = max(10, int(math.ceil((x_max - x_min) / 25)))
    ax.hist(vals, bins=bins, color=colors["fill"], edgecolor="#2c2c2c", density=True)
    median_val = float(np.median(vals)) if vals.size else 0.0
    ax.axvline(median_val, color=colors["median"], linewidth=2.0)
    ax.set_xlim(x_min, x_max)
    ax.set_title(f"Closest neighbor distances ({lineage})", fontsize=16, fontweight="bold")
    ax.set_xlabel("Pairwise distance", fontsize=12, fontweight="bold")
    ax.set_ylabel("Density", fontsize=12, fontweight="bold")
    stats_text = f"Min: {np.min(vals):.0f}\nMax: {np.max(vals):.0f}\nMedian: {median_val:.0f}"
    ax.text(x_max * 0.7, ax.get_ylim()[1] * 0.8, stats_text, fontsize=11)
    pdf_path = output_prefix.with_suffix(".pdf")
    png_path = output_prefix.with_suffix(".png")
    fig.tight_layout()
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    return {"pdf": str(pdf_path), "png": str(png_path)}


def _discard(path: Optional[Path]) -> None:
    """Remove a working file, if it is there. Results share the group folder
    with the tree and tables now, so anything this tool does not mean a user to
    open has to clean up after itself."""
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def write_stats(path: Path, payload: Dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(group_dir: Path, group_name: str, out_dir: Path, snp_dists_path: str, scope: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    stats_path = out_dir / "stats.json"
    fasta_path = find_group_fasta(group_dir)
    if not fasta_path:
        write_stats(stats_path, {"status": "error", "message": "No FASTA found", "group": group_name})
        return 1
    input_fasta = fasta_path
    filtered_fasta = None
    filtered_count = None
    if scope == "step1_only":
        allowlist = load_step1_allowlist(group_dir)
        if not allowlist:
            write_stats(stats_path, {
                "status": "error",
                "message": (
                    "Could not find the VCF source manifest "
                    "(step2/vcf_database/.vcf_source_manifest.csv), so there is no "
                    "record of which of this group's sequences came from Step 1. "
                    "Re-run with 'Include: samples + reference', or collect the "
                    "VCFs into the project database and try again."
                ),
                "group": group_name,
                "scope": scope,
            })
            return 1
        filtered_fasta = out_dir / FILTERED_FASTA_NAME
        filtered_count = filter_fasta_by_headers(fasta_path, filtered_fasta, allowlist)
        if filtered_count < 2:
            # snp-dists on one sequence produces a 1x1 matrix and no distances
            # at all; say why rather than emitting an empty plot.
            write_stats(stats_path, {
                "status": "error",
                "message": (
                    f"Only {filtered_count} of this group's sequences are Step 1 samples, "
                    "so there is no pair to measure. Re-run with "
                    "'Include: samples + reference'."
                ),
                "group": group_name,
                "scope": scope,
            })
            _discard(filtered_fasta)
            return 1
        input_fasta = filtered_fasta
    tab_path = out_dir / "snp_matrix.tsv"
    run_snp_dists(input_fasta, tab_path, snp_dists_path)
    df = read_matrix(tab_path)
    df = remove_root(df)
    df, nan_count = sanitize_matrix(df)
    df, cluster_order = reorder_by_cluster(df)
    n_sequences = int(df.shape[0])
    df.to_csv(out_dir / "snp_matrix.csv")
    _discard(tab_path)
    _discard(filtered_fasta)
    distances = lower_triangle_values(df)
    lineage = detect_lineage(group_name)
    if distances.size < 3:
        write_stats(
            stats_path,
            {
                "status": "insufficient_data",
                # snp_matrix.csv is written and real; the plots are not, because
                # fewer than three pairwise distances is not a distribution. The
                # pane needs to be told that, or the group looks like a failure.
                "message": (
                    f"{n_sequences} sequence{'' if n_sequences == 1 else 's'} give "
                    f"{int(distances.size)} pairwise distance"
                    f"{'' if distances.size == 1 else 's'} — too few to plot. "
                    "snp_matrix.csv holds the distances."
                ),
                "group": group_name,
                "n_sequences": n_sequences,
                "lineage": lineage,
                "scope": scope,
                "filtered_count": filtered_count,
                "cluster_order": cluster_order,
                "nan_filled": nan_count,
            },
        )
        return 0
    kdp_paths = plot_kdp(distances, lineage, n_sequences, out_dir / "kdp")
    neighbor_vals = closest_neighbors(df)
    neighbor_paths = plot_closest_neighbor(neighbor_vals, lineage, out_dir / "closest_neighbor")
    stats = {
        "status": "ok",
        "group": group_name,
        "lineage": lineage,
        "scope": scope,
        "filtered_count": filtered_count,
        "n_sequences": n_sequences,
        "cluster_order": cluster_order,
        "nan_filled": nan_count,
        "min": float(np.min(distances)),
        "max": float(np.max(distances)),
        "median": float(np.median(distances)),
        "iqr": [float(np.quantile(distances, 0.25)), float(np.quantile(distances, 0.75))],
        "kdp": kdp_paths,
        "closest_neighbor": neighbor_paths,
        "fasta": str(fasta_path),
    }
    write_stats(stats_path, stats)
    return 0


def resolve_snp_dists_path(arg_path: Optional[str]) -> str:
    if arg_path:
        return arg_path
    if shutil.which("snp-dists"):
        return "snp-dists"
    raise RuntimeError("snp-dists not found on PATH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-dir", required=True)
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--snp-dists", dest="snp_dists", default=None)
    parser.add_argument("--scope", default="all", choices=["all", "step1_only"])
    args = parser.parse_args()
    try:
        snp_dists_path = resolve_snp_dists_path(args.snp_dists)
        return run(Path(args.group_dir), args.group_name, Path(args.out_dir), snp_dists_path, args.scope)
    except Exception as exc:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_stats(out_dir / "stats.json", {"status": "error", "message": str(exc), "group": args.group_name})
        return 1


if __name__ == "__main__":
    sys.exit(main())
