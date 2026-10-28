# CLI、内部实现与发行边界

**唯一常规 Agent 入口：** `python scripts/quality_guard.py`。

| 动作 | 角色 |
|---|---|
| `doc-generate <repo>` | 生成 / 增量刷新 API 目录 |
| `doc-search <repo> <query>` | 搜索现有接口与能力 |
| `evidence --repo <repo> --symbol ...` | 同一源码快照的调用图、变量溯源和规则修复卡 |
| `audit <repo>` | 执行 normal 等模式的审计并生成报告 |
| `verify <repo>` | 只读质量门禁；必须审阅报告、确保未过期 |

`scripts/git_hook_install.py` 是安装期间的公开工具，不属于日常审计命令。
规则查询、调用图和变量溯源统一通过 `quality_guard.py evidence`，不提供第二套 CLI。

真实实现仅在 `runtime/src/`。`runtime/` 根层保留安装与依赖引导等内部维护代码。
`maintainer/` 存放只服务源码作者的打包、完整发行验收与开发仓库 Git hook。
`metadata/` 只存发布身份和签名材料，不是可执行命令。
根 `requirements.txt` 仅是人类可读的 Python 依赖声明；锁定安装合同仍以
`runtime/dependencies.lock.json` 为准，必须确认二者一致。

安装后的 `.agents` 不携带 `maintainer/`、源仓库测试和离线构建缓存，
但必须携带 `metadata/` 的当前安装态封印。所有迁移均需重新封存、
校验 QG990 和真实 CLI 后才具备发布资格。
