import json
from datetime import datetime, timezone
from pathlib import Path

def write(findings, path: Path):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "finding_count": len(findings),
        "findings": findings,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
