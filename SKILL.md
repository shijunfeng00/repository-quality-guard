---
name: repository-quality-guard
description: 对 Git 或无 Git 源码执行接口复用检索、增量接口文档、多语言质量审计、语义裁决、Reduction Pass 与最终只读门禁。
metadata:
  version: "0.22.5"
  status: stable
---

# Repository Quality Guard

**Version:** 0.22.5
**Status:** Stable
**Distribution:** canonical full-repository ZIP / repository-installed `.agents`

Repository Quality Guard（RQG）是一套面向 Git 或无 Git 源码目录的代码质量审计与开发闭环工具。它把可机械验证的代码事实、接口变化、测试契约和多语言静态分析统一到同一份仓库快照中，再通过结构化审计报告完成语义裁决、Reduction Pass 与最终只读验证。

RQG 当前覆盖 Python、JavaScript、TypeScript、CSS、HTML 与 C/C++，并提供 API catalog、RelationGraph、接口差分、测试契约分析、报告完整性和 release integrity 检查。机器扫描负责事实与候选；需要源码语义判断的项目保留在审计账本中，由使用者完成裁决后再交给 `verify` 重新验证。

## Release identity audit

`v0.20.1` 新增通用 release-identity coupling 审计：

- `QG203`：通用 **CRITICAL**，只覆盖仓库路径/文件名中的 release-like identity（例如版本化测试、fixture、artifact、history/release 目录名）。它属于普通 severity，继续服从 Git baseline/history-aware delta；历史已有 CRITICAL 不会因为严重度本身变成绝对阻断。
- `QG205`：通用 **SEMANTIC** 候选，覆盖文件内容中的依赖/API/协议/schema/迁移版本，以及其他版本、tag、commit/SHA/digest 引用；静态命中本身不直接触发 REJECT。
- `README.md` 与 `README_zh.md` 是仅有的文档豁免；仓库内 migration/release/history 文档不因命名自动豁免。


### 仓库证据 CLI（只读定位，不代替裁决）

处理 QG finding 前，先运行同一个 `scripts/evidence_lookup.py`：

```bash
python scripts/evidence_lookup.py --rule QG004 --repo /repo --api "数据契约消费"
python scripts/evidence_lookup.py --repo /repo --rule QG004 \
  --file runtime/src/topology_facts.py --line 396 \
  --name _incoming_index --kind variable --direction callers --depth 3
python scripts/evidence_lookup.py --repo /repo \
  --file pkg/module.py --line 88 --name Handler --kind class
python scripts/evidence_lookup.py --repo /repo --graph-stats
python scripts/evidence_lookup.py --repo /repo \
  --symbol pkg.module.Consumer.method --variable self.provider --depth 3
```

`--file`（仓库相对路径）、`--line`（1-based）是可直接从告警位置使用的定位契约；
`--kind function/class/variable/auto` 决定追踪关系；`--symbol` 加 `--trace` 也可输出 Trace。
输出包含 `文件:行号`、真实调用点 ± 上下文行、可能形参传导、局部赋值与继承声明。
只有已有源绑定的 `DIRECT_CALL` 可称“已解析”；静态类型/唯一构造来源对应
`MAY_DISPATCH`（源码类型约束的可能目标，不能等同运行时真实调用）；
`MAY_CALLBACK`（标准调度器的源绑定提交点，不能证明任务已执行）；
同名无 owner 证据仅 `[UNKNOWN]` 词法候选。v0.22.5 Graph hotfix 统一
`--symbol` 与 `--file/--line` 为同一证据 Trace：支持函数形参静态注解、未重绑定的
局部构造对象、简单 `self.field` 构造或注入字段；跨对象构造器调用点可沿
`self.field → __init__ 形参 → 调用方实参 → 调用方构造字段` 展示源码赋值候选。
仅当源码显式绑定且已观察调用点未发生类型冲突或未知注入，才建立 MAY_DISPATCH；
祖父类单一路径的方法声明可查询，多继承冲突不猜测 Python 运行时 MRO。
`--graph-stats` 给出函数调用图弱连通分量、最大分量及未连接节点；这是可观测覆盖指标，
不是准确率，不能强行追求 100% 连通。多层属性别名、动态赋值和
开放多态保持 OPEN_WORLD。任何图补边必须有独立外部仓库正反 Cases 的
文件:行号及误连边反例，不得为增加连通率强行提升为 DIRECT_CALL。
跨语言、动态派发、对象别名及不可达性未证明时保持 UNKNOWN，不虚构运行时堆栈。
缓存是可再生的仓库外 JSON 图，按所有分析源码 SHA256 + 分析器身份失效，
同一快照可复用，`--cache-dir` 可指定目录；API Catalog 仍使用原生 BM25 保鲜。
Agent 必须逐个核验真实 Producer/Consumer/Guard/Case；找不到边不等于没有调用者。
任何稳定 QG 的检测器修改必须先有独立正反 Case 及用户裁决。

## 1. 稳定命令

Agent-facing CLI 只保留四个一级命令：

```bash
QG=.agents/skills/repository-quality-guard
python "$QG/scripts/quality_guard.py" doc-generate <repo>
python "$QG/scripts/quality_guard.py" doc-search <repo> "能力描述"
python "$QG/scripts/quality_guard.py" audit <repo>
python "$QG/scripts/quality_guard.py" verify <repo>
```

Portable 模式下，也可以直接从解压后的 Skill 根目录运行：

```bash
python scripts/quality_guard.py audit /path/to/repository
python scripts/quality_guard.py audit /path/to/repository --profile my-profile
```

### `doc-generate`

生成或增量刷新仓库 API catalog。支持：

```bash
python scripts/quality_guard.py doc-generate <repo> --files path/a.py,path/b.py
```

Catalog 用于新增接口前的复用检索和 owner 定位，不替代源码阅读。

### `doc-search`

在 API catalog 中执行能力检索。每次检索前都会对当前生产源码执行强内容摘要保鲜；即使编辑工具保留了文件大小和 mtime，也不会复用过期 catalog。`docs/api-reference/` 是 RQG 拥有的可再生派生产物，不进入业务变更、release-identity 或质量门禁；`doc-generate` 会自动开启新的检索轮次，`doc-search` 会按当前源码 digest 自动淘汰旧 search ledger，无需 Agent 手工删除目录。

```bash
python scripts/quality_guard.py doc-search <repo> "state transaction ownership"
```

默认检索策略保持稳定；Profile 可以注册可选 `SearchStrategy` 对现有候选进行重排或过滤。

### 审计模式（Agent 不得私自降低门禁）

默认不传 `--mode`，执行 `normal`。只有用户明确授权，才可以选择：

- `--mode normal`：正常模式，Git 可用时做增量比较，无 Git 时仅审当前静态状态且所有历史 Delta 为 N/A。
- `--mode ignore-diff`：忽略diff模式，不使用 Git 基线，但不豁免当前质量规则；允许 REVIEW_REQUIRED。
- `--mode cleanup`：强制清零模式，要求普通生产 C/E/W 清零，最终只允许 ACCEPT 或 REJECT。

`--mode ignore-diff/cleanup` 不允许与 `--diff-base` / `--staged` 并用。旧 `--debt-mode progressive/cleanup` 保留兼容映射。`audit` 和 `verify` 必须使用相同模式；报告首页记录模式、基线可用性和当前关系图。**无 `.git` 不构成用户授权忽略 diff 的证据。**

### 默认自动语言识别与原生事实（v0.22.5）

`audit <repo>`、`verify <repo>` **不要求** `--language`、`-std=...` 或手工指定 Python/C++/JS。
Python 的源文件中唯一、无装饰器、无重绑定的模块级调用默认自动检查 QG209
参数绑定；不要求配置 Pyright/Pyrefly。该能力只给出 OPEN_WORLD Warning，
动态调用和不确定目标保留 UNKNOWN，不推断封闭世界。
Python/JS/TS/CSS/HTML 及 C/C++ 通过仓库快照识别，只有权威编译数据库
中的 C++ 翻译单元可用原项目编译器复查语法并输出 `QG212 ERROR`。
没有构建契约、缺依赖或不可用时输出 `QG208` 并要求 REVIEW_REQUIRED，
不可擅自猜 C++ 标准或把零发现当成无缺陷。CMake/Make/Meson 自动准备
原始编译契约、Java Provider 与完整跨语言语义 IR 仍在计划中，不得宣称完成。

**回归验收唯一有效事实源：** 使用经完整性检验的 Skill 官方启动器执行
`audit`，保存真实自动生成的 `修改说明.md`，完成必要人工裁决后运行只读
`verify`，记录源码与 Skill 身份、Profile **名称及来源**、CLI、产物 SHA256、退出码、裁决。
Portable 的专用 Profile 只在发布目录中实际存在且项目目录名精确匹配时自动启用；
Installed Profile 必须是已封存的授权 Profile。报告元数据中的 `Profile` 和
`Profile 来源` 来自真正的扫描配置，而不是目录猜测或 CLI 预期值。
若运行包缺少应有的 Profile，通用报告只代表通用规则，**不能充当专用 Profile 验收**；
未知 Profile 解析状态必须显式失败，禁止谎称通用。隔离扫描 worker 必须从同一
完整性封印的 Skill 加载代码和 Profile，不得从调用者工作目录导入另一份 RQG。
Pyright、GCC、CTest、pytest 和内部 `scan_target` 只能作为独立 Oracle 或研发
定位信息，不能冒充正式 QG Findings、已生成报告或 `verify` 通过。

研发中的受限 QG210 只审查可证实的直线构造顺序；QG214 只证明
宽泛异常被处理器无记录地转换为静态可知 fallback。两者均为
OPEN_RUNTIME WARNING，必须人工核对业务协议，不得把候选数量当作真实 BUG 数量。
QG216 以权威 TypedDict Schema 与确定的关闭字典字面量生产者验证必需字段缺失；
动态展开/动态键和容器化返回必须保留 UNKNOWN。
QG215 对源码明确的宽异常 `pass`/`continue`/局部哨兵赋值单语句处理器
出具未观察到异常的路径证书，仅列条件性 WARNING，不把 best-effort 当成已证实 BUG。
开发阶段针对部分仓库的抽检独立于正式全仓回归节点，不得折算完成度。

### `audit`

扫描当前仓库与固定 Git baseline，生成/刷新仓库根目录的 `修改说明.md`：

```bash
python scripts/quality_guard.py audit <repo> --diff-base <commit>
```

`audit` 会写入机器事实，并保留需要人工完成的 ADD / SEM / Reduction / Commit 等语义字段。报告未完成时返回 `UNVERIFIED`，不能作为最终质量结论。

### QG 编号查询（修复 Finding 的必经步骤）

收到一个或多个 `QGxxx` Finding 后，**在修改生产代码、填写 JUSTIFIED 或批准修复前**，必须先按编号检索权威修复规则；不得仅依据报告中的短消息或严重度直接整改。

```bash
python "$QG/scripts/evidence_lookup.py" --rule QG123 --rule QG153
# 原 `rule_lookup.py -QG123` 保持兼容，但新任务推荐统一查询入口：
python scripts/evidence_lookup.py --rule QG153 --rule QG176
```

命令直接从 `references/RULES.md` 批量输出 Markdown：规则定义、对应规则族的真实风险、正确修复方向、禁止做法和原文行号。可离线执行，无须目标仓库、API catalog 或额外依赖。编号未知时明确失败，不猜测修复办法。

查询后仍必须核验 Finding 的现场源码与上下游 Producer → Schema → Consumer、真实 Owner 和调用方；`RULES.md` 是修复指导而不是静态规则绝对正确的证明。结论矛盾时登记可复现的 RQG BUG，不得为了消掉 Finding 破坏正确契约。对同一 owner / 契约的批量 Finding 可合并检索和裁决，无须重复写长报告。

### QG003/QG004 已裁决的稳定规则变更（P0）

本次经用户授权，修复普通类/多层继承 `get` 被误判为 Mapping，
保留真实字典和 `self._data.get` 路径；QG004 将明确的空结果与
随机非空哨兵严格区别，结合返回注解、必需 Schema 与稀疏生产者 Case。
在 `RULES.md` 查询该编号时，必须同时展示新修复方向、正反 Case，
并使用本 CLI 追查真实方法 owner / 参数来源及所有可见 Producer；
映射字段需要时由业务 Fail-Fast，而不是为降低告警直接放弃检查。
本次不改变 QG033。所有规则实现仍需官方全量自审和只读 verify
通过后方可宣称 v0.22.5 正式发布。

### 仓库证据查询（P0：QG 规则 + 静态调用图统一入口）

对 `.get()`、可能缺失、空值/异常兜底、继承分派或跨函数缺陷整改，**不仅检索 QG 规则，还必须主动查询 upstream callers / owner / source**；不能仅凭当前代码片段、类型系统允许或告警消息判断业务需求。

```bash
python "$QG/scripts/evidence_lookup.py" --rule QG003 --rule QG004 \
  --api "consumer validation" --repo /path/to/repository \
  --symbol package.module.consumer --direction callers --depth 3
# 查询单个符号的直接调用上游/下游，缺省只查上游
python "$QG/scripts/evidence_lookup.py" --repo /path/to/repository \
  --symbol package.module.consumer --direction both --depth 2
# 不涉及源码图时，只查权威规则
python "$QG/scripts/evidence_lookup.py" --rule QG033
```

`--api` 直接复用现有 `doc-search` 的 BM25 API Catalog 和强源码摘要保鲜，仅更新可再生的 `docs/api-reference/` 缓存（无新图数据库），不复刻 BM25/parser；候选搜索不证明 owner 唯一，必须明确 `--symbol` 选择要追的函数。工具复用现有 RepositoryTopology，不运行被审计代码，不额外安装图索引。只输出静态已解析的直接调用边、声明位置和有限源码片段，**不证明某条调用路径实际执行，也不证明全部合法 caller 已发现**。多态、回调、跨语言、动态调用等未覆盖的边必须明确按 UNKNOWN 处理；无边不得推出业务不可达。`--direction callers` 用于从报错函数逆向看已知调用链；`--direction both` 帮助人工阅读相关的 producer/consumer，不是强制无限展开全图。

Agent 必须实际打开相应函数体、schema、校验入口和测试 Case，证明真实可达的合法缺失是否存在、guard 是否支配当前消费点、值是否变异以及失败时是否有独立业务语义。不存在合法缺失 Case 时，required contract 直接访问并 fail-fast；不得因为 `NotRequired` 或 `.get()` 本身语法有效就放行。处理稳定 QG 疑似误报时，记录成对 Cases 并上报人工裁决；不可擅改既有稳定 QG。旧 `rule_lookup.py` 作为兼容命令保留，无需学习第二套图服务。

### `verify`

只读重新扫描源码并校验最新报告：

```bash
python scripts/quality_guard.py verify <repo> --diff-base <commit>
```

`verify` 不补写报告、不修改源码，也不会把未完成或 BLOCKING 的裁决自动改成 JUSTIFIED。最终质量审计结论统一为 `ACCEPT`、`REVIEW_REQUIRED` 或 `REJECT`；单项测试与文件完整性检查仍可用 `PASS` 表示测试通过。

## 2. 标准开发流程

RQG 的推荐闭环如下：

1. fresh clone 或开发任务开始时确认 RQG 环境与托管 `pre-push` 门禁。缺 hook 时运行 `python .agents/skills/repository-quality-guard/scripts/git_hook_install.py .`；已有非托管 `pre-push` 时必须明确报告冲突，不得覆盖，也不得使用 `git push --no-verify` 绕过门禁。
2. 冻结 accepted design baseline。用户明确指定 revision 时使用该 revision；否则使用任务开始时最后一个可确认的目标分支基线。
3. 新增 function / method / class / helper 前先执行 `doc-generate` 与 `doc-search`，检查已有 owner、调用方和可复用能力。
4. 实现变更并先运行受影响的既有测试。测试默认代表稳定行为或契约，不应为了迁就当前实现而机械修改。
5. 执行 `audit`；按本节编号查询批量检索每条待处理 QG 的 `RULES.md` 修复方向，再阅读机器事实、真实源码和上下游，完成新增接口、语义候选、测试契约和历史风险裁决。
6. 执行 Reduction Pass，检查新增 concept、branch、helper、mode、layer、state 或 coupling 是否仍能删除、内联、合并、复用、下沉或收紧。
7. 冻结最终 patch，并在报告中填写覆盖整个 patch 的提交建议。
8. 执行只读 `verify` 得到最终质量状态。
9. `git push` 时由托管 `pre-push` 对即将推送的 HEAD 再次执行只读 `verify`。退出码 `0`（ACCEPT）与 `5`（REVIEW_REQUIRED）可以继续；`REJECT`、报告门禁失败、完整性失败或其他验证错误必须阻断 push。

`修改说明.md` 是审计交付物，默认不进入业务 Git commit，除非项目明确要求。Git hook 属于 clone 本地元数据，不会随普通 Git commit 自动复制，因此每个 fresh clone 都必须重新确认安装状态。

## 3. 退出码

稳定 CLI 使用以下主要退出码：

| Exit code | 含义 |
|---:|---|
| `0` | 命令成功；`audit` 为 `READY_FOR_VERIFY`，或 `verify` 为 `ACCEPT` |
| `1` | `verify REJECT`：静态或语义门禁拒绝 |
| `2` | CLI、Profile、依赖或运行环境错误 |
| `3` | 报告门禁未完成或报告契约拒绝 |
| `4` | Skill / Installed release 完整性失败 |
| `5` | `verify REVIEW_REQUIRED`：接口/协议账本需要人工确认，但不是 REJECT |

调用方应按退出码和日志中的状态共同判断结果，不应把 `audit` 的报告生成动作视作最终通过。

## 4. Portable 与 Installed 模式

### Portable

Canonical full-repository ZIP 是唯一完整、自包含、可离线 bootstrap 的交付物。它保留：

- 第一方 runtime；
- `offline/wheelhouse`；
- `offline/node_modules.zip`；
- 依赖锁；
- 完整本地 Profile catalog；
- `.git` 元数据与完整 authoring/source tree。

Portable 模式可显式使用：

```bash
--profile <name-or-path>
```

若未显式指定 Profile，RQG 只在**仓库目录名与一个实际存在的 Profile 名完全一致**时自动选择，并明确向用户打印自动匹配原因；否则使用 generic policy，不根据项目文件特征猜测 Profile。

### Installed

安装入口：

```bash
python runtime/deploy.py \
  <skill-root> \
  <repo>/.agents/skills/repository-quality-guard \
  [--profile <name-or-path>]
```

安装器验证源 Skill、构造 staging tree、裁掉 Portable-only 离线介质、冻结所选 Profile 与 Policy，并原子替换旧安装。

Installed 模式的 Profile selection 是不可变安装状态。运行时再次传入 `--profile` 会被拒绝；修改 installed policy 或 Profile payload 会触发 release integrity 门禁。

## 5. Profile 扩展系统

Profile 是项目策略插件，不是 JSON DSL。

推荐布局：

```text
profiles/<profile-name>/
├── profile.json
├── extension.py
├── PROFILE.lock        # 存在自定义规则时使用
├── AGENTS.md           # 可选；Profile 命中且宿主缺失时直接复制到根目录
├── README.md           # 可选；Profile authoring 说明，不安装
└── tests/              # 可选；Profile authoring tests，不安装
```

`profile.json` 只负责名称、版本、entrypoint 和少量声明式配置。复杂规则与策略写在 Python 中。若 Profile 提供宿主指令，使用 `"agents_file": "AGENTS.md"` 指向完整文件。

`extension.py` 固定导出：

```python
class Profile(QualityGuardProfile):
    def configure(self) -> None:
        super().configure()
        ...
```

主要扩展接口：

- `QualityGuardProfile`：项目策略与规则装配；
- `RulePack`：组合一组相关规则；
- `QualityRule`：自定义规则；
- `RuleContext`：RQG 提供的只读仓库事实模型；
- `Finding`：规则的标准输出；
- `SearchStrategy`：可选检索重排；
- `ReportExtension`：可选报告扩展。

Profile rule 的稳定身份首先是 `rule key`。自定义显示编号使用 `QG10000+`。未显式指定编号时，只能在 authoring 阶段由：

```bash
python runtime/profile_build.py profiles/<profile-name>
```

分配并冻结到 `PROFILE.lock`；运行时不得重新编号，历史编号不回收。

Profile 可以关闭普通项目规则或追加自定义规则，但不能关闭 release integrity、报告完整性等 Guard 自身可信边界。

## 6. `AGENTS.md` 所有权

RQG 不拥有宿主仓库根 `AGENTS.md`。

部署规则固定为：

- 根 `AGENTS.md` 已存在：逐字节保留，不覆盖、不 merge、不 append；
- 根 `AGENTS.md` 不存在且 Profile 提供 `AGENTS.md`：直接复制该完整 Profile 指令文件；
- 根 `AGENTS.md` 不存在且 Profile 未提供 `AGENTS.md`：创建通用 `templates/AGENTS.template.md`；
- 并发部署期间若文件被其他进程抢先创建：安装失败并回滚，不覆盖新出现的宿主文件。

Profile 的 `AGENTS.md` 是 deploy-time bootstrap 资源，不进入 Installed runtime Profile payload。Profile 的 `README.md`、`tests/` 等 authoring 资产同样不进入 Installed runtime。

## 7. Git 持久化

项目内：

```text
.agents/skills/repository-quality-guard/
```

是需要被 Git 持久化的第一方协作运行时，不是本地缓存。正式升级应全量替换旧 Skill，并用：

```bash
git add -A -- .agents/skills/repository-quality-guard
```

记录新增、修改和删除。

`修改说明.md` 与运行时相反：它必须作为审计附件交付，但默认不进入业务 commit。托管 `.git/hooks/pre-push` 同样不进入 Git 历史；Agent 必须在 fresh clone / 任务开始时确认它存在，并通过 `scripts/git_hook_install.py` 安装缺失 hook。已有非托管 hook 不得被覆盖。

## 8. 完整性与依赖

RQG release 通过 `runtime/MANIFEST.sha256`、`runtime/RELEASE.lock` 和 launcher seal 校验受保护文件。运行过程中生成的 `__pycache__`、`*.pyc`、`*.pyo`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache` 属于可再生缓存，不参与 QG990 runtime integrity；release builder 仍会在正式发行介质中剥离它们。Portable Skill 与 Installed tree 使用不同 distribution policy：

- `skill`：允许并要求正式离线依赖介质；
- `agents`：只保留第一方 runtime 与锁，不携带完整 wheelhouse / node_modules archive。

依赖 bootstrap：

```bash
python runtime/install_dependencies.py
```

Canonical full-repository ZIP 与 `.agents` 安装态使用同一个入口。Agent 不得自行决定 pip/npm 安装步骤，也不得因为环境准备失败而跳过审计；只运行 `python runtime/install_dependencies.py`。安装器固定按“精确宿主环境 → 本地/用户缓存离线介质 → 网络探测 → 有界在线安装”的顺序处理。完整 canonical ZIP 自带 wheel 与 Node parser payload，并会把可复用介质种入仓库外的 QG 用户缓存；`.agents` 默认不复制这些大文件，但同一个 CLI 会自动发现用户缓存或本地 `offline/`。只有本地介质不可用时才探测 PyPI/npm registry；各最多 3 次、每次 3 秒，并明确打印正在使用离线还是网络安装、失败原因和补救办法，禁止静默联网。`RQG_OFFLINE_ONLY=1` 时完全禁止网络。命令失败后不得把 QG 标记为通过或直接继续交付。

扫描生命周期同样不得由 Agent 猜测处理：同一仓库已有 cold scan 时，后续审计必须 single-flight 排队并打印等待日志，不得再启动第二份重扫描；worker 每 5 秒发布心跳，父进程定期打印 phase；worker 异常退出、心跳失联、Node parser 超时或父进程收到 TERM 时必须清理本次运行拥有的完整子进程树并 fail-loud。调用 RQG 的宿主进程消失时，顶层 QG 也必须退出，避免遗留后台审计抢占后续任务资源。

如果宿主 Agent/Harness **明确支持**可持续的后台/异步任务或受控 session polling，并且完整 cold scan 预计会超过宿主单次同步调用窗口，Agent 可以优先只启动一份受监督的长审计并周期轮询其 heartbeat、phase、日志和最终退出码；这只是宿主执行策略建议，不是 RQG 的运行前提。宿主不支持时不得假定存在该能力，也不得使用 `disown`/fire-and-forget 脱离 ownership，更不得因一次调用窗口结束就并发启动第二份 scan。

`audit` / `verify` 的扫描 worker 每 15 秒向外层报告存活阶段；正常审计没有自动截断时间。仅在诊断故障时可显式设置 `RQG_SCAN_TIMEOUT_SECONDS`，到时只能中止并保留失败证据，绝不产生不完整的通过结论。worker 失败或父进程中断时，审计失败并清理 worker 进程树及一次性 IPC；必须修复原因后重新完整审计。

Node.js/npm/Clang 属于宿主系统能力；只有显式 `--install-system` 或 `RQG_INSTALL_SYSTEM_DEPS=1` 才允许安装系统依赖。

## 9. Release 结构

唯一 canonical full-repository ZIP：

- 包含 `.git`、完整第一方 Skill、`README.md`、`README_zh.md`、`dev-tests/`、authoring/release tools、全部本地 Profile 与离线依赖；
- Profile 与离线二进制仍遵守 `.gitignore`，因此它们可以存在于本地完整 ZIP，但不会因此进入 public Git 历史；
- 不包含构建缓存、一次性审计工作目录或 `修改说明.md`。

不再维护 public/internal 两套 ZIP。GitHub 分发使用仓库 + npx 安装路径；若未来单独发布 GitHub Release asset，也应复用同一 canonical ZIP 规则。deploy 到 `.agents` 时继续剥离 `README.md`、`README_zh.md`、`dev-tests/`、`tools/`、`offline/`、wheelhouse、Node 离线 payload 与 Profile authoring 文件。

## 10. 审计原则

- Critical / Error / Warning 独立计数，历史下降不能抵消本轮新增或升级；
- 语义 finding 需要沿 owner / caller / protocol / state / test 路径给出可验证证据；
- fallback、宽松契约、suppression、return-shape、依赖面与启发式行为必须进入相应语义裁决；
- 不得通过修改 Skill runtime、manifest、release lock 或测试断言来规避门禁；
- 行为正确和测试通过不等于 Reduction 完成；存在更小、更直接的可验证设计时应继续收敛；
- 最终质量结论只来自最新源码上的 `verify`。
- Agent 不得使用 `git push --no-verify` 或其他方式绕过托管 `pre-push`；推送前必须让 hook 对待推送 HEAD 重新执行 `verify`。

## 11. 参考文档

以下路径相对于 Repository Quality Guard Skill 根目录：

- `references/RULES.md`：QG 规则与严重度说明；
- `references/AUDIT.md`：报告、审计和验证契约；
- `references/CODING_GUIDE.md`：开发、接口、测试和 Reduction 约束。
