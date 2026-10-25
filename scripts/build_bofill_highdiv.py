#!/usr/bin/env python3
"""Rebuild frozen HighDiv with a timestamp-only patch and explicit -O3 flags."""
import hashlib, json, subprocess, tarfile, io, difflib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMMIT='f2c6e1756b13281d96cbf90a8bd940129e56cce4'
def main():
    source=ROOT/'build/bofill/highdiv_release_source'
    if source.exists(): raise SystemExit('fresh build directory required')
    source.mkdir(parents=True)
    data=subprocess.check_output(['git','-C','/tmp/lia-highdiv-frozen','archive',COMMIT,'HighDiv'])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive: archive.extractall(source,filter='data')
    p=source/'HighDiv/src/sampler/liasampler.cpp'; before=p.read_text()
    needle='        samplesFile << "\\n";\n        num_samples++;'
    assert before.count(needle)==1
    after=before.replace(needle,'''        samplesFile << "\\n";
        samplesFile.flush();
        const auto bofill_written_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        static std::ofstream bofill_times(samplesFileDir + "/" +
            extract_filename(smtFilePath) + ".times.csv");
        bofill_times << num_samples << "," << bofill_written_ns << "," << samplesFile.good() << "\\n";
        bofill_times.flush();
        num_samples++;''')
    p.write_text(after)
    evidence=ROOT/'results/bofill_validation';evidence.mkdir(exist_ok=True)
    (ROOT/'dependencies/bofill_highdiv_timing.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/HighDiv/src/sampler/liasampler.cpp',tofile='b/HighDiv/src/sampler/liasampler.cpp')))
    command=['make','-C',str(source/'HighDiv'),'-j4','Z3_PREFIX=/tmp/lia-highdiv-frozen/venv','CXXFLAGS=-O3 -DNDEBUG -Wall -std=c++20']
    with (evidence/'highdiv_release_build.log').open('w') as log: subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    sha=lambda b:hashlib.sha256(b).hexdigest()
    obj=dict(commit=COMMIT,command=command,source_original_sha256=sha(before.encode()),source_timed_sha256=sha(after.encode()),binary_sha256=sha((source/'HighDiv/highdiv').read_bytes()),z3_sha256=sha(Path('/tmp/lia-highdiv-frozen/venv/lib/libz3.so').read_bytes()),patch_scope='only sample flush and post-write CLOCK_MONOTONIC timestamp; no sampling decision changed')
    (evidence/'highdiv_release_build.json').write_text(json.dumps(obj,indent=2)+'\n')
if __name__=='__main__':main()
