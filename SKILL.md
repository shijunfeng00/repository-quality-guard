---
name: repository-quality-guard
description: 审计 Git 与源码目录的代码质量、接口契约和真实测试行为。用于代码新增、重构、质量治理及合并交付前的证据审计与验收。
metadata:
  version: "0.22.6"
  status: stable
---

# Repository Quality Guard

RQG 为 Coding Agent 提供**接口检索 → 代码修改 → 行为测试 → 质量审计 → 证据裁决 → 最终验收**的工作流。机器负责可复算的源码、Git 和规则事实；Agent 结合真实生产者、数据契约、消费者及副作用完成语义分析。

## 任务入口

命令在 Skill 根目录运行；宿主仓库安装路径为 `.agents/skills/repository-quality-guard/`。

| 场景 | 命令 | 详细协议 |
| --- | --- | --- |
| 检索已有接口 | `python scripts/quality_guard.py doc-generate /repo`、`doc-search /repo "能力"` | [CODING_GUIDE.md](references/CODING_GUIDE.md) |
| 查询规则与源码证据 | `python scripts/quality_guard.py evidence --rule QG003` | [RULES.md](references/RULES.md) |
| 生成质量报告 | `python scripts/quality_guard.py audit /repo --diff-base BASE` | [AUDIT.md](references/AUDIT.md) |
| 验收最新报告 | `python scripts/quality_guard.py verify /repo --diff-base BASE` | [AUDIT.md](references/AUDIT.md) |
| 编写和运行行为测试 | `python -m rqgtest.run` | [TEST.md](references/TEST.md)、[TEST_CASE_PROTOCOL.md](references/TEST_CASE_PROTOCOL.md) |

`python scripts/quality_guard.py --help` 提供完整参数；`evidence --help` 提供文件、符号、变量、调用方向及深度等检索选项。

## 交付工作流

1. **确定基线**：使用用户指定 revision，或在任务开始时冻结当前 Git 起点；记录本轮改动范围与适用 Profile。
2. **先检索再实现**：生成并搜索接口目录，核实已有能力所有者、调用方及可复用实现；保持职责单一、diff 精简。
3. **独立验证行为**：以需求/协议确定 Case、输入与 Oracle，运行受影响测试；以真实功能与可观察结果为判断依据。
4. **生成审计报告**：运行 `audit`，阅读 `修改说明.md`，根据证据完成 ADD、SEM、架构审查和 Reduction Pass。自动生成字段由工具维护。
5. **闭环问题**：修复新增质量债务，处理本轮触达范围内的历史债务；源码变化后更新测试与审计结果。
6. **最终验收**：以相同基线、Profile、模式运行只读 `verify`；交付实际退出码、C/E/W、剩余待裁决项、测试结果和报告。

`修改说明.md` 是独立审计产物，默认与生产 Git commit 分开交付。具体裁决标准及报告格式见 [AUDIT.md](references/AUDIT.md)。

## Finding 裁决

先执行 `evidence --rule QGxxx` 获取规则语义和修复方向，再验证源码中的 **Producer → Schema/Guard → Consumer → Side Effect**。结合真实控制流、数据流、测试和项目合同确认 TP、FP、FN 或待复核状态。

```bash
python scripts/quality_guard.py evidence --repo /repo --symbol pkg.module.consumer --direction callers --depth 3
python scripts/quality_guard.py evidence --repo /repo --file pkg/module.py --line 88 --name Handler --kind class
```

动态调用、反射、多态和框架入口以实际证据界定可达性；尚未闭合的关系标为 `UNKNOWN`。规则语义变动以独立正反例与维护者授权为依据。受支持语言由仓库自动识别，具体语义深度与原生编译事实按项目实际依赖报告。

## 审计模式与结果

`normal` 为默认模式。`--mode ignore-diff` 用于当前源码整体审计，`--mode cleanup` 用于生产范围普通 C/E/W 清零；专项模式由用户明确授权。`audit` 和 `verify` 使用相同的基线、模式和 Profile。无 Git 目录的当前源码审计将历史差分标记为 N/A。

| `verify` 状态 | 退出码 | 处理 |
| --- | ---: | --- |
| `ACCEPT` | 0 | 验收完成 |
| `REVIEW_REQUIRED` | 5 | 完成人工语义裁决后复核 |
| `REJECT` | 1 | 修复拒绝项后重新审计 |
| 报告待完善 | 3 | 补齐报告合同与证据 |
| 环境 / 完整性异常 | 2 / 4 | 恢复运行环境或发行完整性后复核 |

## 安装与发布

- 完整 Skill ZIP 用于源码恢复和离线部署，已部署的 `.agents/skills/repository-quality-guard/` 作为宿主 Git 的协作资产提交。
- 依赖安装：`python runtime/install_dependencies.py`；部署：`python runtime/deploy.py <skill-root> <target-skill-dir>`。
- 项目 Profile 按实际目录及授权配置加载；报告记录最终 Profile 及来源。
- Git 推送门禁使用 `python scripts/git_hook_install.py <repo>` 安装，fresh clone 验证托管 `pre-push` hook。

按任务读取对应参考文件，保持主流程上下文精简。
