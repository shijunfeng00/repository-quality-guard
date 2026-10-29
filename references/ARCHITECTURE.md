# 静态架构证据：先理解，再修改

RQG 的 `evidence` 查询的是**源码静态关系图**，不是运行时调用栈。查询结果帮助开发者定位已有实现、调用方和变量来源；正式审计仍需结合真实源码、业务合同和行为测试裁决。

## 何时查什么

| 需要回答 | 使用的命令 | 看什么 |
| --- | --- | --- |
| 仓库已经有类似能力吗？ | `doc-generate` → `doc-search` | 公开接口、签名和候选实现 |
| 修改一个函数会影响谁？ | `evidence --symbol ... --direction callers --depth 2` | 上游调用点、使用者与调用证据 |
| 它依赖哪些下游行为？ | `evidence --symbol ... --direction callees --depth 2` | 下游符号与可能派发 |
| 这个文件的某一行属于谁？ | `evidence --file ... --line ... --name ... --kind ...` | 符号所有者及源码定位 |
| 参数、局部变量或 `self.field` 从哪来？ | `evidence --symbol ... --variable ... --trace` | 候选赋值、实参到形参、构造注入 |
| 调用关系覆盖程度如何？ | `evidence --repo ... --graph-stats` | 各类边及未解析关系的统计 |
| 某个 QG Finding 为什么出现？ | `evidence --rule QGxxx` | 规则合同；再回到源码追证据 |
| 该怎么测试和验收？ | `references/TEST.md`、`references/AUDIT.md` | 行为 Oracle 与正式门禁 |

## 最小查询流程

在 Skill 根目录运行，`/repo` 为待处理源码仓库。先使用 `doc-generate` / `doc-search` 查重，再用源码符号查询上下游；只展开与改动相关的边：

```bash
python scripts/quality_guard.py doc-generate /repo
python scripts/quality_guard.py doc-search /repo "要修改的能力"
python scripts/quality_guard.py evidence --repo /repo --symbol pkg.module.func --direction both --depth 2 --max-edges 20 --trace --context 2
python scripts/quality_guard.py evidence --repo /repo --file pkg/module.py --line 88 --name Handler --kind class
python scripts/quality_guard.py evidence --repo /repo --symbol pkg.module.func --variable source --trace
```

短符号仅在唯一匹配时可用；不确定时使用完整符号或 `--file` / `--line` 定位。`--depth` 与 `--max-edges` 控制上下文规模，`--snippet-lines` 控制定义处的源码预览。`--cache-dir` 可指定仓库外缓存目录，内容变动后须重新核对其来源和快照状态。完整参数见 `evidence --help`。

## 证据边界与阅读顺序

1. **看定义**：先读符号签名、可见 docstring、必要注释、源码位置和所有者；注释表达作者意图，不能代替执行证据。
2. **看上下游**：沿 `callers` / `callees` 查生产方、调用点、消费方和副作用；用 `--variable` 核对形参、局部赋值与字段来源。
3. **看不确定性**：`DIRECT_CALL` 代表静态源码绑定，`MAY_DISPATCH` / `MAY_CALLBACK` 代表可能关系；`UNKNOWN` 保留未解析事实。它们都不是某次运行的真实调用栈，也不能独自证明分支必然执行。
4. **再改代码**：结合受影响调用者、边界契约、异常和独立测试确定最小变更；提交前运行对应 `audit → verify`。

当前 CLI 的 `--trace` 能展示静态调用路径和调用点附近源码，定义预览也可能包含 docstring；**它尚不能自动为路径中的每一个函数汇总所有注释或生成完整架构摘要**。需要这类信息时按源码位置进一步阅读，不要把查询结果误称为全项目记忆。
