#!/usr/bin/env python3
"""Chinese report from audited paired data; exposure is never pooled with screen."""
import argparse,csv,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LABEL={'plain':'plain','catalog_random_one':'catalog 随机','native_random_one':'native 随机','native_feedback_one':'native 反馈'}
def read(path):
    with path.open() as f:return list(csv.DictReader(f))
def short(name):return 'SMT_random' if 'random' in name else 'SMT_real'
def cell(x):
    if x is None or x=='':return '未完成/NA'
    if isinstance(x,float):return f'{x:.6g}'
    return str(x)
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'---|'*len(headers),*['| '+' | '.join(cell(x) for x in row)+' |' for row in rows]])
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='results/native_feedback');p.add_argument('--report',default='docs/原生决策反馈实现与实验报告.md');a=p.parse_args()
    root=Path(a.root);gate=json.loads((root/'exposure_gate.json').read_text());ex=json.loads((root/'exposure_30s/analysis/summary.json').read_text())
    lines=['# 原生决策机会与在线收益反馈：实现与实验报告','',
        '基于冻结提交 `6b54afe`，新分支 `native-feedback-phase`。本轮保留所有历史报告及 No-Go；未重跑 HighDiv，未启动900秒实验，未添加目标约束、整数选择编码、投影或额外受限查询。',
        '', '## 实现与可复核边界','',
        '使用独立新补丁库中的原式持久 simple 求解器。首次及时模型之后，每次正常查询只查看前32个原生决策，在首个支持对象选择一次 KeepNative/FlipOnce。所有及时、合法、不同原模型继续输出，新增断言只有完整模型阻塞。缓存4096、16维特征、统一256条反馈窗口、岭参数1、每动作8条暖启动、80%利用与20%随机，以及5%机制成本保护均在曝光前冻结。',
        '', 'catalog_random_one 是按新单点额度实现的旧目录范围对照，不能当成历史 random_soft 原样重跑。四种配置使用同一新库与相同普通查询额度。随机配置不承担学习版特有训练。',
        '', 'Release、ASan/UBSan、原生接口及全部适用回归证据见 `results/native_feedback_validation/` 和 `dependencies/native_feedback_build.json`。审计使用冻结 ManualSatisfiesMetric，搜索入口禁用；逐模型核对完整 seen0/seen1、短路访问、整数低64位、写完截止、正式去重、边际累计，以及决策前特征和独立重建的岭预测。失败、无模型和无机会查询的成本全部保留。',
        '', '## 功能曝光（8个30秒会话，单独报告）','',
        table(['公式','配置','正式模型','covered','实际机会','目录外所选机会','Keep/Flip','机制秒','停止原因'],
              [[short(r['formula']),LABEL[r['configuration']],r['formal_models'],r['covered'],r['selected'],r['outside_catalog'],f"{r['keep']}/{r['flip']}",r['mechanism_seconds'],r['mechanism_stop'] or '无'] for r in ex['sessions']]),
        '', '曝光门槛：'+json.dumps(gate,ensure_ascii=False)+'。此门槛检查正确性和接口实际工作，不根据覆盖胜负挑选公式或调整参数。']
    if not gate['passed']:
        lines+=['','## 本轮结论','','曝光未通过预先规定的接口适用性门槛，未启动24个120秒会话。没有通过增加约束、机会或额度追求曝光成功。原始数据与失败证据已保存；不能据此声称学习有效。']
    else:
        out=root/'screen_120s';data=json.loads((out/'analysis/summary.json').read_text());sessions=data['sessions'];pairs=data['paired_summary']
        times=read(out/'analysis/time_points.csv');prefixes=read(out/'analysis/prefixes.csv');errors=read(out/'analysis/prediction_errors.csv')
        lines+=['','## 正式验证（24个120秒会话）','','同一公式、seed 的四配置在四个不同物理核心同步启动，组间屏障，核心按组轮换。最多4个采样进程；采样期间不编译或审计。6组的启动、资源、亲和性、完整公式、二进制/库/源码哈希在计划及原始归档中。总预算从加载之前开始，没有免费训练或延长查询。曝光与正式数据不合并作为重复。',
            '',table(['公式','seed','配置','模型数','covered/分母','30/60/120秒 covered','首模型观察/后续观察','后续 covered','进程秒'],
                [[short(r['formula']),r['seed'],LABEL[r['configuration']],r['formal_models'],f"{r['covered']}/{r['total']}",'/'.join(x['covered'] for x in times if x['run']==r['run']),f"{r['first_model_observations']}/{r['post_first_observations']}",r['post_first_covered'],r['process_wall_seconds']] for r in sessions]),
            '', '进程墙钟为监督器观察值，可能含轮询与清理延迟；及时输出用模型写完时间单独判定。精确查询区间加末尾维护与内部 elapsed 对账，不能用 check 秒数替代会话总成本。',
            '', '### 三组必须分开的配对比较','', '下表按公式给出3个seed的均值、中位数和胜/平/负，差值单位为真实 covered bit。每个会话是重复单位；查询事件不是独立实验重复。',
            '', table(['公式','比较（左−右）','秒','配对数','胜/平/负','均值差','中位差'],
                [[short(r['formula']),LABEL[r['left']]+' − '+LABEL[r['right']],r['checkpoint'],r['pairs'],f"{r['wins']}/{r['ties']}/{r['losses']}",r['mean_delta'],r['median_delta']] for r in pairs if r['dimension']=='time_seconds']),
            '', '### 相同正式样本数','',
            table(['公式','seed','配置','前2/5/10/50/100/1000 covered'],
                [[short(r['formula']),r['seed'],LABEL[r['configuration']],'/'.join(x['covered'] if x['completed']=='True' else '未完成' for x in prefixes if x['run']==r['run'])] for r in sessions]),
            '', '缺失前缀不能补零，也不只展示已达到的会话。逐seed配对差以及每个前缀的胜平负、均值和中位数见 `analysis/paired.csv` 和 `paired_summary.csv`。最终时间覆盖好不自动代表相同样本数更好。',
            '', '### 机会、反馈与完整成本','',
            table(['公式','seed','配置','支持访问/所选机会','目录外所选','Keep/Flip','最终实现/未实现/无模型','冷启动/利用','正/零交付回报','机制/钩子/训练秒','停止原因'],
                [[short(r['formula']),r['seed'],LABEL[r['configuration']],f"{r['supported_encounters']}/{r['selected']}",r['outside_catalog'],f"{r['keep']}/{r['flip']}",f"{r['realized']}/{r['unrealized']}/{r['no_model']}",f"{r['cold_start']}/{r['exploitation']}",f"{r['selected_positive_gain']}/{r['selected_zero_gain']}",'/'.join(f'{r[k]:.6g}' for k in ['mechanism_seconds','hook_seconds_nested','training_seconds']),r['mechanism_stop'] or '无'] for r in sessions]),
            '', '“支持访问”包含目录模式跳过的目录外对象，不能与“实际所选机会”混为一谈。钩子嵌套在 check 内，同时也属于机制成本分类，不能把这三列相加作为墙钟。5%是停止后续新干预的保护阈值，有界单次操作与必要审计记录的越界成本仍保存，不能隐藏。',
            '',table(['公式','seed','反馈阶段','所选反馈数','正交付标签','预测效用不等次数','收益MAE（归一化）','成本MAE秒'],
                [[short(r['formula']),r['seed'],r['feedback_stage'],r['selected_queries'],r['positive_delivered_labels'],r['unequal_utility_predictions'],r['gain_mae'],r['cost_mae_seconds']] for r in errors if r['configuration']=='native_feedback_one']),
            '', '预测误差只针对当时实际选择动作。预测值不同、更多翻转或拟合误差较小都不能替代覆盖胜出；另一个动作的真实回报仍未知。最终未实现方向时仍保留执行过的动作标签。每条反馈是既有增量求解历史下整次查询结果，不是原子的独立因果贡献。',
            '', '### 供给与高原风险','',
            table(['公式','seed','配置','SAT/Unknown','最长无covered增长秒','最长零新观察模型段','完整区间/末尾秒','未完成前缀数'],
                [[short(r['formula']),r['seed'],LABEL[r['configuration']],f"{r['sat']}/{r['unknown']}",r['longest_no_growth_seconds'],r['longest_zero_new_observation_models'],f"{r['full_cycle_seconds']:.6g}/{r['tail_seconds']:.6g}",r['incomplete_prefixes']] for r in sessions])]
        final=[r for r in pairs if r['dimension']=='time_seconds' and r['checkpoint']==120]
        b=[r for r in final if r['left']=='native_feedback_one' and r['right']=='native_random_one']
        repeated=[r for r in b if r['wins']==3]
        opportunities=sum(r['outside_catalog'] for r in sessions if r['configuration']=='native_random_one')
        lines+=['','## 回答本轮研究问题','',
            f'1. **是否覆盖旧摘要遗漏的真实机会？** native_random_one 实际选择了 {opportunities} 次目录外支持决策。表达式、原变量依赖和目录身份均逐条重算；这证明接口适用范围的实际变化。是否值得扩大范围，必须再看 native_random−catalog_random 的时间与相同前缀配对，不能由机会数直接宣布假设A的性能部分成立。',
            '', '2. **完整实际收益是否形成比逻辑下界更有效的选择依据？** 本轮确实只用实际执行动作的完整交付收益与成本训练，并在决策前保存预测；但没有重跑一组“逻辑下界评分且其余完全相同”的控制。因此不能直接宣称已证明经验预测优于逻辑下界。可以回答的是同机会范围下 feedback−random 的差异以及是否走出冷启动。',
            '', '3. **是否优于随机并提高真实覆盖？** '+('两公式均在120秒三seed中严格胜过同范围随机，是开发阶段的重复正面信号；是否同时优于 plain、早期和相同样本数必须结合上述配对，不能自动晋级。' if len(repeated)==2 else '仅 '+short(repeated[0]['formula'])+' 在120秒三seed均严格胜过同范围随机，属于单公式有限支持；其余实例、早期与相同前缀的风险保留，不推及整个 bofill 类。' if repeated else '没有公式在120秒三seed中均严格胜过同范围随机。当前证据不支持稳定的学习优势；逐seed存在的改善或退步如表保留。'),
            '', '本轮到此停止扩大实验。即使有开发正面信号，也只可另行讨论验证计划；若学习未胜随机，不增大模型、翻转频率、奖励权重或预算来修饰结论。历史 No-Go 保持不变，没有自动启动后续长实验。']
    lines+=['','## 证据与重放','',
        '协议：`docs/原生决策反馈实验协议.md`；实现与回归说明：`docs/原生决策反馈接口与正确性.md`。本报告由审计后表格生成，完整模型、每次决策上下文、概率、当时预测、成本、失败与输出截止证据均在阶段原始归档中。`archive_manifest.json` 列出分片与逐文件 SHA-256；不能把归档中的候选视为额外正式输出。',
        '', '```bash',
        'export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen',
        '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/archive_bofill_results.py results/native_feedback/screen_120s --restore',
        '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/audit_native_feedback_results.py results/native_feedback/screen_120s --jobs 15',
        '/tmp/lia-highdiv-frozen/venv/bin/python3 scripts/analyze_native_feedback_results.py results/native_feedback/screen_120s',
        'python3 scripts/report_native_feedback_results.py',
        '```',
        '', '以上为保存数据的离线重放，不调用求解器搜索。若曝光门槛未过，没有 screen_120s，应将路径替换为 exposure_30s。运行命令及冻结参数见协议；不得用重放报告暗示重新执行了性能实验。','']
    Path(a.report).write_text('\n'.join(lines));print('Chinese native feedback report written',flush=True)
if __name__=='__main__':main()
