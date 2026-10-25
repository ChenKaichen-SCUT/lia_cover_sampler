#!/usr/bin/env python3
"""Capture exact local dependency/build provenance after both builds pass."""
import json,hashlib,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    z=ROOT/'dependencies/z3-soft-phase';binary=ROOT/'build/soft_phase/release/soft_phase_sampler'
    frozen=Path('/tmp/lia-highdiv-frozen/venv/lib/libz3.so');assert sha(frozen)=='c66acd3c4743444ce90b255820a88a65cbeaa4bd13808ed59ac7be91721967ca','frozen dependency changed'
    files=[ROOT/'dependencies/patches/z3_soft_phase.patch',z/'build/config.mk',z/'build_sanitize/config.mk',z/'install/lib/libz3.so',z/'install-sanitize/lib/libz3.so',binary,ROOT/'build/soft_phase/sanitize/soft_phase_sampler']+sorted((z/'install/include').glob('*'))
    for p in files:assert p.is_file(),p
    value=dict(upstream_commit='f2c6e1756b13281d96cbf90a8bd940129e56cce4',base_commit='07098bc',frozen_library_sha256=sha(frozen),compiler=subprocess.check_output(['g++','--version'],text=True),release_config=(z/'build/config.mk').read_text(),sanitizer_config=(z/'build_sanitize/config.mk').read_text(),files={str(p.relative_to(ROOT)):sha(p) for p in files},runtime_links=subprocess.check_output(['ldd',str(binary)],text=True),commands=['python3 scripts/mk_make.py --prefix=ABS/install --pypkgdir=ABS/install/python -b build --python','make -C build -j12 libz3.so','make -C build -j12 install','sanitize: independent build tree, all objects rebuilt with -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer; 4 then 8 workers; link sanitizer runtime','cmake -S . -B build/soft_phase/release -DCMAKE_BUILD_TYPE=Release -DLIA_ENABLE_SOFT_PHASE=ON -DZ3_PREFIX=ABS/install','cmake --build build/soft_phase/release -j7','python3 scripts/test_soft_phase_build.py --build build/soft_phase/release --out results/soft_phase_validation/release','python3 scripts/test_soft_phase_build.py --build build/soft_phase/sanitize --out results/soft_phase_validation/sanitize --sanitize'])
    (ROOT/'dependencies/soft_phase_build.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
