#!/usr/bin/env python3
"""Generate the minimal output boundary patch against exact frozen AB source."""
from pathlib import Path
import hashlib,json,difflib
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'vendor/frozen_ab/src/main.cpp'
manifest=json.loads((source.parents[1]/'manifest.json').read_text())
assert hashlib.sha256(source.read_bytes()).hexdigest()==manifest['files']['src/main.cpp']
s=original=source.read_text()
def replace(a,b,count=1):
 global s
 assert s.count(a)==count,(a,s.count(a))
 s=s.replace(a,b)
replace('#include "core.hpp"','#include "core.hpp"\n#include "ab_output.hpp"')
replace('void signal_handler(int) { interrupted = 1; }','void signal_handler(int) { interrupted = 1; output_refiner::cancel(); }')
replace('double seconds = 900;','double seconds = 900;\n    bool output_polish=false;')
replace('else if (k == "--audit-values")','else if (k == "--output-polish")\n            a.output_polish = v == "true";\n        else if (k == "--audit-values")')
replace('unsigned count = 0;','''unsigned count = 0;
        FormalOutput formal(*f,budget,opt.output_polish,opt.seed,opt.output,typed,samples,emission);
        formal.input_path=opt.input;
        std::function<std::string()> isolation_snapshot;''')
replace('<< "{\\\"status\\\":\\\"running_checkpoint\\\",\\\"samples\\\":" << count','<< "{\\\"status\\\":\\\"running_checkpoint\\\",\\\"samples\\\":" << formal.count')
replace('<< ",\\\"elapsed\\\":" << budget.elapsed() << ",\\\"covered\\\":" << coverage.covered()','<< ",\\\"elapsed\\\":" << budget.elapsed() << ",\\\"covered\\\":" << formal.covered')
replace('<< ",\\\"source_sha256\\\":" << quote(LIA_SOURCE_SHA) << "}\\n";','<< ",\\\"source_sha256\\\":" << quote(LIA_SOURCE_SHA) << formal.fields(count,coverage.covered()) << "}\\n";')
start=s.index('            Timer t(stats, "write");',s.index('auto accept ='))
end=s.index('            coverage.accept(o);',start)
s=s[:start]+'''            Timer t(stats, "write");
            const double extra_before=formal.extra_seconds;
'''+s[end:]
replace('''            round_feedback.add(result);
            return result;''','''            round_feedback.add(result);
            const auto guard=isolation_snapshot?isolation_snapshot():std::string();
            formal.deliver(m,o,category,count-1,coverage.covered());
            if(isolation_snapshot && isolation_snapshot()!=guard)
                throw std::logic_error("output polish modified frozen generator state");
            if(isolation_snapshot) ++stats.counts["raw_generator_isolation_checks"];
            stats.times["write"]-=formal.extra_seconds-extra_before;
            return result;''')
# n constrains actual delivered rows only. Internal raw count never feeds strategy.
replace('if (count >= opt.n || budget.done())','if (formal.count >= opt.n || budget.done())',2)
replace('while (count < opt.n && !budget.done())','while (formal.count < opt.n && !budget.done())')
replace('status = count >= opt.n ?','status = formal.count >= opt.n ?')
replace('auto batch_start = Clock::now();','auto batch_start = Clock::now();\n                    const double extra_at_batch_start=formal.extra_seconds;')
replace('std::chrono::duration<double>(Clock::now() - batch_start).count();','(std::chrono::duration<double>(Clock::now() - batch_start).count()-(formal.extra_seconds-extra_at_batch_start));')
replace('double recent_local_gain = 0, recent_local_cost = 0;','''double recent_local_gain = 0, recent_local_cost = 0;
            if(opt.mode!="benchmark") isolation_snapshot=[&] {
                std::ostringstream out;out<<rng<<'|'<<stalled<<'|'<<recent_local_gain<<'|'<<recent_local_cost
                    <<'|'<<count<<'|'<<round_feedback.coverage<<'|'<<round_feedback.observations<<'|'<<f->key(current)<<coverage.json();
                for(const auto &model:seed_bank) out<<f->key(model)<<'|';
                if(global) {
                    out<<global->blocked_models()<<global->query_smt2()<<global->exploration_query_smt2();
                    for(const auto &core:global->conflicts.cores) for(int id:core) out<<id<<',';
                }
                return out.str();
            };''')
replace('<< "{\\\"status\\\":" << quote(status) << ",\\\"samples\\\":" << count','<< "{\\\"status\\\":" << quote(status) << ",\\\"samples\\\":" << formal.count')
replace('<< ",\\\"covered\\\":" << coverage.covered() << ",\\\"total\\\":" << f->total_bits()','<< ",\\\"covered\\\":" << formal.covered << ",\\\"total\\\":" << f->total_bits()')
replace('<< "}\\n";\n        std::cout << status', '<< formal.fields(count,coverage.covered()) << "}\\n";\n        std::cout << status')
replace('std::cout << status << ": " << count << " samples, " << coverage.covered()', 'std::cout << status << ": " << formal.count << " samples, " << formal.covered')
s='// Generated from exact AB 269de45 by scripts/make_ab_adapter.py; never edit generation logic here.\n'+s
(ROOT/'src/ab_adapter.cpp').write_text(s)
(ROOT/'vendor/frozen_ab/output_adapter.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='269de45/src/main.cpp',tofile='src/ab_adapter.cpp')))
print('Generated output-only adapter; original generation loop retained')
