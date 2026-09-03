from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import shutil


# Post-hoc results live in the GROUP folder, beside the tree and the tables
# they describe — not in a posthoc/ subfolder of it.
#
# The subfolder bought nothing. It held no inputs, it grouped no set of files a
# user thinks about separately, and the results pane already flattened it back
# out for display ("posthoc/kdp.png"), so its only lasting effect was one more
# level to click through on disk for a matrix that belongs with the alignment it
# was computed from. Runs made before this change keep theirs; every lookup
# below checks the group folder first and falls back to the old location, so an
# existing comparison still shows and still reports its results.
LEGACY_SUBDIR = "posthoc"


def output_path(group_dir: Path, rel: str) -> Path:
    """Where one output lives: current layout first, then the legacy subfolder.

    Returns the current-layout path when neither exists, so a caller reporting
    "not there yet" names the place it is going to appear.
    """
    direct = group_dir / rel
    if direct.exists():
        return direct
    legacy = group_dir / LEGACY_SUBDIR / rel
    if legacy.exists():
        return legacy
    return direct


@dataclass(frozen=True)
class PosthocTool:
    tool_id: str
    label: str
    description: str
    requires: List[str]
    # The files a finished run leaves behind — the ones a user opens, and the
    # only ones "has results" may be decided from. stats_file is deliberately
    # NOT among them: it is written on the failure path too, so counting it as
    # an output made a job that died on its first step report itself finished
    # with nothing to show.
    outputs: List[str]
    stats_file: str = "stats.json"


TOOLS: Dict[str, PosthocTool] = {
    "snp_analysis": PosthocTool(
        tool_id="snp_analysis",
        label="SNP Analysis",
        description="SNP distance matrix, KDP, and closest-neighbor plots",
        requires=["snp-dists"],
        outputs=[
            "snp_matrix.csv",
            "kdp.pdf",
            "kdp.png",
            "closest_neighbor.pdf",
            "closest_neighbor.png",
        ],
    )
}


def list_tools() -> List[PosthocTool]:
    return list(TOOLS.values())


def get_tool(tool_id: str) -> Optional[PosthocTool]:
    return TOOLS.get(tool_id)


def _resolve_requirement(req: str, tool_bin: str) -> bool:
    if tool_bin:
        candidate = Path(tool_bin) / req
        if candidate.exists():
            return True
    return shutil.which(req) is not None


def tool_status(tool: PosthocTool, tool_bin: str = "") -> Dict[str, object]:
    req_status = {req: _resolve_requirement(req, tool_bin) for req in tool.requires}
    missing = [req for req, ok in req_status.items() if not ok]
    return {
        "available": len(missing) == 0,
        "requirements": req_status,
        "missing": missing,
    }
