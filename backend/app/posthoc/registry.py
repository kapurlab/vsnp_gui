from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import shutil


@dataclass(frozen=True)
class PosthocTool:
    tool_id: str
    label: str
    description: str
    requires: List[str]
    outputs: List[str]


TOOLS: Dict[str, PosthocTool] = {
    "snp_analysis": PosthocTool(
        tool_id="snp_analysis",
        label="SNP Analysis",
        description="SNP distance matrix, KDP, and closest-neighbor plots",
        requires=["snp-dists"],
        outputs=[
            "posthoc/snp_matrix.csv",
            "posthoc/kdp.pdf",
            "posthoc/kdp.png",
            "posthoc/closest_neighbor.pdf",
            "posthoc/closest_neighbor.png",
            "posthoc/stats.json",
        ],
    )
}


def list_tools() -> List[PosthocTool]:
    return list(TOOLS.values())


def get_tool(tool_id: str) -> Optional[PosthocTool]:
    return TOOLS.get(tool_id)


def _resolve_requirement(req: str, vsnp3_path: str) -> bool:
    if vsnp3_path:
        candidate = Path(vsnp3_path) / "bin" / req
        if candidate.exists():
            return True
    return shutil.which(req) is not None


def tool_status(tool: PosthocTool, vsnp3_path: str) -> Dict[str, object]:
    req_status = {req: _resolve_requirement(req, vsnp3_path) for req in tool.requires}
    missing = [req for req, ok in req_status.items() if not ok]
    return {
        "available": len(missing) == 0,
        "requirements": req_status,
        "missing": missing,
    }
