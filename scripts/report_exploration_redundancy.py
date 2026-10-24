#!/usr/bin/env python3
"""Tabulate already-audited R10 offline CSVs; no model generation/evaluation."""
import csv,json,statistics as st,math
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'results/round10_analysis'
def read(name):return list(csv.DictReader((BASE/name).open()))
def write(name,rows):
 if not rows:return
 with (BASE/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def rank(v):
 out=[0.]*len(v);idx=sorted(range(len(v)),key=v.__getitem__);i=0
 while i<len(v):
  j=i+1
  while j<len(v) and v[idx[j]]==v[idx[i]]:j+=1
  for k in range(i,j):out[idx[k]]=(i+j-1)/2
  i=j
 return out
def corr(a,b):
 a,b=rank(a),rank(b);x=st.mean(a);y=st.mean(b);num=sum((u-x)*(v-y) for u,v in zip(a,b));den=math.sqrt(sum((u-x)**2 for u in a)*sum((v-y)**2 for v in b));return num/den if den else None

def main():
 rows=read('model_redundancy.csv');groups=defaultdict(list);summary=[];waits=[];correlations=[]
 for r in rows:groups[r['batch'],r['instance']].append(r)
 for (batch,case),rr in groups.items():
  for positive in (False,True):
   a=[r for r in rr if bool(int(r['actual_marginal_covered_bits']))==positive]
   if not a:continue
   row=dict(batch=batch,instance=case,positive=positive,models=len(a),variables=int(a[0]['variables']),initial_models=sum(int(r['round'])==-1 for r in a),known_reachable_gap=sum(r['reachability']=='PLATEAU_WITH_KNOWN_REACHABLE_GAP' for r in a),any_new_observation=sum(int(r['new_observation_bits'])>0 for r in a),future_completed_bits=sum(int(r['future_bits_completed_from_this_zero_observation']) for r in a),changed_gap_count_equals_total=all(r['changed_missing_bit_related_variables']==r['changed_variables_from_previous_model'] for r in a))
   for key in ('actual_marginal_covered_bits','changed_variables_from_previous_model','changed_variables_from_recent_history','changed_missing_bit_related_variables','stable_variables_with_gap','coverage_gap_before','known_missing_bits','dependency_truncated_variables'):
    values=[int(r[key]) for r in a]
    for label,fn in [('mean',st.mean),('median',st.median),('min',min),('max',max)]:row[key+'_'+label]=fn(values)
   summary.append(row)
  for (session,),a in __import__('itertools').groupby(sorted(rr,key=lambda r:r['session']),lambda r:(r['session'],)):
   a=list(a)
   for key in ('changed_variables_from_previous_model','changed_missing_bit_related_variables','changed_related_gap_weight','changed_variables_from_recent_history'):
    correlations.append(dict(batch=batch,instance=case,session=session,models=len(a),x=key,spearman=corr([float(r[key]) for r in a],[int(r['actual_marginal_covered_bits']) for r in a])))
  zero=[r for r in rr if int(r['actual_marginal_covered_bits'])==0 and int(r['round'])>=0]
  for censored in (False,True):
   a=[r for r in zero if (r['next_positive_censored']=='True')==censored]
   row=dict(batch=batch,instance=case,right_censored=censored,zero_models=len(a))
   for k in ('models_until_next_positive','ordinary_query_wall_until_next_positive','ordinary_check_until_next_positive'):
    v=[float(r[k]) for r in a if r[k]!=''];row[k+'_mean']=st.mean(v) if v else None;row[k+'_median']=st.median(v) if v else None;row[k+'_max']=max(v) if v else None;row[k+'_known']=len(v)
   waits.append(row)
 write('group_summary.csv',summary);write('forward_wait_summary.csv',waits);write('within_session_correlations.csv',correlations)
 vr=read('variable_feedback.csv');vv=defaultdict(lambda:defaultdict(int))
 for r in vr:
  key=(r['batch'],r['instance'],r['variable'])
  for k in ('changes','changes_with_zero_global_gain','related_new_bits_during_change','stable_with_gap','gap_weight_sum'):vv[key][k]+=int(r[k] or 0)
 vout=[dict(batch=b,instance=c,variable=v,**d) for (b,c,v),d in vv.items()];vout.sort(key=lambda r:r['changes_with_zero_global_gain'],reverse=True);write('variable_aggregates.csv',vout)
 text=['# 第十轮：普通探索重复覆盖的离线原因分析','','仅重新评价已保存模型；无新求解。冻结评测器保留原 AST、短路访问、整数低 64 位与原分母。逐条对齐原正式覆盖状态，并验证原始边际收益和完整最终 seen0/seen1。新增表不覆盖历史报告。','','分析包括第九轮 6 条 plain_fixed 会话的 298 个正式输出，及第八轮 9 条 micro_plain 会话的 2388 个正式输出。另有 4892 条保存完整赋值的普通候选评估；它们与正式表重叠，不能相加。SAT 但无法关联完整赋值的记录为 0。仅复核实际引用的 10 份历史可达性见证，未重放全量历史模型。','','## 210 个零收益模型的直接证据','','| 类别（第九轮） | 数量 | 相对上一正式模型变化变量，中位数 / 范围 | 对最近四份均为新值的变量，中位数 | 新观察 |','|---|---:|---|---:|---:|']
 for r in summary:
  if not r['batch'].startswith('round9'):continue
  pre='changed_variables_from_previous_model';recent='changed_variables_from_recent_history'
  text.append(f"| {'正收益' if r['positive'] else '零收益'} | {r['models']} | {r[pre+'_median']:g} / {r[pre+'_min']}–{r[pre+'_max']} | {r[recent+'_median']:g} | {r['any_new_observation']} 份有新观察 |")
 text += ['','210 份零收益输出全部没有新单侧观察；不是“先铺垫、以后才配对”的模型。这些模型仍改变 3–29 个变量，因此不是重复赋值，但相较正收益组的 3–389 个变量，变化更局部。零收益组相对最近四份模型的新值变量中位数为 0，说明不少变化在近期已有值之间切换。这里测量的是变化变量数量与值复用，不等同于整数欧氏距离，也不能概括所有普通探索都在邻近数值区域。','','这些状态都有大量稳定变量关联静态单侧缺口。零收益组稳定且关联缺口的变量数中位数为 755；但静态“关联缺口”很宽：本数据所有变化变量都被标为关联缺口，`changed_missing_bit_related_variables` 与全部变化数完全相同，不能据此证明覆盖反馈比一般变量变化更有辨别力。`within_session_correlations.csv` 给出会话内秩相关；相关不是因果，同会话模型也不是独立实验重复。','','缺口数包含恒定表达式和不可达对立值，不能当作可实现收益上界。有界依赖每变量最多 128 节点、512 操作；截断见逐例列，不声称完整传递闭包。第九轮所有 210 份零收益模型所在状态都仍有已保存历史见证支持的可达缺口，每份为 2865–8256 个 bit；不能证实全部单侧缺口都可达。','','## 第八轮对照和首次观察','','Bromberger 的 2300 个正式输出中 1616 个为零收益，其中 3 个是首次模型；非初始零收益模型没有新观察。其零收益组变化变量中位数为 1，但最大为 90，存在大量变量变化仍不增加覆盖的状态。684 个正收益模型变化变量中位数为 26.5。convert 的 85 个输出中只有 3 个初始模型为零收益，其余 82 个均有正收益；SMT_random 的三条会话只保存了各自初始模型。不能把后两者描述成持续产生很多零收益模型。','','第八轮初始模型覆盖增益为零，却提供大量首次单侧观察。Bromberger 三个初始模型的首次观察后来共同支持 48684 个新覆盖 bit（各会话相加）；convert 为 107841 个。SMT_random 三份初始观察没有在保存轨迹中配对，尾部按删失保留。这说明零收益与毫无未来价值不是同义词，但第九轮这 210 个零收益模型没有这种首次观察证据。','','## 等待下一份正收益的成本','','以下是第九轮零收益输出之后的前向等待。不同起点会重叠，不能把各行等待时间相加为总开销。']
 for r in waits:
  if not r['batch'].startswith('round9'):continue
  if r['right_censored']:text.append(f"- {r['zero_models']} 份位于截止尾段，没有下一份正收益，保留删失；剩余普通查询墙钟中位数 {r['ordinary_query_wall_until_next_positive_median']:.6f} 秒。")
  else:text.append(f"- {r['zero_models']} 份可观测到下一份正收益：需再输出模型中位数 {r['models_until_next_positive_median']:g}，最大 {r['models_until_next_positive_max']:g}；普通查询墙钟中位数 {r['ordinary_query_wall_until_next_positive_median']:.6f} 秒，均值 {r['ordinary_query_wall_until_next_positive_mean']:.6f} 秒，最大 {r['ordinary_query_wall_until_next_positive_max']:.6f} 秒。纯 check 中位数 {r['ordinary_check_until_next_positive_median']:.6f} 秒。")
 text += ['','第八轮普通查询包装时间和纯 check 并不都可恢复，未知值保持为空；不把微观候选成本混作普通探索成本。例如第九轮 xH33 变化 78 次，其中 50 次对应零全局增益；xH34 为 77/50，xH296 为 47/32。它们同时发生的相关新增 bit 总数分别为 137、123、36，既不能说它们从不有用，也不能把这些 bit 全归因于它们。逐会话、逐变量统计可定位大量变化但多为零全局增益的变量；`related_new_bits_during_change` 是共同发生量，不是变量独立贡献。','','## 对新机制的含义','','数据支持试验“改变近期稳定且关联缺口的变量”，但不证明这种选择能实现缺口或比多一次普通查询划算。静态相关变量过宽、不可达缺口以及大范围变化仍零收益都是反证风险。因此只按冻结协议比较 plain_only、plain_extra 和 projection_escape，用真实覆盖和完整成本决定是否停止。','','重放命令（输出目录必须新建，不能覆盖本次审计）：','','```bash','HIGHDIV_ROOT=/tmp/lia-highdiv-frozen /tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_exploration_redundancy.py','python3 scripts/report_exploration_redundancy.py','```','','脚本当前将首次分析写入固定的新目录；本次目录已存在时会拒绝覆盖。已有 CSV 的汇总可以单独运行第二条。证据哈希在各案例 sources.json、plan.json，逐模型、变量、等待和相关数据均在 results/round10_analysis/。']
 (ROOT/'docs/第十轮普通探索重复覆盖原因分析.md').write_text('\n'.join(text)+'\n')
if __name__=='__main__':main()
