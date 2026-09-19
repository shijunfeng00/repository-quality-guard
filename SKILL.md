---
name: repository-quality-guard
description: 对 Git 仓库执行接口复用检索、增量接口文档、Python/JS/TS/CSS/HTML/C++ 质量审计、语义裁决与最终只读门禁。
---

# Repository Quality Guard

这是低自由度质量门禁。机器负责扫描事实与候选；模型负责阅读真实源码、做语义裁决并继续减法，不得自行弱化规则。

## 固定流程

1. 修改前先冻结 **accepted design baseline**：用户明确指定 commit 时以该 commit 为准，否则使用任务开始时最后一个可确认的目标分支基线。该 revision 在整轮 `audit/verify` 中保持不变，不得移动到本轮自己生成的中间 commit 来缩小差分，也不得按 author 姓名筛选“权威提交”。
2. 新增 function/method/class/helper 前，先执行 `doc-generate`，再用 `doc-search` 检索相关能力；阅读 Top10 中相关源码与调用方。
3. 修改 production 后先运行受影响的既有测试；既有 tests 默认是稳定行为/契约基线，不得为了让当前实现通过而顺手改断言。只有真实需求变化、Bug 修复导致旧行为失效、测试自身缺陷，或保持行为不变的测试重构，才允许修改既有测试。随后执行 `audit`，填写 ADD/SEM/TEST-CHANGE/十一问；发现 BLOCKING 或可安全削减项时继续修改。
4. 所有语言事实都由统一 `RepositoryAnalysisSnapshot` 持有：Python 保持既有 AST/RelationGraph 路径；JS/TS/CSS/HTML/C++ 由 language adapter 从同一 baseline/current 源码快照派生。优先查看 callers/callees、传递可达性、owner surface、隐藏 SCC、接口 blast radius 与 affected tests；不得建立第二套独立 Git/file snapshot、数据库或外部 CodeGraph 运行依赖。
5. 重新 `audit`，完成 Reduction Pass：再次检查剩余 Warning、语义候选和新增接口能否删除、内联、合并、复用、下沉或收紧契约。
6. 最终 patch 冻结后填写报告 `## 9. 提交 Commit`：必须提供一条描述整个 patch 的多行中文 `git commit -m` **建议命令**；随后执行 `verify`。报告中的建议命令只是审计契约，不代表 Guard 或执行模型获准实际提交。缺失 Commit、占位 Commit、patch 变化后的旧 Commit 或身份覆盖命令都按 QG984 拒绝。`audit` 只负责生成/刷新报告，报告未完成时退出码为 3（UNVERIFIED），不得当成通过；只有只读 `verify` 可以给出最终 PASS/REVIEW_REQUIRED/REJECT。实际执行 `git commit` 只服从当前任务的明确授权；`REVIEW_REQUIRED` 的人工确认是质量裁决，不等价于 Git 提交确认。

## 审计交付闭环

- 任何代码修改任务，无论用户要求直接修改、patch、diff、mbox、commit、bundle、zip 或完整仓库，**代码交付形式都不能替代质量审计交付**。必须生成并完成仓库根目录 `修改说明.md`，按报告要求修代码、复跑 `audit`、完成 Reduction Pass，再以 `verify` 得出最终状态。
- `修改说明.md` 是**必须交付给用户的审计附件**，但默认是审计运行产物，**不进入业务 Git patch/commit/mbox**；除非用户明确要求纳入 Git。`.gitignore` 忽略它不等于可以不生成、不填写或不交付。
- `audit GENERATED`、退出码 3、报告仍有 `PENDING`/占位、报告 validator 未通过，均表示审计**未完成**。不能因为 mbox/patch 已生成、replay tree 一致、测试通过或工作区 clean 就结束。
- 最终回复必须同时给出：代码交付物、`修改说明.md`、报告门禁结果、静态事实门禁、模型语义/最终状态，以及未执行或因环境阻断的验证。只给 mbox/patch/zip 链接而不报告质量结论，视为任务未完成。
- 若最终 `verify` 后又修改任何代码、测试、配置或 `修改说明.md`，必须重新 `audit`/补报告并再次 `verify`；不得沿用陈旧结论。

## Git 持久化闭环

- 仓库内 `.agents/skills/repository-quality-guard/**` 是**必须被 Git 持久化的协作运行时**，不是本机安装缓存。根 `AGENTS.md` 属于宿主共享协调状态；QG 不拥有覆盖权，只有在文件原本缺失且本轮 deploy 创建了模板时才可能属于同一次升级提交；普通项目没有对应模板时不得创建、覆盖或强行纳入 Agent 专用根指令。只在当前工作区 deploy 成功但未纳入 Git，视为 Skill 升级未完成：其他同事 `git pull` / fresh clone 后无法执行 Guard，等同于没有部署。
- 用户要求安装或升级仓库内 Repository Quality Guard 时，**默认且正式的升级方式永远是全量替换**：不要求目标仓库先处于相邻版本，无论旧 `.agents` 是 v0.8、v0.13、v0.17、残缺安装或带废弃布局，都以当前正式 Skill 的安装态 manifest 为唯一目标树，使用 staging + 原子替换删除所有目标版本不存在的旧文件。增量 patch 不是安装前置条件。
- deploy 后将**整个** `.agents/skills/repository-quality-guard` 用 `git add -A --` 纳入一次独立 Skill 升级提交；若 deploy 本轮确实同步了根 `AGENTS.md`，再把该文件一并纳入。必须同时记录新增、修改与旧版删除，不能只提交已跟踪旧文件，也不得为了通用仓库升级而创建/覆盖 Agent 专用 `AGENTS.md`。该提交使用实际执行者当前 Git identity，不伪装成设计基线作者。用户明确要求“只安装到本机、不提交”时才可例外，并必须明确说明该工作区不能作为可 pull 的完整交付。
- deploy 后提交前必须检查 `git status --short --untracked-files=all` 与 `git check-ignore`；任何必需 `.agents` 文件为 untracked/ignored 都不得声称升级完成。提交后必须用 `git ls-files` / `git cat-file` 证明 `scripts/quality_guard.py`、`runtime/src/workflow.py`、`runtime/src/integrity.py` 与 `SKILL.md` 已存在于 `HEAD`；只有本轮同步过根 `AGENTS.md` 时才要求同时验证它。
- 最终还必须从该 **Git commit** 做 fresh-clone/replay smoke：clone 中无需原 `.skill.zip` 即能找到完整 `.agents` 第一方 runtime，并实际执行 `doc-generate → audit → 完成修改说明.md → audit READY_FOR_VERIFY → verify`。第三方依赖缺失时运行 `.agents/.../runtime/install_dependencies.py` 安装锁定环境；不得把 Git-tracked launcher/runtime 缺失解释为依赖问题。
- 托管 `pre-push` hook 是可选协作门禁；项目或 Profile 明确要求时运行 `.agents/skills/repository-quality-guard/scripts/git_hook_install.py <repo>`。hook 不进入 Git、不得覆盖已有非托管 hook，并调用当前安装策略下的 `verify <repo>`；报告缺失/未完成/陈旧或 verify REJECT 时拒绝 push。
- `修改说明.md` 与上述 Git 持久化相反：它仍是必须交付的审计附件，默认不进入业务或 Skill 升级 commit。**运行时必须进 Git，审计报告必须交付但默认不进 Git。**

## 合并协作基线

- 通用任务按用户指定 revision 或任务起点冻结 accepted design baseline；不得按 author 姓名推导“权威提交”。
- 若某项目需要额外的协作/合并语义，应由该项目的 Profile/AGENTS 明确声明；Core 不内置任何组织、作者、仓库或分支身份规则。
- 设计权威与 Git 作者身份解耦：新提交使用实际贡献者 identity，历史提交遵守 Git 原生 author/committer 语义。

## 唯一四个质量命令

```bash
QG=.agents/skills/repository-quality-guard
python "$QG/scripts/quality_guard.py" doc-generate <repo>
python "$QG/scripts/quality_guard.py" doc-search <repo> "能力描述"
python "$QG/scripts/quality_guard.py" audit <repo>
python "$QG/scripts/quality_guard.py" verify <repo>
```

环境与本地 Git hook 是安装辅助入口，不增加质量命令：

```bash
python "$QG/runtime/install_dependencies.py"
python "$QG/scripts/git_hook_install.py" <repo>
```

Portable 模式可显式追加 `--profile <name-or-path>`；未显式指定时，只在“仓库目录名与一个实际存在的 Profile 名完全一致”时自动选择，并把选择原因打印给用户。Installed 模式使用 deploy 时冻结并受 seal 保护的 Profile，不接受运行期 `--profile`。接口文档支持 `doc-generate --files path/a.py,path/b.py` 范围刷新；`doc-search` 会自动检查 catalog 新鲜度并只重解析变化文件。

## Profile 扩展开发

- Profile 是可选项目策略插件，不是 JSON DSL。`profile.json` 只声明名称、版本、`entrypoint`、模板与少量数据配置；复杂规则必须写 Python。
- `entrypoint` 只允许模块路径，模块必须固定导出 `class Profile(QualityGuardProfile)`；不得由 JSON 指定任意类名，也不得在 `__init__` 中产生注册副作用。项目策略在 `configure()` 中显式注册，并在构建后冻结。
- 组合规则使用 `RulePack`；自定义规则继承 `QualityRule`，只读取只读 `RuleContext` 并返回标准 `Finding`。Profile 可以追加 `SearchStrategy` / `ReportExtension`，但不得接管 exit code、release seal、最终门禁或报告可信边界。
- 新增自定义规则的稳定身份首先是 `rule key`；显示编号使用 `QG10000+`。未显式编号的规则只能在 authoring 阶段由 `runtime/profile_build.py` 分配并写入 `PROFILE.lock`，运行时不得动态重新编号；历史编号不回收。
- Portable 模式可以显式 `--profile` 或按仓库目录名精确自动匹配；Installed 模式只使用 deploy 时冻结并受完整性 seal 保护的 Profile。

## 不可绕过

- Critical/Error/Warning 分档独立；历史下降不能抵消本轮新增/升级。
- 本轮 fallback、宽松契约、return-shape、suppression、依赖面、启发式候选按报告要求做语义裁决。
- 不得用报告文字豁免未授权 QG178；不得以“用户体验”为理由吞掉未知程序错误或伪造成功。
- 报告写完不是终点；Reduction Pass 必须实际检查还能否进一步削减；没有最终 `verify` 结果就没有质量结论。
- accepted design baseline 是 revision/用户明确设计授权，不是某个作者身份；无本轮明确需求时优先保持该基线已有 public interface、protocol 与 owner，不能因为实现方便自行漂移。
- 不得直接修改 Skill runtime、manifest 或 release lock 来规避检查。
- tests 不是实现快照：纯实现重构不应驱动测试同步漂移；源码字符串/private helper/“确保刚删除的名字永不出现”只可作为语义候选，除非能证明长期稳定架构/安全/协议不变量。
- 行为正确、测试通过和静态 Delta=0 不足以证明质量通过；若存在有源码依据、能直接减少 concept/branch/helper/mode/layer/state/coupling 的更简单设计，必须在 Reduction Pass 中实现，或用真实调用链说明为何当前方案更简单。不得用拆函数、拆文件、薄 wrapper 或新增 mode 把复杂度从一个指标搬到另一个指标。
- 语义 finding 必须遵循 `hypothesis → trace → evidence → finding`：能沿 owner/caller/protocol/state/test 路径验证的必须追到底。未经验证的“如果/可能/假如”不得升级为 BLOCKING；高严重度结论必须给出完整失败路径和可观察后果。

## 发布与安装边界

- 正式发布只提供一个**完整、全量、可离线独立运行的 `.skill.zip`**：包含第一方 QG 代码、规则、模板、精确依赖锁，以及 `offline/wheelhouse` / `offline/node_modules.zip` 离线依赖介质；不包含 tests、缓存或历史 release 布局。
- **`.skill.zip` 与安装态 `.agents` 是两种不同使用模式。** 外部 Skill 必须在无网络且宿主缺少锁定 Python/Node 包时仍可用随包离线介质完成 bootstrap；`runtime/deploy.py` 安装到仓库时则必须裁掉 `offline/`、wheel、`node_modules`，只保留完整第一方运行时。不得为了 `.agents` 瘦身而删除 `.skill.zip` 的离线能力。
- 项目内 `.agents/skills/repository-quality-guard` 必须直接包含 `scripts/quality_guard.py`、`scripts/git_hook_install.py`、`runtime/install_dependencies.py`、依赖锁、全部 `runtime/src/*.py` 与 `runtime/src/multilang_parser.js`。这些文件进入完整性 manifest；任一缺失都不是可运行安装。
- `python runtime/deploy.py <skill-root> <repo>/.agents/skills/repository-quality-guard [--profile <name-or-path>]` 是通用且唯一的全量升级入口。它先验证正式源包与锁定依赖，再在 staging 构造完整目标树并原子替换旧目录；目标仓库当前 QG 版本不构成前置条件，旧版独有文件不会残留。根 `AGENTS.md` 属于宿主共享协调文件：已存在时必须逐字节保留；仅当缺失时才创建，优先使用 Profile 的 `AGENTS.template.md`，否则使用通用 `templates/AGENTS.template.md`。Profile 与策略在安装阶段冻结并进入 release seal，安装后运行时不再接受 `--profile`。
- fresh clone 缺少第三方依赖时运行 `python .agents/skills/repository-quality-guard/runtime/install_dependencies.py`。安装器按 `runtime/dependencies.lock.json` 复用精确宿主版本，否则用 pip/npm 在线安装精确版本；依赖 cache 位于仓库外，不进入业务 Git。`RQG_OFFLINE_ONLY=1` 只允许已有精确依赖/cache并在缺失时 fail-loud。
- Node.js/npm/Clang 是宿主系统能力；只有显式 `--install-system` 或 `RQG_INSTALL_SYSTEM_DEPS=1` 才允许安装器调用 apt/dnf/yum/pacman/brew。不存在因为系统能力缺失而偷偷降级到弱解析。
- 如果用户明确要求针对某个旧仓库交付 patch/mbox，必须先对该仓库**真实当前 HEAD 执行全量升级**，再导出 HEAD→完整目标树的差异；不能要求用户先逐版本升级，也不能提供只包含相邻版本局部文件的“安装补丁”。

以下路径**均相对于本 Skill 根目录 `repository-quality-guard/`，不是被审计仓库根目录**：`references/RULES.md`、`references/AUDIT.md`、`references/CODING_GUIDE.md`。
