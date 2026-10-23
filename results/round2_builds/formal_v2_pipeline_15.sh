#!/usr/bin/env bash
set -euo pipefail
trap '' HUP
python3 scripts/resume_round2.py results/round2_formal_v2 --takeover-pid 47031 --jobs 15 > results/round2_builds/formal_v2_resume.log 2>&1
HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_round2.py results/round2_formal_v2 --jobs 15 > results/round2_builds/formal_v2_audit.log 2>&1
python3 scripts/analyze_round2.py results/round2_formal_v2 --output results/round2_analysis > results/round2_builds/formal_v2_analysis.log 2>&1
python3 - <<'ARCHIVE'
from pathlib import Path
import subprocess
folders=[str(p) for p in sorted(Path('results/round2_formal_v2').iterdir()) if (p/'raw').is_dir()]
with Path('results/round2_builds/formal_v2_archives.log').open('w') as log:
    subprocess.run(['python3','scripts/package_results.py',*folders],stdout=log,stderr=subprocess.STDOUT,check=True)
Path('results/round2_builds/formal_v2_pipeline.complete').write_text('sampling, audit, analysis and archives completed\n')
ARCHIVE
