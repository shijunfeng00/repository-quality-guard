# 审计与验收协议

本文件规定 `quality_guard.py audit` 生成的 `修改说明.md`、人工裁决和只读 `verify` 的**外部可观察合同**。规则修复方向见 [`RULES.md`](RULES.md)，代码修改规范见 [`CODING_GUIDE.md`](CODING_GUIDE.md)。

## 基线与模式

- `normal` 为默认模式。Git 可用时按用户指定的 `--diff-base` 或任务起点冻结的 accepted design baseline 比较；无 Git 时历史 Delta 为 N/A，不擅自切换模式。
- `ignore-diff` 忽略 Git 差分，但不豁免当前质量规则；`cleanup` 要求清除当前生产范围的普通 C/E/W。仅用户明确授权方可选择二者。
- `ignore-diff` / `cleanup` 不与 `--diff-base` 或 `--staged` 混用。`audit` 与 `verify` 必须使用相同模式、基线、Profile 和源码。
- 用户未授权接口破坏时，基线内的公开 API、参数、返回值、协议和 owner 为默认契约；出现变化必须说明必要性与调用方影响。
- 实际 Git author / committer 由真实贡献和本地 Git 工作流决定，不能根据提交者姓名猜测设计基线。

## `修改说明.md` 报告

正式报告固定九部分：审计元数据、变更事实、静态质量门禁、ADD（新增接口必要性）、SEM（语义审计账本）、架构十一问、Reduction Pass、最终门禁、提交建议。

机器生成的源码、Git 差分、Finding、身份/哈希等字段不可手改；人工字段必须依据当前源码和真实消费者填写，不准用旧版报告或没有核验的推测代替。

`audit` 负责生成/刷新报告，报告缺失人工裁决时仍为未验收（rc=3）。`修改说明.md` 是独立审计交付物，默认不进入业务 Git commit；mbox、ZIP、单测 PASS 均不能替代。若用户明确要求其进入版本管理，再按项目流程处理。

## ADD：接口必要性

每个新增的生产 function/method/class/helper 应记录复用搜索、唯一能力所有者、调用方与实际用途、可删除/内联/合并的理由。`doc-search` 只产生候选，未命中不证明没有既有实现。

## SEM：证据裁决

- 对本轮新增/升级的普通 Critical/Error/Warning 必须解决；触达文件的历史债务必须逐项修复或以真实消费者/协议/行为证据解释保留理由与关闭条件。未触达债务不应无界扩大本轮工作。
- 结论须按 `hypothesis → trace → evidence → finding` 核验：从变更点追踪 Producer、Schema/Guard、Consumer、状态/异常/事务/并发与可观察后果。缺乏失败路径的猜测不能写成高确定性的阻断。
- 必需内部合同优先 fail-loud。静默 fallback、宽松 return-shape、suppression 和额外依赖的保留，需要有明确外部兼容、可选能力或 best-effort 业务理由；不能只把一种兜底语法换成另一种。
- 若规则候选与真实合同冲突，先登记可复现的 TP/TN/FP/FN 和人工裁决，再申请规则修改授权；报告不能代替修改规则的授权。
- 关系图可提供静态调用与受影响测试线索；动态调用未解析时影响范围为 UNKNOWN，而非“无调用方”。受影响测试首先应原样运行，不是默认允许改写断言的清单。
- **Release identity**：QG203 检查文件路径/名称中的发布身份，按 normal 差分规则裁决；QG205 检查内容中的版本和外部依赖证据，属于语义复核而非自动 REJECT。真实版本化 API、schema、依赖和迁移协议应保留并说明其 owner。Profile 额外配置的绝对 BLOCKER 不受历史差分抵消。

## 架构与 Reduction Pass

分别检查**正确性/契约**与**结构/减法**：一处函数变短不证明复杂度下降。比较修改前后的概念数、分支、状态/mode、helper/layer 和耦合；可直接删除、复用或下沉的结构应优先收敛。必要的额外层须有真实复用、协议或资源生命周期证据。允许有证据的 KEEP，不设机械减行数配额。

若宿主提供独立 reviewer，可对高风险变更做 fresh-eyes 审阅；其结论只是待核验假说，不代替主审计者查证。

## Commit 建议与最终 `verify`

报告中需要包含绑定当前**完整 patch**的 `git commit -m` 建议命令；这只是一项审计内容，不授权 RQG 执行提交。未设置 Profile commit policy 时不强加项目专属格式。

`verify` 只读复查当前源码、Profile、基线和报告，拒绝 stale 或不完整账本，不替用户填写人工裁决。最终结论：

| 状态 | 退出码 | 含义 |
| --- | ---: | --- |
| `ACCEPT` | `0` | 审计门禁全部通过 |
| `REVIEW_REQUIRED` | `5` | 非 REJECT，但仍有语义项需人工裁决 |
| `REJECT` | `1` | 静态或语义门禁拒绝 |
| 报告不可验收 | `3` | 缺失、未完成或违反报告契约 |
| 环境/完整性失败 | `2` / `4` | 不能声称审计通过 |

**源码、依赖配置、Profile 或报告变化后，需要重新完成验证。** 最终交付必须列出实际测试结果、报告完整性、静态事实、语义裁决状态、全部未解决 C/E/W 和退出码。

## 升级与 Git 持久化

`.agents/skills/repository-quality-guard/**` 是宿主仓库应提交的 Skill 运行时；更新需要完整替换、`git add -A` 并从 fresh clone 验证。`修改说明.md` 默认单独交付。RQG 不覆盖宿主已有根 `AGENTS.md`；只有部署时确实新生成的宿主指令可按授权纳入变更。

每个 clone 要检查托管 pre-push hook；不得覆盖非托管 hook、跳过 `verify` 或以 `--no-verify` 绕过门禁。完整 ZIP 与 installed `.agents` 的具体构建及依赖策略由发行工具验证，不能仅凭本机部署成功声称发行验收通过。
