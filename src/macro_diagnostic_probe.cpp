#include "macro_choice.hpp"
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
using namespace lia;
int main(int argc,char**argv){
 try {
  if(argc!=7)throw std::invalid_argument("macro_diagnostic_probe F GROUP ALT TIMEOUT_MS SEED OUT");
  Budget session;session.seconds=30;
  std::filesystem::path out(argv[6]);if(!std::filesystem::create_directory(out))throw std::runtime_error("new directory required");
  Formula f(argv[1]);double formula_seconds=session.elapsed();
  Budget cb;cb.seconds=.1;cb.parent=&session;ChoiceCatalog cat(f,cb);
  unsigned g=std::stoul(argv[2]),a=std::stoul(argv[3]),ms=std::stoul(argv[4]),seed=std::stoul(argv[5]);
  if(ms!=50 && ms!=200 && ms!=1000 && ms!=2000)throw std::invalid_argument("fixed diagnostic levels");
  auto engine_start=Clock::now();MacroChoiceGenerator macro(f,cat,seed);
  double engine_seconds=std::chrono::duration<double>(Clock::now()-engine_start).count();
  double initialized=session.elapsed();
  Budget action;action.seconds=ms/1000.+1.;action.parent=&session;Stats stats;
  auto q=macro.query_choices({{g,a}},action,stats,ms);
  std::ofstream cf(out/"catalog.json");cf<<cat.json()<<'\n';cf.flush();
  std::ofstream model(out/"model.json");model<<(q.model?f.model_json(*q.model):"null")<<'\n';model.flush();
  double written=action.elapsed();if(!model||!cf)throw std::runtime_error("write failure");
  std::ofstream summary(out/"summary.json");
  summary<<std::setprecision(17)<<"{\"source_sha256\":"<<quote(LIA_SOURCE_SHA)<<",\"group\":"<<g<<",\"alternative\":"<<a<<",\"group_node\":"<<cat.groups.at(g).node
   <<",\"alternative_node\":"<<cat.groups.at(g).alternatives.at(a).node<<",\"status\":"<<quote(q.status)<<",\"reason\":"<<quote(q.detail)
   <<",\"seed\":"<<seed<<",\"evidence\":"<<q.evidence_json()<<",\"formula_seconds\":"<<formula_seconds<<",\"catalog_seconds\":"<<cat.seconds
   <<",\"engine_seconds\":"<<engine_seconds<<",\"initialization_seconds\":"<<initialized<<",\"query_seconds\":"<<q.elapsed_seconds<<",\"model_written_action_seconds\":"<<written
   <<",\"action_deadline\":"<<action.seconds<<",\"timely\":"<<(written<=action.seconds&&!session.done()?"true":"false")<<",\"has_model\":"<<(q.model?"true":"false")
   <<",\"session_seconds\":"<<session.elapsed()<<",\"statistics\":"<<stats.json()<<"}\n";
  summary.flush();if(!summary)throw std::runtime_error("write summary");return 0;
 }catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}
}
