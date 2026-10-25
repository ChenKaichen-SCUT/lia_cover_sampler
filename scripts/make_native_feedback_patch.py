#!/usr/bin/env python3
"""Construct the additional native callback patch against the frozen soft patch.
Only a temporary source tree is edited; historical dependency remains untouched.
"""
import difflib, subprocess, tarfile, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
COMMIT='f2c6e1756b13281d96cbf90a8bd940129e56cce4'

def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        with tempfile.TemporaryFile() as f:
            subprocess.run(['git','archive',COMMIT+':z3'],cwd='/tmp/lia-highdiv-frozen',stdout=f,check=True);f.seek(0)
            with tarfile.open(fileobj=f) as t:t.extractall(root,filter='data')
        subprocess.run(['patch','-p1','-i',str(ROOT/'dependencies/patches/z3_soft_phase.patch')],cwd=root,check=True,stdout=subprocess.DEVNULL)
        originals={}
        def edit(name,old,new):
            path=root/name;text=path.read_text();originals.setdefault(name,text)
            assert text.count(old)==1,(name,old[:50],text.count(old))
            path.write_text(text.replace(old,new))
        edit('src/smt/smt_context.h','        void soft_phase_decision(bool_var v, bool& is_pos);', '''        void soft_phase_decision(bool_var v, bool& is_pos);
        std::function<int(expr*,bool,unsigned,unsigned)> m_native_phase_callback;
        unsigned m_native_phase_epoch=0, m_native_phase_decisions=0, m_native_phase_selected=0;
        unsigned m_native_phase_flips=0, m_native_phase_skipped=0, m_native_phase_special=0;
        unsigned m_native_phase_conflict_start=0;
        bool m_native_phase_active=false;
        double m_native_phase_seconds=0;
        void native_phase_decision(bool_var v, bool& is_pos);''')
        edit('src/smt/smt_context.h','        std::string soft_phase_log();','''        std::string soft_phase_log();
        void native_phase_install(std::function<int(expr*,bool,unsigned,unsigned)> cb,unsigned epoch);
        void native_phase_clear();
        std::string native_phase_log();''')
        edit('src/smt/smt_context.h','#include <chrono>','#include <chrono>\n#include <functional>')
        edit('src/smt/smt_context.cpp','bool context::guess(bool_var var, lbool phase) {','''void context::native_phase_install(std::function<int(expr*,bool,unsigned,unsigned)> cb,unsigned epoch) {
    m_native_phase_callback=std::move(cb);m_native_phase_epoch=epoch;
    m_native_phase_decisions=m_native_phase_selected=m_native_phase_flips=m_native_phase_skipped=m_native_phase_special=0;
    m_native_phase_seconds=0;m_native_phase_conflict_start=m_stats.m_num_conflicts;
    m_native_phase_active=bool(m_native_phase_callback);
}
void context::native_phase_clear(){m_native_phase_active=false;m_native_phase_callback=nullptr;}
void context::native_phase_decision(bool_var v,bool& is_pos) {
    // Ordinary state, not trailed: neither backtracking nor restart grants a new slot.
    if(!m_native_phase_active||m_native_phase_selected||m_native_phase_decisions>=32)return;
    auto begin=std::chrono::steady_clock::now();
    ++m_native_phase_decisions;
    if(m_user_propagator||get_assignment(v)!=l_undef){++m_native_phase_special;}
    else {
        int action=m_native_phase_callback(bool_var2expr(v),is_pos,m_native_phase_decisions,
                                          m_stats.m_num_conflicts-m_native_phase_conflict_start);
        if(action==1||action==2){++m_native_phase_selected;if(action==2){is_pos=!is_pos;++m_native_phase_flips;}}
        else {++m_native_phase_skipped;if(action==3)m_native_phase_active=false;}
    }
    m_native_phase_seconds+=std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count();
}
std::string context::native_phase_log(){
    std::ostringstream o;o.precision(17);
    o<<"{\\\"epoch\\\":"<<m_native_phase_epoch<<",\\\"decisions_examined\\\":"<<m_native_phase_decisions
     <<",\\\"selected\\\":"<<m_native_phase_selected<<",\\\"flips\\\":"<<m_native_phase_flips
     <<",\\\"skipped\\\":"<<m_native_phase_skipped<<",\\\"special_or_assigned\\\":"<<m_native_phase_special
     <<",\\\"hook_seconds\\\":"<<m_native_phase_seconds<<",\\\"active\\\":"<<(m_native_phase_active?"true":"false")<<"}";
    return o.str();
}

bool context::guess(bool_var var, lbool phase) {''')
        edit('src/smt/smt_context.cpp','    if (m_soft_active) soft_phase_decision(var, is_pos);','''    if (m_soft_active) soft_phase_decision(var, is_pos);
    if (m_native_phase_active) native_phase_decision(var, is_pos);''')
        edit('src/solver/solver.h','    virtual std::string soft_phase_log() { return "{}"; }','''    virtual std::string soft_phase_log() { return "{}"; }
    virtual void native_phase_install(std::function<int(expr*,bool,unsigned,unsigned)>,unsigned) { throw default_exception("native phase requires simple SMT solver"); }
    virtual void native_phase_clear() {}
    virtual std::string native_phase_log() { return "{}"; }''')
        # solver.h already uses std::function through its original dependencies.
        edit('src/smt/smt_solver.cpp','        std::string soft_phase_log() override {return m_context.get_context().soft_phase_log();}','''        std::string soft_phase_log() override {return m_context.get_context().soft_phase_log();}
        void native_phase_install(std::function<int(expr*,bool,unsigned,unsigned)> cb,unsigned epoch) override {m_context.get_context().native_phase_install(std::move(cb),epoch);}
        void native_phase_clear() override {m_context.get_context().native_phase_clear();}
        std::string native_phase_log() override {return m_context.get_context().native_phase_log();}''')
        edit('src/api/z3_api.h','    void Z3_API Z3_solver_set_initial_value(Z3_context c, Z3_solver s, Z3_ast v, Z3_ast val);','''    /* Project C++ callback extension. Return 0=unsupported, 1=KeepNative,
       2=FlipOnce, 3=stop. Read-only AST APIs only inside the callback.
       Atom is borrowed for the callback; clients retaining it must inc_ref. */
    typedef int Z3_native_phase_callback(void* state, Z3_ast atom, bool native_truth,
                                         unsigned decision, unsigned conflicts);
    void Z3_API Z3_solver_set_native_phase_callback(Z3_context c,Z3_solver s,Z3_native_phase_callback* cb,void* state,unsigned epoch);
    void Z3_API Z3_solver_clear_native_phase_callback(Z3_context c,Z3_solver s);
    Z3_string Z3_API Z3_solver_get_native_phase_log(Z3_context c,Z3_solver s);

    void Z3_API Z3_solver_set_initial_value(Z3_context c, Z3_solver s, Z3_ast v, Z3_ast val);''')
        edit('src/api/api_solver.cpp','    void Z3_API Z3_solver_set_initial_value(Z3_context c, Z3_solver s, Z3_ast var, Z3_ast value) {','''    void Z3_API Z3_solver_set_native_phase_callback(Z3_context c,Z3_solver s,Z3_native_phase_callback* cb,void* state,unsigned epoch) {
        Z3_TRY;RESET_ERROR_CODE();init_solver(c,s);
        // The two project interfaces cannot silently change the same decision.
        to_solver_ref(s)->soft_phase_clear();
        if(!cb){to_solver_ref(s)->native_phase_clear();return;}
        to_solver_ref(s)->native_phase_install([cb,state](expr* e,bool truth,unsigned d,unsigned conflicts){return cb(state,of_ast(e),truth,d,conflicts);},epoch);
        Z3_CATCH;
    }
    void Z3_API Z3_solver_clear_native_phase_callback(Z3_context c,Z3_solver s) {
        Z3_TRY;RESET_ERROR_CODE();init_solver(c,s);to_solver_ref(s)->native_phase_clear();Z3_CATCH;
    }
    Z3_string Z3_API Z3_solver_get_native_phase_log(Z3_context c,Z3_solver s) {
        Z3_TRY;RESET_ERROR_CODE();init_solver(c,s);return mk_c(c)->mk_external_string(to_solver_ref(s)->native_phase_log());Z3_CATCH_RETURN("");
    }
    void Z3_API Z3_solver_set_initial_value(Z3_context c, Z3_solver s, Z3_ast var, Z3_ast value) {''')
        patch=''.join(''.join(difflib.unified_diff(before.splitlines(True),(root/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name)) for name,before in originals.items())
        (ROOT/'dependencies/patches/z3_native_feedback_phase.patch').write_text(patch)
        print('Generated additional patch; apply after frozen z3_soft_phase.patch')

if __name__=='__main__':main()
