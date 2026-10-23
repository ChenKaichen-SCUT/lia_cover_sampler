#!/usr/bin/env python3
"""Copy HighDiv's outer source and add only flushed output timestamps."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser()
p.add_argument("source", type=Path)
p.add_argument("z3_prefix", type=Path)
p.add_argument("destination", type=Path)
p.add_argument("--audit", type=Path, required=True)
a = p.parse_args()
source = a.source.resolve() / "HighDiv"
dest = a.destination.resolve()
if dest.exists():
    raise SystemExit("destination exists: refuse to overwrite")
shutil.copytree(source, dest, ignore=shutil.ignore_patterns("build", "highdiv"))
path = dest / "src/sampler/liasampler.cpp"
before = path.read_text()
after = before.replace("namespace sampler {", """namespace sampler {
static const auto lia_audit_process_start = std::chrono::steady_clock::now();
""", 1)
needle = '        samplesFile << "\\n";\n        num_samples++;'
if before.count(needle) != 1:
    raise SystemExit("unexpected frozen source")
after = after.replace(needle, r'''        samplesFile << "\n";
        samplesFile.flush();
        static std::ofstream lia_audit_times(samplesFileDir + "/" +
            extract_filename(smtFilePath) + ".times.csv");
        lia_audit_times << num_samples << "," << std::chrono::duration<double>(
            std::chrono::steady_clock::now() - lia_audit_process_start).count() << "\n";
        lia_audit_times.flush();
        num_samples++;''')
path.write_text(after)
a.audit.mkdir(parents=True, exist_ok=True)
(a.audit / "highdiv_timing.patch").write_text("".join(difflib.unified_diff(
    before.splitlines(True), after.splitlines(True), fromfile="original/liasampler.cpp",
    tofile="timed/liasampler.cpp")))
subprocess.run(["make", "-C", str(dest), "-j", "12", f"Z3_PREFIX={a.z3_prefix.resolve()}"], check=True)
binary = dest / "highdiv"
(a.audit / "highdiv_timing.json").write_text(json.dumps({
    "original_sha256": hashlib.sha256(before.encode()).hexdigest(),
    "timed_source_sha256": hashlib.sha256(after.encode()).hexdigest(),
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "changes": ["flush accepted sample", "record elapsed time", "flush timestamp"],
    "sampling_logic_changed": False, "flags": "-g -O0 -Wall -std=c++20"
}, indent=2)+"\n")
