"""KAMP-776: average powermetrics runs (one file per variant) into a comparison table.

Usage: python3 -I summarize_powermetrics.py /tmp/kamp-776
Handles Intel (package W, GPU Busy %) and Apple Silicon (CPU/GPU/Combined mW) output.
"""

import re
import statistics
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "pkg_W": re.compile(r"package power \(CPUs\+GT\+SA\): ([\d.]+)W"),
    "cpu_mW": re.compile(r"^CPU Power: ([\d.]+) mW", re.M),
    "gpu_mW": re.compile(r"^GPU Power: ([\d.]+) mW", re.M),
    "combined_mW": re.compile(
        r"^Combined Power \(CPU \+ GPU \+ ANE\): ([\d.]+) mW", re.M
    ),
    "cores_active_%": re.compile(r"^Cores Active: ([\d.]+)%", re.M),
    "avg_cores": re.compile(r"^Avg Num of Cores Active: ([\d.]+)", re.M),
    "gpu_active_%": re.compile(r"^GPU Active: ([\d.]+)%", re.M),
    "gpu_busy_%": re.compile(r"GPU Busy\s*: ([\d.]+)%"),
    "gpu_hw_active_%": re.compile(r"^GPU HW active residency:\s+([\d.]+)%", re.M),
}


def summarize(path: Path) -> dict[str, tuple[float, float, int]]:
    text = path.read_text(errors="replace")
    out: dict[str, tuple[float, float, int]] = {}
    for key, pat in PATTERNS.items():
        vals = [float(v) for v in pat.findall(text)]
        if vals:
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            out[key] = (statistics.mean(vals), sd, len(vals))
    return out


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kamp-776")
    files = sorted(root.glob("*.txt"))
    if not files:
        print(f"no .txt files in {root}")
        return
    results = {f.stem: summarize(f) for f in files}
    keys = [k for k in PATTERNS if any(k in r for r in results.values())]
    print("| variant | n | " + " | ".join(keys) + " |")
    print("|---|---|" + "---|" * len(keys))
    for name, r in results.items():
        n = max((v[2] for v in r.values()), default=0)
        cells = [f"{r[k][0]:.2f} ± {r[k][1]:.2f}" if k in r else "-" for k in keys]
        print(f"| {name} | {n} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
