# Repository Quality Guard

Repository Quality Guard（RQG）是面向 Git 仓库的代码质量审计与开发闭环工具。它把接口复用检索、增量 API catalog、多语言静态分析、接口/测试契约变化、语义裁决、Reduction Pass 和最终只读验证放在同一套流程里。

当前稳定版本：**v0.20.1**。

## 快速开始

完整 Portable Skill 解压后可以直接运行：

```bash
python scripts/quality_guard.py doc-generate /path/to/repo
python scripts/quality_guard.py doc-search /path/to/repo "需要复用的能力"
python scripts/quality_guard.py audit /path/to/repo --diff-base <commit>
python scripts/quality_guard.py verify /path/to/repo --diff-base <commit>
```

安装到目标仓库：

```bash
python runtime/deploy.py \
  /path/to/repository-quality-guard \
  /path/to/repo/.agents/skills/repository-quality-guard
```

安装后使用：

```bash
QG=.agents/skills/repository-quality-guard
python "$QG/scripts/quality_guard.py" audit . --diff-base <commit>
python "$QG/scripts/quality_guard.py" verify . --diff-base <commit>
```

`audit` 负责生成/刷新 `修改说明.md` 和机器事实；最终结论由只读 `verify` 给出。

## Portable、Installed 与 Profile

Portable release 可以在运行时选择 Profile：

```bash
python scripts/quality_guard.py audit /path/to/repo --profile /path/to/my-profile
```

如果没有显式 `--profile`，RQG 只会在**目标仓库目录名与一个实际存在的 Profile 目录名完全一致**时自动匹配，并打印选择原因；否则使用 generic policy。

安装时也可以显式冻结一个 Profile：

```bash
python runtime/deploy.py \
  /path/to/repository-quality-guard \
  /path/to/repo/.agents/skills/repository-quality-guard \
  --profile /path/to/my-profile
```

Installed 模式把 Profile policy 与运行所需 payload seal 到 `.agents` 中，运行时不能再用 `--profile` 换策略。

## v0.20.1：Release identity coupling 审计

RQG 会把“某次发布的身份”与“长期稳定的项目契约”分开审计：

- `QG203`：通用 **CRITICAL**，只检查仓库路径/文件名是否绑定 release-like identity（例如版本化测试、fixture、artifact、history/release 路径）。它仍走正常 baseline/history-aware delta：历史已有 CRITICAL 不自动成为绝对阻断。
- `QG205`：通用 **SEMANTIC** 候选，覆盖文件内容里的依赖/API/协议/schema/迁移版本，以及其他需要判断长期合理性的版本、Git tag、commit/SHA/digest 引用；静态匹配本身不直接 REJECT。
- `README.md` 是唯一文档豁免。仓库内 migration/release/history 文档不会因为“看起来像发布材料”自动跳过；真正独立的 release evidence 应位于仓库外。
- Guard 会读取仓库已有 Git tags 作为证据。如果代码或测试显式依赖某个 tag，这个事实不会因为 tag 确实存在就自动合理化。

这与 `QG192` 不同：QG192 管测试读取 production source、private helper、源码字符串以及 `legacy/removed/no_longer` tombstone 等 implementation-history/change-detector 风险；QG203/QG205 管具体 release identity。一个测试可以同时命中两类规则。

## 开发自定义 Profile

Profile 是普通 Python 扩展，不是 JSON DSL。推荐本地目录：

```text
profiles/my-project/
├── profile.json
├── extension.py
├── PROFILE.lock       # 有自定义 QualityRule 时生成
├── AGENTS.md          # 可选；宿主根缺失时直接复制
├── README.md          # 可选；Profile authoring 说明
└── tests/             # 可选；Profile authoring tests
```

本仓库默认将整个 `/profiles/` 目录加入 `.gitignore`，因此本地私有 Profile 不会进入 public Git history。需要发布 Profile 时，可以把它放在独立仓库/包中，并通过 `--profile /path/to/profile` 使用。

### 1. 最小 `profile.json`

```json
{
  "schema": "repository-quality-guard/profile-v1",
  "name": "my-project",
  "version": "1.0.0",
  "entrypoint": "extension.py",
  "agents_file": "AGENTS.md",
  "rules": {
    "disable": ["QG173"]
  },
  "settings": {
    "canonical_owner": "MyService"
  }
}
```

`profile.json` 只放 manifest 和声明式配置。复杂检查、AST/源码判断、搜索策略和报告扩展写在 Python 中。

`rules.disable` 只能关闭允许 suppress 的普通规则；release integrity、报告完整性等 Guard 信任边界不能被 Profile 关闭。

### 2. 最小 `extension.py`

Profile entrypoint 固定导出名为 `Profile` 的类：

```python
from runtime.src.profile_api import QualityGuardProfile


class Profile(QualityGuardProfile):
    name = "my-project"
    version = "1.0.0"

    def configure(self) -> None:
        super().configure()
        self.add_capability("my-capability")
        self.add_nonblocking_path("examples/**")
```

`super().configure()` 会应用 `profile.json` 中的声明式配置；项目专属 Python 行为再由子类追加。

### 3. 添加自定义质量规则

```python
import ast

from runtime.src.profile_api import (
    Finding,
    QualityGuardProfile,
    QualityRule,
    RuleContext,
)


class NoDangerousEval(QualityRule):
    key = "my-project.no-dangerous-eval"
    title = "禁止直接 eval"
    description = "项目代码不得直接调用 eval()."
    severity = "error"
    scope = "repository"

    def evaluate(self, ctx: RuleContext):
        for path in ctx.analysis.paths:
            tree = ctx.python_ast(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "eval"
                ):
                    yield Finding(
                        code="",  # authoring lock 会为本规则冻结显示编号
                        severity=self.severity,
                        confidence="high",
                        path=path,
                        line=getattr(node, "lineno", 1),
                        column=getattr(node, "col_offset", 0) + 1,
                        message="发现直接 eval() 调用。",
                        suggestion="改为受控解析器或显式协议。",
                        source=self.key,
                    )


class Profile(QualityGuardProfile):
    name = "my-project"

    def configure(self) -> None:
        super().configure()
        self.add_rule(NoDangerousEval)
```

`RuleContext` 是只读事实视图。常用入口包括：

- `ctx.source(path)`：读取统一分析快照中的源码；
- `ctx.python_ast(path)`：取得已经解析的 Python AST；
- `ctx.analysis` / `ctx.base_analysis`：当前树与 baseline 的统一分析结果；
- `ctx.interface_diff`：接口差分；
- `ctx.settings`：`profile.json.settings` 的只读映射。

自定义 Rule 返回标准 `Finding`；Profile 不负责直接打印报告或决定进程退出码。

> 示例中的规则遍历方式只展示 API 结构。实际规则应尽量使用 RQG 已经建立的统一分析快照，避免在每个 Rule 中重复解析整个仓库。

### 4. 冻结自定义 Rule 编号

自定义规则的稳定身份是 `rule key`。如果规则类没有显式 `code`，在 authoring 阶段运行：

```bash
python runtime/profile_build.py profiles/my-project
```

生成/更新：

```text
profiles/my-project/PROFILE.lock
```

RQG 会在 `QG10000+` 区间为新 rule key 分配一次显示编号。已经分配的编号不会因为规则删除而回收，运行时也不会重新编号。

### 5. 组合 RulePack

相关规则较多时，用 `RulePack` 组合，不建议依靠 Profile 多重继承：

```python
from runtime.src.profile_api import QualityGuardProfile, RulePack


class SecurityRules(RulePack):
    def register(self, profile: QualityGuardProfile) -> None:
        profile.add_rule(NoDangerousEval)
        # profile.add_rule(...)


class Profile(QualityGuardProfile):
    name = "my-project"

    def configure(self) -> None:
        super().configure()
        self.include(SecurityRules())
```

### 6. 自定义检索和报告扩展

Profile 还可以注册：

- `SearchStrategy`：只对默认 `doc-search` 候选做重排/过滤；
- `ReportExtension`：添加稳定报告元数据；
- Core capability/config contracts：复用 RQG 已有通用分析机制。

Profile 扩展代码来自可信 Skill/Profile 包。RQG **不会从被审计仓库中 import 任意 Python 代码** 来执行质量规则。

### 7. `AGENTS.md` 的行为

Profile 的 `AGENTS.md` 是一份完整指令文件，不是需要渲染的模板。

部署时：

- 宿主根已经有 `AGENTS.md`：逐字节保留，绝不覆盖、merge 或 append；
- 宿主根没有 `AGENTS.md`，且 Profile 声明 `agents_file: "AGENTS.md"`：直接复制过去；
- Profile 没有提供 `AGENTS.md`：使用 RQG 的 generic bootstrap 资源。

Profile 的 `AGENTS.md`、`README.md`、`tests/` 都属于 authoring/deploy-time 资产，**不会进入 Installed runtime Profile payload**。

## Source、Skill 与 Installed 的资产边界

| 资产 | Git-ready source | Public Skill | Internal Skill | Installed `.agents` |
|---|---:|---:|---:|---:|
| `README.md` | ✓ | ✓ | ✓ | ✗ |
| `dev-tests/` | ✓ | ✓ | ✓ | ✗ |
| `tools/` authoring / release tools | ✓ | ✓ | ✓ | ✗ |
| `profiles/` | 本地存在、Git ignored | ✗ | ✓ | 仅选中 Profile 的运行必需文件 |
| Profile `AGENTS.md` | 可选 | ✗ | 可选 | ✗（只可能复制到宿主根） |
| Profile `README.md` / `tests/` | 可选 | ✗ | 可选 | ✗ |
| `offline/wheelhouse` | ✓ | ✓ | ✓ | ✗ |
| `offline/node_modules.zip` | ✓ | ✓ | ✓ | ✗ |
| `runtime/src` | ✓ | ✓ | ✓ | ✓ |
| `installed/POLICY.json` | ✗ | ✗ | ✗ | ✓ |

因此，Git-ready source 与完整 `.skill.zip` 都可以保留 README、回归测试和 authoring/release tools；正式 Installed runtime 只保留运行必需代码、锁和冻结策略，不携带 wheelhouse、Node archive、README、测试或 authoring tools。

## Git-ready 仓库与私有 Profile

本仓库默认：

```gitignore
/profiles/
```

这意味着本地 `profiles/*` 目录可以存在并用于 internal build，但不会进入 `git add`、commit 或 `git push`。

发布 public Skill：

```bash
python tools/build_release.py . dist/repository-quality-guard-public.skill.zip
```

发布包含本地 Profile catalog 的 internal Skill：

```bash
python tools/build_release.py . dist/repository-quality-guard-internal.skill.zip --internal
```

两种 release 都会重新 seal。`profiles/` 属于可选的发布/作者资产，不属于 QG990 自完整性保护面：Portable Skill 可以携带并直接使用 Profile catalog，也可以完全不携带；QG990 不负责证明 Profile “必须存在”或“必须不存在”。安装器会在生成 `.agents` 安装态时只冻结选中 Profile 的运行必需内容，且不会保留 `profiles/` catalog。

> Git-ready source checkout 是开发工作树，不是已经 seal 的安装态运行目录。`/profiles/` 可以作为 Git ignored 的本地作者资产存在；是否随 Skill 发布由构建参数决定，而不是由 QG990 推导。

## 完整性与退出码

主要退出码：

- `0`：成功；`audit` 为 `READY_FOR_VERIFY`，或 `verify` 为 `PASS`；
- `1`：`verify REJECT`；
- `2`：CLI / Profile / 依赖 / 环境错误；
- `3`：报告未完成或报告契约拒绝；
- `4`：release integrity 失败；
- `5`：`verify REVIEW_REQUIRED`。

完整规则、审计报告和开发约束参见：

- `SKILL.md`
- `references/RULES.md`
- `references/AUDIT.md`
- `references/CODING_GUIDE.md`
