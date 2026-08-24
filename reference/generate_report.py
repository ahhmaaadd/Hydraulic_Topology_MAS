"""Produce the sized-solution certificates for all seven problems."""
from __future__ import annotations
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from hydraulic_mas.sizing.planner import size_problem

ROOT = pathlib.Path(__file__).resolve().parent
data = json.loads((ROOT / "validated_topologies.json").read_text())
out = {}
for pid in sorted(data):
    entry = data[pid]
    result = size_problem(pid, entry["topology"], entry["requirements"])
    out[pid] = {
        "sizing": {k: v for k, v in result.sizing.items() if v},
        "throttle_k": result.throttle_k,
        "certificate": result.certificate.to_dict(),
        "repairs": result.repairs,
        "notes": result.notes,
    }
(ROOT / "sized_certificates.json").write_text(json.dumps(out, indent=1, default=str))
print("wrote", ROOT / "sized_certificates.json")
for pid, value in out.items():
    print(pid, value["certificate"]["verdict"], value["certificate"]["counts"])
