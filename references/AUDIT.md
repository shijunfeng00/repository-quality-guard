# 审计协议

## Skill 升级的 Git 持久化门禁

`.agents/skills/repository-quality-guard/**` 是仓库协作运行时，必须进入 Git；根 `AGENTS.md` 是宿主共享协调文件；QG 从不覆盖已有文件，仅当本轮 deploy 在原本缺失时创建了根 `AGENTS.md` 才可能属于同一升级提交。它们与默认不入 Git 的 `修改说明.md` 属于不同交付通道。正式升级默认始终是**从任意旧/残缺版本到当前 manifest 的全量替换**，不要求逐版本升级；deploy 用 staging 构造完整目标树并原子替换旧目录，目标版本不存在的历史文件必须消失。仅在本机 deploy 成功不证明可交付：必须至少 `git add -A -- .agents/skills/repository-quality-guard`，只有本轮同步过根 `AGENTS.md` 时才一并 stage，提交新增/修改/删除，并从 commit fresh clone。clone 中必须存在 `scripts/quality_guard.py`、`scripts/git_hook_install.py`、`runtime/install_dependencies.py`、完整 `runtime/src` 与 `SKILL.md`；根 `AGENTS.md` 仅对本轮受管理模板做条件验证，且必须能实跑报告闭环。若用户明确要求本机临时安装而不提交，必须把“不可由同事 pull 复现”列为显式非持久化状态，不得声称仓库升级完成。

正式 `.skill.zip` 是外部独立运行与 authoring 介质：可以携带 `README.md`、`dev-tests/`、authoring/release tools，同时必须携带锁定 Python wheelhouse 与 Node 离线 payload，使 `runtime/install_dependencies.py --offline` 在无网络环境完成 bootstrap；不得携带构建缓存或历史 runtime。部署后的 `.agents` 必须裁掉 README、测试、authoring tools、Profile authoring 文件及第三方离线介质，仅保留第一方运行时代码、依赖锁与冻结策略。项目或 Profile 可以要求安装托管 pre-push；hook 调用当前安装策略下的 `verify`，因此报告缺失、未完成或 stale 会与代码 REJECT 一样阻止 push。

## 报告结构

固定九段：审计元数据、变更事实、静态质量门禁、新增与接口必要性审判、语义审计账本、十一项架构拷问、Reduction Pass、最终门禁、提交 Commit。

机器自动区块不可改。报告中的 Git baseline 同时是本轮 accepted design baseline：用户指定 revision 优先，否则在开始修改前冻结任务起点基线；它不能按 author 姓名推导，也不能在本轮中途移动到新 commit 来缩小差分。文件变化只列 A/M/D/R；函数、方法、类、property、变量与 API 变化进入 definition/interface 账本，避免按文件重复解释。

## 审计产物与最终交付

`修改说明.md` 是代码修改任务的强制审计产物。它默认留在被审计仓库工作区/交付附件中，不属于业务 Git 差分；patch、mbox、commit、bundle、zip 或 clean worktree 都不能替代它。用户若只指定某种代码交付格式，仍必须另外交付完整 `修改说明.md`。只有用户明确要求把审计报告纳入版本控制时才进入 Git。

最终回复必须显式汇报 `修改说明.md` validator/报告门禁、静态事实门禁、模型语义与最终状态，并列出未执行或受环境阻断的验证。`audit GENERATED`/rc=3/PENDING 只表示模板已生成，不能作为完成结论。最终 `verify` 后任何代码或报告变化都会使结论失效，必须重新闭环。

## Merge collaboration baseline

普通 accepted design baseline 由用户指定 revision 或任务起点决定，不能按 author 推导。若项目存在额外协作/合并基线规则，由对应 Profile/AGENTS 声明；Core 不内置组织、作者或仓库身份。Git authorship 始终遵循实际贡献者及 Git 原生语义。

## Interface baseline

无本轮明确需求授权时，accepted design baseline 中已经存在的 public interface、参数、返回结构、协议与 owner 是默认契约；实现应优先适配它们。存在生产接口变化时 Q2 不得 `NOT_APPLICABLE`，必须说明需求授权、调用方兼容性与验证。该设计优先级与 Git author/committer 完全无关。

## ADD

每个新增生产 function/method/class/helper 必须说明：复用检索、唯一所有者、必要性/减法、真实源码与调用方证据。`doc-search` 只负责高召回，Top1/分数不能证明能力不存在。

## SEM

本轮 Delta 必须逐项裁决。历史语义候选按规则聚合；本轮触及 owner/调用链附近的历史候选优先进入有限风险抽样，不要求逐项展开数百行。抽样发现 INVALID 时必须修复并重新 audit；全部 JUSTIFIED 时允许历史修复数为 0。

fallback/宽松契约的保留必须属于明确的外部兼容、用户可见降级、可选能力或 best-effort side effect；内部确定性契约默认 fail-loud。用户降级必须保持失败可观测，不伪造工具/检索/证据成功，不污染 canonical state，也不能吞未知程序 bug。

QG144 的不同返回结构只有在存在显式 union/discriminator/公开协议时才能保留。QG020 suppression 必须窄且确有必要。QG128 必须证明聚合依赖来自协议固定签名，否则缩小依赖面。QG178 未获 Git 基线授权时只能 BLOCKING。QG191 表示本轮手工构造标准协议对象（例如 OpenAI tool_call）而未复用项目既有 schema/validator/ledger owner；必须结合 doc-search 与真实源码裁决，不能靠手工 JSON “形状看起来一样”放行。

### Semantic Review Method

非静态审计固定分成两个认知视角，但仍写入同一 SEM/十一问/Reduction 报告，不新增状态机或命令：

1. **Correctness / Contract**：先确认 intent、可观察失败、non-goals，再追踪 producer/consumer、协议/API、异常路径、state/transaction/concurrency、affected tests。能沿真实调用链验证的假设必须验证完，不能把“如果后端没处理”“可能存在问题”直接写成 BLOCKING。
2. **Structure / Reduction**：独立检查 code-judo、更简单 canonical owner/layer、concept/branch/mode/helper/layer/state/coupling 是否真实减少；函数变短、文件拆开或静态告警下降，不等于复杂度已删除。新增 abstraction/wrapper 必须有独立契约、真实复用或明确边界收益，不能只为让指标变好。

语义结论遵循 `hypothesis → trace → evidence → finding`。ERROR/CRITICAL/BLOCKING 必须能给出从本轮变化到被破坏 invariant、真实消费方/状态条件以及可观察后果的完整失败路径；只能证明设计 smell 而不能证明行为故障时，保留为 Warning/REVIEW，而不是靠措辞升级严重度。

若宿主支持独立 reviewer/subagent，巨型重构、公开协议大改、高 blast radius 或 `REVIEW_REQUIRED` 可选做一次 fresh-eyes review：给 reviewer 同一份 intent、frozen diff、相关源码和 QG facts，只寻找漏掉的 correctness issue、complexity relocation、canonical-owner 错位与更简单设计。外部 reviewer 的结果只是 hypothesis，主审计者重新验证后才能进入 SEM；无 subagent 的环境不降低核心 QG 能力。


## Relation Impact

关系分析只从唯一 `RepositoryAnalysisSnapshot` 持有的 baseline/current 源码事实派生，不建立第二套 Git/file snapshot，也不要求安装 CodeGraph。Python 继续提供定义、导入、调用、继承、override、传递可达性、接口 blast radius 与 affected tests；JS/TS 额外提供 symbol-reference usage、动态 prototype composition、owner surface 与 feature dependency SCC；CSS/HTML/C++ 使用各自结构事实，不伪装成 Python function AST。

- 直接调用边消失但目标能力仍可经新路径到达时，报告替代路径，避免把 owner migration 误报成功能删除。
- 接口修改/删除显示可解析的直接调用方、传递依赖与受影响测试；动态框架关系未解析时不得伪装成“影响为零”。
- affected tests 是优先原样运行的回归集合，不是允许自动修改 tests 的清单。
- Graph 只证明结构与影响；职责合理性、需求授权和动态协议语义仍由规则或语义审计裁决。
- 审计报告把三类结果分账：旧规则 finding 继续独立守住 legacy parity；可达性路径用于 resolution gain；QG193/QG194 只计入 RelationGraph new recall，新增召回不得掩盖旧 finding 消失。


## Test Contract

Tests 与 production 分账，但变更 tests 不是默认动作。机器只对本轮变化的测试文件做 AST 差分：统计新增/修改/删除用例、断言/skip 变化，并记录每个修改用例中消失的可观察断言；因此“总断言数增加”也不能掩盖旧稳定断言被删除。源码读取、`inspect.getsource/signature`、源码字符串存在/不存在断言、历史 tombstone 命名等仍标为 QG192 候选。既有测试用例一旦修改或删除，以及命中 QG192 的新增用例，会生成稳定 `TEST-CHANGE-*`。

每个 `TEST-CHANGE-*` 必须说明稳定行为/旧断言为什么失效、真实需求/Bug/测试缺陷证据和可观察验证路径，最终只裁决 `JUSTIFIED/BLOCKING`。`IMPLEMENTATION_COUPLED` 只能 BLOCKING；若源码结构断言实际表达长期架构不变量，只能以 `STABLE_ARCHITECTURE_CONTRACT` JUSTIFIED，并给出独立于本次实现的长期契约证据。普通新增行为测试不因为“新增”自动进入人工账本。

既有测试默认应先原样运行：失败首先视为 production regression 候选，而不是自动修改 tests。历史删除记录不等于长期需求；“确保被删除参数/helper 永不出现”不得仅凭删除历史成为永久测试契约。应优先验证稳定可观察行为；确属长期架构不变量时由架构门禁表达。负向测试只有在验证长期稳定的安全、协议或公开行为不变量时才是正常回归测试。

## Reduction Pass

填写报告后继续审查剩余 Warning、SEM 和新增接口。能安全删除、内联、复用、合并、下沉、收紧类型/契约的必须处理；不设机械“必须减少 N 条”配额。全部确有必要时允许 0 个额外修改，但 KEEP 必须有源码证据。

Reduction 的核心问题是**复杂度被删除还是仅被重新排列**。至少从 concept、branch、mode/state、helper/layer、coupling 五个维度比较 before/after：某个大函数变短但 helper、owner surface 或跨层依赖同步增加，不得仅凭局部指标下降宣称结构债务清零。优先选择能让整段分支、特殊 mode、临时状态或抽象层直接消失的 code-judo；若保留更多层次能换取真实事务、协议、资源生命周期或复用边界，必须给出调用链证据。

## Commit

最终 patch 冻结后必须填写 `## 9. 提交 Commit`。该章节包含机器生成的完整 patch 范围事实，以及一条可直接执行的多行中文 `git commit -m` **建议命令**。它用于验证提交描述能覆盖整个 baseline→target patch，不代表 Guard 已执行或获准执行 Git 写操作。命令必须描述整个 patch，而不是只描述最后修改的文件、单条 finding 或 Reduction Pass 中的局部修复；主题使用 `type(scope): 中文主题`（scope 可省略），正文至少两条中文 `- ` 摘要。实际提交仍只服从当前任务授权；`REVIEW_REQUIRED` 的人工裁决也不等价于提交授权。

若随后任何源码/配置/index 变化导致 `change_digest` 改变，下一次 `audit` 会主动把旧 commit 命令重置为 `PENDING`；`verify` 在命令缺失、仍是占位、格式错误、摘要不足或试图通过 `git config user.*` / `--author` / `GIT_AUTHOR_*` / `GIT_COMMITTER_*` 覆盖贡献者身份时直接 REJECT。`audit` 生成了未完成报告时也返回门禁退出码 3，避免 Agent 把“已生成报告”误认成“审计通过”。

## verify

`verify` 重新计算当前源码强指纹并验证 baseline、ADD/SEM/十一问与 Reduction Pass，拒绝 stale report。若同一 release、profile、显式 diff-base 与源码内容已经产生完整扫描快照，则复用该不可变 ScanReport；任一相关源码/配置/index 输入变化都会强制完整重扫。它不写代码、不补报告、不自动把 BLOCKING 改成 JUSTIFIED。

## 进程完成语义

- `audit` / `verify` 的最终状态输出只在扫描结果、报告读写与门禁计算完成后产生。
- 正常 CLI 返回路径在 stdout/stderr flush 后直接结束一次性进程，不执行与审计结果无关的解释器 teardown/atexit 清理；避免重 AST 或第三方退出钩子把已完成命令拖成长尾。
- 入口与 scan worker 均启用 60 秒重复 faulthandler，并保持到进程实际终止；若 dependency/bootstrap/workflow/worker 或 DONE 后帧清理长期阻塞，会把 Python 栈写入 stderr，但不会降低规则或自动跳过阶段。
