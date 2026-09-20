---
name: repository-quality-guard
description: 对 Git 仓库执行接口复用检索、增量接口文档、多语言质量审计、语义裁决、Reduction Pass 与最终只读门禁。
metadata:
  version: "0.20.2"
  status: stable
---

# Repository Quality Guard

**Version:** 0.20.2
**Status:** Stable
**Distribution:** Portable `.skill.zip` / repository-installed `.agents`

Repository Quality Guard（RQG）是一套面向 Git 仓库的代码质量审计与开发闭环工具。它把可机械验证的代码事实、接口变化、测试契约和多语言静态分析统一到同一份仓库快照中，再通过结构化审计报告完成语义裁决、Reduction Pass 与最终只读验证。

RQG 当前覆盖 Python、JavaScript、TypeScript、CSS、HTML 与 C/C++，并提供 API catalog、RelationGraph、接口差分、测试契约分析、报告完整性和 release integrity 检查。机器扫描负责事实与候选；需要源码语义判断的项目保留在审计账本中，由使用者完成裁决后再交给 `verify` 重新验证。

## Release identity audit

`v0.20.1` 新增通用 release-identity coupling 审计：

- `QG203`：通用 **CRITICAL**，只覆盖仓库路径/文件名中的 release-like identity（例如版本化测试、fixture、artifact、history/release 目录名）。它属于普通 severity，继续服从 Git baseline/history-aware delta；历史已有 CRITICAL 不会因为严重度本身变成绝对阻断。
- `QG205`：通用 **SEMANTIC** 候选，覆盖文件内容中的依赖/API/协议/schema/迁移版本，以及其他版本、tag、commit/SHA/digest 引用；静态命中本身不直接触发 REJECT。
- `README.md` 与 `README_zh.md` 是仅有的文档豁免；仓库内 migration/release/history 文档不因命名自动豁免。

`QG192` 仍独立负责 implementation-history/change-detector test；只有写入具体版本、RC、tag 或 SHA 身份时，才同时进入 QG203/QG205。

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

在 API catalog 中执行能力检索：

```bash
python scripts/quality_guard.py doc-search <repo> "state transaction ownership"
```

默认检索策略保持稳定；Profile 可以注册可选 `SearchStrategy` 对现有候选进行重排或过滤。

### `audit`

扫描当前仓库与固定 Git baseline，生成/刷新仓库根目录的 `修改说明.md`：

```bash
python scripts/quality_guard.py audit <repo> --diff-base <commit>
```

`audit` 会写入机器事实，并保留需要人工完成的 ADD / SEM / TEST-CHANGE / Reduction / Commit 等语义字段。报告未完成时返回 `UNVERIFIED`，不能作为最终质量结论。

### `verify`

只读重新扫描源码并校验最新报告：

```bash
python scripts/quality_guard.py verify <repo> --diff-base <commit>
```

`verify` 不补写报告、不修改源码，也不会把未完成或 BLOCKING 的裁决自动改成 JUSTIFIED。最终状态为 `PASS`、`REVIEW_REQUIRED` 或 `REJECT`。

## 2. 标准开发流程

RQG 的推荐闭环如下：

1. 冻结 accepted design baseline。用户明确指定 revision 时使用该 revision；否则使用任务开始时最后一个可确认的目标分支基线。
2. 新增 function / method / class / helper 前先执行 `doc-generate` 与 `doc-search`，检查已有 owner、调用方和可复用能力。
3. 实现变更并先运行受影响的既有测试。测试默认代表稳定行为或契约，不应为了迁就当前实现而机械修改。
4. 执行 `audit`，阅读机器事实和真实源码，完成新增接口、语义候选、测试契约和历史风险裁决。
5. 执行 Reduction Pass，检查新增 concept、branch、helper、mode、layer、state 或 coupling 是否仍能删除、内联、合并、复用、下沉或收紧。
6. 冻结最终 patch，并在报告中填写覆盖整个 patch 的提交建议。
7. 执行只读 `verify` 得到最终质量状态。

`修改说明.md` 是审计交付物，默认不进入业务 Git commit，除非项目明确要求。

## 3. 退出码

稳定 CLI 使用以下主要退出码：

| Exit code | 含义 |
|---:|---|
| `0` | 命令成功；`audit` 为 `READY_FOR_VERIFY`，或 `verify` 为 `PASS` |
| `1` | `verify REJECT`：静态或语义门禁拒绝 |
| `2` | CLI、Profile、依赖或运行环境错误 |
| `3` | 报告门禁未完成或报告契约拒绝 |
| `4` | Skill / Installed release 完整性失败 |
| `5` | `verify REVIEW_REQUIRED`：接口/协议账本需要人工确认，但不是 REJECT |

调用方应按退出码和日志中的状态共同判断结果，不应把 `audit` 的报告生成动作视作最终通过。

## 4. Portable 与 Installed 模式

### Portable

Portable `.skill.zip` 是完整、自包含、可离线 bootstrap 的发行物。它保留：

- 第一方 runtime；
- `offline/wheelhouse`；
- `offline/node_modules.zip`；
- 依赖锁；
- 可选 Profile catalog。

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

`修改说明.md` 与运行时相反：它必须作为审计附件交付，但默认不进入业务 commit。

## 8. 完整性与依赖

RQG release 通过 `runtime/MANIFEST.sha256`、`runtime/RELEASE.lock` 和 launcher seal 校验受保护文件。运行过程中生成的 `__pycache__`、`*.pyc`、`*.pyo`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache` 属于可再生缓存，不参与 QG990 runtime integrity；release builder 仍会在正式发行介质中剥离它们。Portable Skill 与 Installed tree 使用不同 distribution policy：

- `skill`：允许并要求正式离线依赖介质；
- `agents`：只保留第一方 runtime 与锁，不携带完整 wheelhouse / node_modules archive。

依赖 bootstrap：

```bash
python runtime/install_dependencies.py
```

安装器按 `runtime/dependencies.lock.json` 使用精确版本。`RQG_OFFLINE_ONLY=1` 时只能使用已有精确依赖或随包离线介质，缺失即 fail-loud。

Node.js/npm/Clang 属于宿主系统能力；只有显式 `--install-system` 或 `RQG_INSTALL_SYSTEM_DEPS=1` 才允许安装系统依赖。

## 9. Release 结构

正式 public `.skill.zip`：

- 包含完整第一方 Skill、`README.md`、`README_zh.md`、`dev-tests/`、authoring/release tools 与离线依赖；
- 不包含构建缓存或私有 Profile catalog。

这些 authoring 资产只属于完整 Skill 发行介质；deploy 到 `.agents` 时必须剥离 `README.md`、`README_zh.md`、`dev-tests/`、`tools/`、`offline/`、wheelhouse、Node 离线 payload 与 Profile authoring 文件。

Internal build 可以额外带本地 Profile catalog，用于组织内部 dogfood / parity 验证；它不改变 public Git tree。

## 10. 审计原则

- Critical / Error / Warning 独立计数，历史下降不能抵消本轮新增或升级；
- 语义 finding 需要沿 owner / caller / protocol / state / test 路径给出可验证证据；
- fallback、宽松契约、suppression、return-shape、依赖面与启发式行为必须进入相应语义裁决；
- 不得通过修改 Skill runtime、manifest、release lock 或测试断言来规避门禁；
- 行为正确和测试通过不等于 Reduction 完成；存在更小、更直接的可验证设计时应继续收敛；
- 最终质量结论只来自最新源码上的 `verify`。

## 11. 参考文档

以下路径相对于 Repository Quality Guard Skill 根目录：

- `references/RULES.md`：QG 规则与严重度说明；
- `references/AUDIT.md`：报告、审计和验证契约；
- `references/CODING_GUIDE.md`：开发、接口、测试和 Reduction 约束。
