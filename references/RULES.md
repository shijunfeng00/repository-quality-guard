# Repository Quality Guard Rules

本文件是完整告警字典。当前运行时可发出的每一个 `QGxxx` 与 `RUF:FORMAT` 都必须在这里有明确解释。规则实现与最终证据以源码和审计报告为准，本文件负责让使用者定位、理解并继续修复。

## 严重级别

- `critical`：最高优先级，默认必须修复；若为本轮新增且确实无法安全消除，只能通过逐项 `DELTA-*` 强论证进入条件通过。
- `error`：静态证据明确的契约、冗余、边界或执行问题，本轮新增项默认必须修复；例外同样要求逐项 `DELTA-*` 论证。
- `warning`：高风险结构或启发式命中；目标仍是尽可能修复，但存量 Warning 不要求为清零而擅自扩大为大范围重构。
- `info`：候选信号；结合调用链判断，不做机械批量修改。

同一规则在不同证据强度或上下文中可能动态调整级别，因此表格描述“检查内容”，不把级别写死。

## 修复方向契约

QG finding 不是“把命中的语法改没了”就算关闭。除非某条规则明确另有说明，**所有 `QGxxx` 都同时约束问题识别与修复方向**：先找出真正拥有该契约、状态、接口或行为的 owner，再决定在哪里修改。发现问题的位置只是 observation site，不自动等于 repair site。

每次处理 finding，至少回答下面四件事；同一 owner / 同一契约的一组 finding 可以合并回答，不要求逐条写长篇说明：

1. **问题是什么**：命中的是哪一个真实风险，而不只是哪个 AST/字符串模式。
2. **希望怎么改**：沿 producer → schema/canonicalizer → consumer，或 owner → caller → adapter 的上下游追踪，优先在定义问题的边界修复；required contract 应 fail-fast，合法 optionality 应由正式 owner 明确定义。
3. **不希望怎么改**：不得只为消除 QG 编号，把问题换成 helper、wrapper、membership branch、ternary、try/except、fallback、动态反射、兼容别名或另一种同义语法。若原风险语义仍在，只是写法变化，仍视为未解决。
4. **关闭证据是什么**：至少给出 owner/调用链或 schema 事实、修改后的唯一契约，以及能区分“合法缺省”和“契约破坏”的验证。测试通过只能证明行为未明显回归，不能单独证明修复方向正确。

如果无法证明某个 fallback、动态探测、兼容分支或宽契约是业务上合法的，默认方向是**向上追根因并收紧契约**，而不是在消费方继续容错。若确实必须保留例外，应指出唯一边界、长期语义和为什么不能由更上游 owner 统一处理。

### 常见规则族修复卡

下面不是新增 QG，也不改变既有 severity；它们给现有规则提供默认 remediation direction。没有命中专用卡的规则仍必须遵守上面的全局修复方向契约。

| 规则族 | 问题是什么 | 希望怎么改 | 不希望怎么改 |
|---|---|---|---|
| **内部字段 / Mapping 契约**：`QG003`、`QG004`、`QG012`、`QG023`、`QG025`、`QG026`、`QG061`–`QG066`、`QG144`、`QG153`、`QG157` | consumer 在猜字段是否存在、形状是否稳定，或把契约错误当成正常缺省。 | 先追 producer、schema、canonicalizer 和所有合法构造路径。required 字段由 owner 保证并直接索引；真正 optional 字段在正式 contract 中声明，优先在唯一边界归一化；普通 lookup table 的 miss 则作为明确业务分支处理。 | 不要把 `.get()` 机械换成 `if key in`、三元式、`try/except KeyError`、`optional_*` helper 或默认合并；除非已证明 optional/lookup 语义，否则这些只是问题换皮。 |
| **动态属性 / 反射 / 类型猜测**：`QG005`、`QG006`、`QG057`、`QG060`、`QG065`、`QG183`、`QG184`、`QG186` | caller 不知道自己拿到的到底是什么对象，或绕过静态接口修改/探测正式对象。 | 追构造来源和 declared type；单一类型就修正式接口，多个合法类型就建立 Protocol/ABC/discriminated union/typed dispatch；第三方或插件对象只允许在唯一 adapter 边界收敛成内部稳定类型。 | 不要在 `hasattr`、`getattr(default)`、`isinstance`、`__dict__`、`setattr`、反射 helper 之间互换，也不要把动态探测搬进“安全访问器”。 |
| **异常、失败与 fallback**：`QG007`、`QG009`、`QG023`、`QG024`、`QG048`、`QG049`、`QG066`、`QG130`、`QG176` | 程序错误、外部输入错误和预期业务失败被混成同一条“继续运行”路径。 | 先判断失败属于内部 invariant、外部边界还是合法业务结果；内部错误应暴露或在 owner 修复，外部错误在 ingestion/adapter 转成正式 validation/domain error，合法失败用明确结果类型或状态表达。 | 不要从 `.get()` 换 `try/except`、从异常换空对象、从 fallback branch 换 helper；也不要仅因为测试期望旧兜底就保留旧路径。 |
| **重复实现 / helper / 抽象层**：`QG001`、`QG002`、`QG008`、`QG010`、`QG013`–`QG019`、`QG021`、`QG080`–`QG098`、`QG154`、`QG156`、`QG162`–`QG168`、`QG185` | 能力 ownership 不清，或者复杂度只是在 helper、wrapper、子类和兼容层之间搬家。 | 先确认 authoritative owner；重复能力归并到真正 owner，shadow override 删除，长流程按独立职责拆 owner；若两个领域只是偶然相似则保持分离。优先删除/内联/复用，再考虑新增 abstraction。 | 不要为了降行数制造一次性 helper，不要为了 DRY 强行合并不同领域，也不要把旧实现包进 shared helper 后同时保留新旧两条路径。 |
| **接口 / 参数 / 返回契约**：`QG031`–`QG050`、`QG145`–`QG152`、`QG159`–`QG160`、`QG169`、`QG180`–`QG182`、`QG191` | public/internal contract 漂移，调用方被迫兼容多种形状或新接口没有证明必要性。 | 先检索已有 owner 与调用方；能复用就不新增。必须修改时保持单一、显式、typed 的新契约，并同步所有 caller、文档和稳定行为测试；破坏性迁移按已授权目标删除旧形状。 | 不要用 `*args/**kwargs`、别名参数、兼容 wrapper、双返回结构或“旧/新都支持”来降低迁移痛感；不要把历史实现形状当成必须永久兼容的接口。 |
| **配置 / Prompt / 环境边界**：`QG011`、`QG100`–`QG113` | 配置所有权分散、默认值掩盖部署错误，或本应由 schema/代码保证的约束泄漏进 Prompt。 | 配置集中到权威入口，必需项启动时 fail-fast；外部表示只在配置/adapter 边界解析。可机器表达的 Prompt 约束下沉到 schema、类型和代码。 | 不要在多个业务模块重复读同一环境变量，不要叠默认值链，也不要用更多 Prompt 禁令弥补代码契约缺失。 |
| **Tool / State / History / Trace**：`QG120`–`QG135`、`QG189` | 状态写入、Tool 返回和执行历史缺少唯一 owner，导致多个组件直接修改同一事实。 | 找到统一 state transition / merge owner；Tool 返回稳定结构化结果，由唯一入口归并；读写模型字段由正式 schema 定义，History 与 Trace 保持职责分离。 | 不要让每个 Tool 自己 patch State，不要增加“兼容各种返回”的 merge helper，也不要用反射或容器别名绕过状态写入边界。 |
| **测试变更**：`QG170`–`QG175`、`QG192`–`QG194` | 测试可能被当前实现牵着走，或把历史源码形状当成长期 contract。 | 先运行 accepted-baseline 测试，再判断旧断言保护的是仍有效的行为、明确废弃的形状还是测试自身缺陷；修改后验证稳定输入输出、协议和不变量。 | 不要为了过测试恢复已废弃实现，不要只删断言/skip/xfail，也不要以源码字符串、函数名、旧文件路径替代真实行为验证。 |
| **多语言与结构复杂度**：`QG195`–`QG205`、`QG207` | 动态 owner、隐藏循环、重复结构、版本身份或复杂度恶化可能逃过单一语言的表面检查。 | 追真实 owner、调用/dispatch 关系和稳定协议语义；对版本/digest 区分项目 release identity 与合法依赖/API/schema/完整性契约；只在语义 owner 一致时合并重复结构。 | 不要为了清 finding 重命名版本字符串、拆小函数或机械合并相似 AST；不要把合法协议 digest 和项目 release lineage 混为一谈。 |
| **运行环境 / 报告 / Release 完整性**：`QG000`、`QG900`–`QG902`、`QG980`–`QG985`、`QG990`、`QG998`、`RUF:FORMAT` | 审计事实、执行环境或交付证据本身不可信。 | 先修工具链、Git 基线、报告证据或 release seal，再讨论代码是否通过；所有结论必须来自最新源码和可重放验证。 | 不要删规则、改 manifest、降低断言、手填假 PASS 或绕过 hook 来让门禁变绿。 |

特别地，**一个 finding 从扫描结果中消失，不等于 remediation 成功**。如果 diff 显示同一风险被迁移到新 helper、新 wrapper、新 fallback 或另一处 consumer，仍应按原规则继续追 owner；`QG176` 只针对同一局部行为的特定等价改写；跨函数迁移不得仅凭相似度推断为新债务。


### Case 驱动的契约裁决与静态调用证据（v0.22.5 P0 修复指导 hotfix）

**语法/类型许可 != 合法业务 Case。** `NotRequired`、`Optional`、`dict.get`、`hasattr`、`bool` 或框架默认容错只证明一种代码写法可运行，不证明业务执行时真实允许字段缺失、返回空值或跳过处理。规则修复前必须证明：

1. **真实可达**：从可信入口/Producer、数据约束、调用参数和状态转移，构造具体的合法 Case（而非任意伪造输入）；缺失若实际是 invariant 违约，应 fail-fast。
2. **两条分支具有业务意义**：存在与缺失分别对应不同的正确结果、状态或副作用；纯粹把错误转成空字符串、空集合、`pass` 或默认对象不是合法 Case 的证明。
3. **上下游已有保证**：先确认 schema、canonicalizer、入口验证与 Guard 是否已处理；必须检查 guard 对消费位置的控制流支配、值传递/别名、是否变异和其他可达入口，不能因为上游出现过一个 `if` 就证明当前值必然有效。
4. **正反 Case 成对**：正例证明合法缺省确实存在且输出正确，反例证明内部必须字段缺失仍会被检测/失败；真实观测与独立 Oracle 优先，类型注解/单元测试自我断言不能独立证明业务需求。
5. **修改归属**：确认 source-level owner 后区分 CODE_BUG / RULE_BUG / REVIEW_REQUIRED；稳定规则的判定算法和严重度未经主理人裁决不得因误报而修改，必要时先提交规则缺陷 Case。

**统一证据查询入口（只读）**：

```bash
python scripts/evidence_lookup.py --rule QG003 --rule QG004 --api "consumer validation" --repo /path/to/repository --symbol package.module.consumer --direction callers --depth 3
python scripts/evidence_lookup.py --repo /path/to/repository --api "existing capability" --api-limit 5
python scripts/evidence_lookup.py --repo /path/to/repository --symbol package.module.consumer --direction both --depth 2
python scripts/evidence_lookup.py --rule QG033
```

`--rule` 直接投影本文件的规则定义及修复卡；`--api` 复用现有 `doc-search` 的 BM25 Catalog、强内容摘要保鲜和可再生 `docs/api-reference/` 缓存（只生成派生文档，不修改源码）；`--symbol` 使用既有 `RepositoryAnalysisSnapshot → RepositoryRelationGraph → RepositoryTopology` 导出有位置的 `DIRECT_CALL` 已解析前驱/后继和有限源码片段，**不是运行时 traceback 或 MUST 证明**。缺少调用边不代表不存在调用；跨语言/动态调用/插件/反射/外部消费者可能不在静态图中。多名称冲突必须显式给出完整 symbol ID；预算溢出必须标注截断。不得只贴工具返回的片段而不阅读实际函数体、来源文件和调用入口。历史 `scripts/rule_lookup.py` 仍兼容纯规则查询；不额外建设第二套静态图或 runtime agent 服务。

#### QG003 / QG026 · 方法真实 owner

`obj.get()` 只有解析到对象自身明确的零参数方法签名时，才可排除 *Mapping.get* 的归因；Mapping/TypedDict 的 `get()` 无 key 本身不合法。`obj.get(key)` 需沿 MRO、override、方法体和调用边追查；自定义包装方法内部调用 `_data.get(key, default)` 时不能因为最外层不是 `dict` 就直接放行。方法身份仍 unknown 时保留复核，不能用名称、参数数量单一条件给出高置信判决。

#### QG003 · 已授权修复后的 Owner 判定（P0）

只有证实接收者是 `dict`、`Dict`、`Mapping`、`TypedDict` 或其继承链上
真实的 Mapping owner，才把 `.get(key)` 归为 QG003 映射风险。
无参数 `Mapping.get()` 明确缺少必填 key，应报告；普通对象的 `get()`
只要追溯到本地类/父类/祖父类的真实实现，不应被归类为映射访问。
`self._data` 在构造器里明确初始化为字典/映射时仍检查内部 `.get()`；
包装器 `Repository.get(key)` 本身不能机械当作 Mapping，
但其方法体中的 `self._data.get(key, default)` 必须继续检测。
多继承冲突、动态派发及未知 owner 只能输出低置信候选，
不得假称已经解析完整 C3 MRO，更不能以 suppress 掩盖真正的必填字段访问。

#### QG003 · 有来源、有处理的缺失分支（P0 补充）

对无第二参数的 `mapping.get(key)` 不应一律判作静默兜底：例如有限
注册表中的未知类型确实可能出现，返回的 `None` 被全部已解析的同模块
调用点以显式 None 分支消费；又如构造器逐条写入的稀疏索引，未命中
后有实际独立业务路径。只有可见生产者、返回/赋值路径、必要的 guard、
两侧不同处理均有源码证据，才可排除 QG003。单独声明 Optional、
单独出现 `if value is None: pass`、存在未审的其它消费路径均不充分。

检索入口：`scripts/evidence_lookup.py --repo <repo> --file <path> --line <line>
--name <symbol-or-field> --kind variable --direction callers --depth 3`；
需结合真实正反 Case 和完整函数体审查，不能把 `[UNKNOWN]` 边当作排除证据。
特别注意必填 TypedDict 字段、伪造非空 ID、没有证据的同名 `.get()`
必须保留原有告警。已观察过的其它仓库 Finding 需原位对照，不能以
本仓误报消失充当跨仓稳定的证明。

#### QG004 · 缺省实际可达性

`TypedDict(NotRequired)` 表示声明可缺，并不证明真实 Producer 会合法省略该字段，更不证明默认值正确。稀疏反向索引对**已知存在、确实零入边**的节点返回空边集，是可通过构造/消费方证明的合法 Case；相反对于必需 id，缺失后返回哨兵属于静默掩盖。普通 lookup table 的 miss 也需要调用入口和行为 Case，不能因变量名包含 `index`/`map` 就豁免。

#### QG004 · 已授权修复后的明确空结果（P0）

`get(key, "UNKNOWN")`、随机字符串或任意非空伪 ID 与
`get(key, None)`、`get(key, [])`、`get(key, "")` 必须区分。
只有**直接返回**且与明确 `Optional[T]` / `T | None` 的返回契约一致时，
才可把 `None` 识别为已声明的空结果；对于 `[]`/`""`，还需真实
稀疏生产者（例如构造中空映射、仅在边遍历中 `setdefault` 写入）以及
匹配的 `list[T]` / `str` 返回契约。没有证明合法 miss 的普通 map
不能因默认值为空就直接免审；必填 TypedDict 字段即使有 Optional
返回标注也仍然报告 QG004，业务实际存在性不能从语法可选性推出。
需要按 Cases 分别证明合法的未命中与真实数据损坏行为。

#### QG033 · 布尔语义

`bool(set[str])` 是集合非空判断，不是解析字符串 `"False"`；但若整个 `bool(operations)` 与下游 `any(...)` 在全域上等价而冗余，应优先精简代码。`bool(flag: str)` 也可能只是检查字符串非空，必须有布尔文本解析的调用契约才能判定解析错误。规则缺陷仍作为稳定规则改动候选单独裁决。

#### QG177（已退役，不可恢复）· 正反对抗 Case

不恢复该规则、不根据相似 fallback 进行阻断；但仍在后续 R3 对抗 Cases 中分别保留“真正新增的 helper 使必需键缺失被吞掉”（应检出）和“已有未修改 helper / 合法 sparse miss”（不得误报）。跨函数失败迁移只能依据 before→after 因果差异和 Producer→Consumer→Effect 实际证据认定。

## 完整规则索引

### 解析、基础结构与文档

| 规则 | 检查内容 |
|---|---|
| `QG000` | Python 语法解析失败；当前文件无法继续做可靠 AST 审查。 |
| `QG001` | 短小且低复用的内部函数/方法才进入 ephemeral-helper 审查；direct call、callable reference、callback/protocol hook、公开/导出接口都会作为正向复用证据。 |
| `QG002` | 短小且静态实例化/引用很少的类，检查是否为低价值包装。 |
| `QG003` | 先确认真实 Mapping owner：本地方法及继承实现不得仅按 `.get` 名称误归类；无参数 Mapping.get 缺 key；内部字段若已证明是字典仍严格。`internal_formal` 稳定映射使用 `.get(key)` 猜必需字段时保持严格；`external_optional`/`dynamic_boundary` 只保留低严重度所有权证据；无法静态确认 owner 时保持 unknown 并转 QG026 语义复核，不因 `strict_get` 假装成内部契约。 |
| `QG004` | 明确空结果必须与返回类型和可达生产者 Case 对应；必填键与任意非空哨兵不因默认值语法合法而豁免。`internal_formal` 映射使用 `.get(key, default)` 静默兜底仍按 Critical；静态解析到的外部可选契约或明确 ingestion/adapter 边界不按内部 schema 失败处理；unknown 保持低置信复核。 |
| `QG005` | `hasattr()` 探测 `internal_formal` 对象仍属于运行时形状猜测；解析到的外部协议字段/动态边界降为所有权证据，unknown 不硬猜。显式 Protocol/ABC/联合类型/override 不属于本规则目标。 |
| `QG006` | `getattr(..., default)` 对 `internal_formal` 对象继续严格；外部可选协议/动态边界只记录低严重度证据，unknown 进入语义复核。不得把内部 formal contract 伪装成外部 optional。 |
| `QG007` | 捕获宽泛异常；吞错并返回默认结果时风险更高。 |
| `QG008` | 类直接 authored methods `<=20` 不因数量告警；`21..50` 进入 owner-cohesion Semantic Review；`>50` 才按 Critical。禁止为压方法数制造 wrapper/mixin/helper 碎片。 |
| `QG009` | 通过 `ImportError/ModuleNotFoundError` 选择备用实现或兼容导入。 |
| `QG010` | 函数只把参数转发给另一个调用；单次使用的 private 薄包装按 Critical，公开协议包装结合调用方审查。 |
| `QG011` | 读取配置时提供默认值，可能掩盖部署缺项。 |
| `QG012` | 稳定映射使用 `setdefault()` 同时读取、兜底和写入。不得机械展开为 `if key not in mapping: mapping[key] = default`；`setdefault(...).add/append/extend/update` 是明确的聚合原语，不命中本规则。其他合法缓存/聚合应保留简洁语义或收拢到唯一状态入口，必需字段则直接索引。 |
| `QG013` | owner 内 `private/internal + tiny + low-reuse` ephemeral helper 达到数量/密度阈值时进入 Semantic Review；共享 primitive、callback/protocol hook、公开接口不计入碎片化。 |
| `QG014` | 单个函数超过配置阈值，属于函数职责臃肿；按 Critical，且不得机械拆成碎片 helper。 |
| `QG015` | 函数参数总数过多，可能混合多项职责。 |
| `QG016` | 控制流嵌套层级过深。 |
| `QG017` | 默认达到 25 个分支时输出 INFO 语义建议；不因为分支计数直接阻断。 |
| `QG018` | 函数接收多个布尔开关，形成隐式模式矩阵。 |
| `QG019` | 模块物理行数超过配置阈值，属于模块职责臃肿；按 Critical。 |
| `QG020` | 源码包含静态检查抑制标记，需要确认其必要性和范围。 |
| `QG021` | 两个函数具有完全相同的 AST 函数体，属于高置信重复实现；按 Critical。 |
| `QG022` | 仓库模块间形成循环依赖强连通分量。 |
| `QG023` | 捕获 `AttributeError/KeyError/TypeError` 后返回默认结果，掩盖契约错误。 |
| `QG024` | 使用 `contextlib.suppress()` 完全隐藏异常。 |
| `QG025` | 稳定映射 `pop(key, default)` 静默忽略本应存在的字段；生产代码按 Critical。 |
| `QG026` | 接收者类型无法确认的 `.get()` 候选，需要核对是否为内部映射契约。 |
| `QG027` | 单个生产类、函数或方法缺少 docstring；按 ERROR 修复，测试代码独立审计。property/cached_property/setter/deleter 由接口审查账本逐项说明，不重复生成本规则。 |
| `QG028` | 公开接口 docstring 过短；生产代码单项按 Error 处理。property 类接口由 QG180 的必要性与同步字段审查，不强制机械扩写 Args/Returns。 |
| `QG029` | 公开接口 `Args:` 未覆盖全部非 `self/cls` 参数；生产代码单项按 Error 处理。 |
| `QG030` | 公开接口缺少 `Returns:` 或 `Yields:` 说明；生产代码单项按 Error 处理。property 类接口不重复命中。 |

### 函数签名、返回和调用安全

| 规则 | 检查内容 |
|---|---|
| `QG031` | 布尔参数使用 `0/1` 或文本布尔作为默认值。 |
| `QG032` | 布尔参数类型同时允许 `str/int` 等外部表示。 |
| `QG033` | 内部函数再次解析、比较或 `bool()` 转换多种布尔表示。 |
| `QG034` | 函数签名使用 bare `*` 或 `*args`：前者引入额外 keyword-only 调用契约，后者接受未逐项声明的可变位置参数。项目默认优先固定显式参数；参数总量由 QG015 独立检查。 |
| `QG039` | 参数使用 list/dict/set 可变默认值。 |
| `QG040` | 参数默认值为 `None`，但类型标注未声明可空。 |
| `QG041` | 旧同名函数参数匹配已停止；需编译器认证调用目标和参数绑定后才能恢复正式诊断。 |
| `QG042` | 使用开放 `**kwargs`，或虽声明有限 key 但在当前函数中读取/改写而非纯透明透传。仅机器可验证的有限契约（优先 `Unpack[TypedDict]`；兼容场景可用 `# RQG-KWARGS: keys=...; mode=forward-only`）且实际只原样 `**kwargs` 透传时不报告。 |
| `QG045` | 同一函数返回互不兼容的顶层类型。 |
| `QG046` | 非可空返回契约存在隐式落到函数末尾返回 `None` 的路径。 |
| `QG047` | 协议语义函数以 `str/repr` 代替稳定结构化返回。 |
| `QG048` | 捕获异常、记录后继续执行，可能把失败伪装成成功。 |
| `QG049` | 翻译异常时未使用 `raise ... from ...`，丢失异常 cause。 |
| `QG050` | 对输入参数执行调用方可见的原地修改。 |
| `QG051` | 使用 `global/nonlocal` 修改共享状态或闭包状态。 |
| `QG052` | 生产代码直接使用 `print()`。 |
| `QG053` | `subprocess.run()` 未显式检查返回码。 |
| `QG054` | 网络调用未设置明确 `timeout`。 |
| `QG055` | 异步函数直接调用同步阻塞 API。 |
| `QG056` | 使用 `eval/exec/compile` 动态执行 Python。 |
| `QG057` | 仅凭目标名称认定契约对象的旧检测已停止；待类型/所有权绑定后的结构检测。 |

### 内部数据契约

| 规则 | 检查内容 |
|---|---|
| `QG060` | 类型判别与声明契约：经可靠类型来源证明封闭内置类型不相容才报 CRITICAL；类型关系未知、精确类型的重复检测仅报 WARNING。已证明的基类→子类分派不报；具有明确 ABC 所有权、失败即抛 `TypeError`、成功后注册类或调用所声明抽象接口的注册边界不报，不能仅凭函数名或 `raise TypeError` 豁免普通 Consumer。不以 `isinstance()` 语法本身证明缺陷。证据包括类型注解、判别类型、绑定来源及证明分类。 |
| `QG061` | 消费方把标量/序列互相包装修复，例如 `x if list else [x]`。 |
| `QG062` | `zip()` 未使用严格等长语义，可能静默截断。 |
| `QG063` | 通过 `min(len(...))`、切片等方式静默修复长度不一致。 |
| `QG064` | 同时读取多个字段别名，例如 `id/doc_id` 兼容链。 |
| `QG065` | 同一数据同时按对象属性和映射下标消费。 |
| `QG066` | 使用 `payload or {}`、`result or []` 等固定空结构替换。 |
| `QG069` | 使用 `assert` 承担运行时输入或契约校验。 |
| `QG070` | 调用字符串、Path 等不可变对象方法后丢弃返回值。 |

### 类、模块和抽象层

| 规则 | 检查内容 |
|---|---|
| `QG080` | 类只有一个公开方法，可能只是函数包装。 |
| `QG081` | 类的私有 helper 数量相对公开方法过多。 |
| `QG082` | 类的大部分方法是 `staticmethod/classmethod`，可能只是命名空间。 |
| `QG083` | 大量实例方法不使用实例状态。 |
| `QG084` | dataclass 只有一个字段，可能重复包装已有值。 |
| `QG085` | 属性或方法只转发内部对象成员。 |
| `QG086` | 构造函数依赖数量过多或机械保存过多参数。 |
| `QG087` | 构造字段只被极少方法使用，类职责可能可局部收窄。 |
| `QG088` | 抽象类在仓库内只有一个实现。 |
| `QG089` | 根据 Factory/Builder/Manager/Adapter 名称后缀推断包装职责的旧检测已停止；必须依据调用图、真实职责和出口契约判断。 |
| `QG090` | 依据转发目标的同名字符串构图已停止；必须采用绑定的调用边与可重放的控制流事实。 |
| `QG091` | `utils/common/helpers` 通用模块定义持续膨胀。 |
| `QG092` | 模块级可变容器形成隐式共享状态。 |
| `QG093` | 使用 `from ... import *`，符号来源不透明。 |
| `QG094` | 定义名称包含 compatibility/legacy/fallback 等旧路径信号。 |
| `QG095` | 运行时模块包含 migration/backfill 一次性迁移入口。 |
| `QG096` | 函数或类名称过于宽泛，无法表达职责。 |
| `QG097` | 顶层函数内部定义嵌套函数或类的仓库级提示。 |
| `QG098` | 标记为 deprecated 的定义在仓库内已无静态引用。 |

### 配置和 Prompt

| 规则 | 检查内容 |
|---|---|
| `QG100` | 非配置/边界模块直接读取环境变量或配置文件。 |
| `QG101` | 同一配置键在多个模块重复读取。 |
| `QG102` | 源码中硬编码服务地址、IP 或端口。 |
| `QG103` | 配置读取时声明默认值，可能掩盖部署错误。 |
| `QG104` | 配置键由动态表达式生成，无法确认权威来源。 |
| `QG106` | 同一表达式在多个配置来源之间长期回退。 |
| `QG107` | 同一配置项默认值在多个模块重复定义。 |
| `QG110` | Prompt 名称/词表/语言中否定词数量不能证明规则滥用，旧检测已停止。 |
| `QG111` | 仅凭 Prompt 字符串拼接碎片数不能证明实现不当，旧检测已停止。 |
| `QG112` | 仅凭常量集合规模或变量名猜失败样例特判已停止，须验证具体消费行为。 |
| `QG113` | 同一长 Prompt 在多个模块重复实现。 |

### Profile 状态、Tool、History 与 Trace

| 规则 | 检查内容 |
|---|---|
| `QG120` | Tool 直接修改 Agent State，而非返回结构化结果交给统一入口归并。 |
| `QG121` | Tool 存在多种返回结构。 |
| `QG122` | Tool 返回裸字符串而不是稳定 JSON 结构。 |
| `QG123` | 不能因 Tool 以 get/read 命名或调用 save/update 命名而推断只读/写入；旧检测已停止。 |
| `QG124` | 写入状态模型未声明字段。 |
| `QG125` | 读取状态模型未声明字段。 |
| `QG126` | 同一状态字段被写入多种静态结构。 |
| `QG127` | 序列状态字段使用 `append()` 追加整个容器。 |
| `QG128` | 组件接收完整 State 却只读取一个字段，依赖范围过宽。 |
| `QG129` | 同一函数同时处理 History 与 Trace。 |
| `QG130` | 异常处理路径把失败步骤写入 History。 |
| `QG131` | Tool docstring 未说明固定 JSON 返回字段。 |
| `QG134` | 非统一状态更新入口直接写入 Profile 配置的状态对象。 |
| `QG135` | 同一状态字段混用 `append()` 与 `extend()` 归并语义。 |

### 返回、接口和项目边界

| 规则 | 检查内容 |
|---|---|
| `QG144` | 同一函数返回多组字典字段集合，可能形成不稳定返回契约。 |
| `QG145` | 通过 `del` 显式丢弃接口参数。 |
| `QG146` | 显式声明的返回/流式映射契约相对 Git 基线发生变化。 |
| `QG147` | 框架适配入口签名、异步形态、装饰器或返回标记漂移。 |
| `QG148` | SSE 事件类型、事件外壳或结束字段相对基线变化。 |
| `QG149` | 跨所有者使用单下划线私有符号：包括其他对象/类的 `_member`、其他模块的 `_function`/`_Class`/`_variable`、私有模块路径及其别名导入；生产运行时代码按 Critical 处理，`tests/**` 单独报告为 Warning。标准库明确公开但采用下划线命名的确定 API（当前仅 `os._exit`）不按项目 private 处理。 `__init__.py` 不得借公开别名绕过模块私有边界，`__all__` 与模块级赋值不得重导出私有定义；子类访问父类 `_member` 属于越权，只有本类拥有的私有成员可直接访问。Profile 显式非阻断路径与测试可降为 Warning，但不得静默取消检测。 |
| `QG150` | 模块级字面量常量未使用全大写名称。 |
| `QG151` | HTTP 路由方法、路径、endpoint 或响应形态相对基线变化。 |
| `QG152` | 请求/响应 Header 读取、写入或过滤边界相对基线变化。 |
| `QG153` | 从同一映射逐字段 `.get()` 手写构造新 dict，兼具重复投影与契约猜测；生产代码按 Critical。 |
| `QG154` | 函数中套函数或类中套类；标准 decorator wrapper 和一次性 lambda 例外。 |
| `QG155` | **Error**：终止语句后的代码、静态恒真/恒假 if 分支、`while False` 循环体、`while True` 的 else，以及 try/handler/finally 块内可证明不会执行的代码。历史存量不绝对阻断；本轮新增或升级由 QG179 拒绝。 |
| `QG156` | 单下划线私有函数/方法没有静态调用或引用。 |
| `QG157` | 消费方先探测字段存在性，再读取同一字段并回退默认值；高置信内部契约猜测按 Critical。 |
| `QG158` | 运行时代码、Prompt 或字段描述声称接口、返回结构或行为“保持不变/兼容”，却没有可验证的 Git before/after 基线。 |
| `QG159` | **Critical，精确名称回归**：生产声明、参数或属性新引入 `top_k`、`file_name`、`file_names`、`cfg` 等已明确废弃的完整名称时拒绝。只做完整标识符匹配，不做子串、相似词或模糊关键词扩展；稳定作用域指纹避免代码移行造成假新增。 |
| `QG160` | 函数或方法滥用 `normalize*` 命名；除向量、矩阵、概率、分数、张量等明确数学归一化外，按 Critical 处理并要求改为真实动作名称。 |

### 多语言结构事实

以下规则与 Python 既有规则共用同一 baseline/current 门禁，但事实由统一 `RepositoryAnalysisSnapshot` 的 language adapter 提供。JS/TS 的 QG001/QG010/QG014/QG016/QG017/QG019 沿用既有编号和阈值；CSS/HTML 不强行映射成函数 AST。

| 规则 | 检查内容 |
|---|---|
| `QG195` | **Error**：JS/TS 同一词法作用域重复定义同名 function/method，后声明会遮蔽前声明。对象字面量中不同 owner 的同名 property/method 不算同作用域重复。 |
| `QG196` | **Info，必须语义裁决**：Git 基线已存在的 giant JS/TS owner（>500 行或 >=50 个直接 nested definitions）发生显著恶化（当前阈值：新增 >=20 行或直接 nested definitions 增加 >=5）；用于阻止“child 指标下降但 owner 继续膨胀”被误报为质量清零，同时不因 +1/+2 行 hotfix 制造人工审计噪声。 |
| `QG197` | **Info，必须语义裁决**：既有 giant owner 一轮新增 >=5 个直接 nested helper，提示 helper laundering；必须判断复杂度是否迁移到独立 owner，而不是只在原 closure 内切碎。 |
| `QG198` | **Warning**：JS/TS 通过 `Object.assign(Class.prototype, ...)` 等动态组合形成超大 owner surface（当前高信号阈值：>100 composed methods 或 >50 constructor fields）。 |
| `QG199` | **Warning**：动态 feature method bags 的真实 `this/self.method()` 依赖形成 >=5 模块强连通分量；ES import graph 无环不能抵消该隐藏循环。 |
| `QG200` | **Info**：>=4 行 JS/TS definition 具有完全相同的规范化 AST 结构。只作为复用候选；对称操作、callback、类型适配和稳定语义谓词不得机械合并。 |
| `QG201` | **Info**：CSS selector 存在 >=3 条完全相同 declaration block。只有语义 owner 一致时才建议合并；声明值巧合相同不构成错误。 |
| `QG202` | **Error**：同一 HTML 文档出现重复 `id`。 |
| `QG203` | **Critical，baseline-aware**：非 README 资产的仓库路径/文件名包含 release-like identity（如 `vX.Y`、`rcN` 及其组合）。它只针对结构性 identity，不扫描内容中的 API/协议/依赖版本；历史已有 Critical 继续按 baseline/history-aware 规则处理。 |
| `QG205` | **Semantic**：非 README 正式资产的文件内容包含版本、Git tag、commit/SHA、固定 digest 或依赖版本身份。必须判断它是依赖/API/协议/schema/迁移/完整性等稳定契约，还是把项目自身某次 release lineage 固化成当前项目契约；默认静态命中不直接 REJECT。Profile 可用 JSON `rules.levels` 将其提升为普通 severity 或绝对 BLOCKER。 |
| `QG204` | **Warning/Critical**：真实发生变更的 C/C++ function 经 Clang AST 证明函数长度/分支/嵌套越过阈值或相对基线恶化。`-Werror` 负责编译/类型 warning，QG204 负责结构复杂度，两者互不替代。 |
| `QG208` | **Info / REVIEW_REQUIRED**：当前源码快照存在本项目 C++ 翻译单元，但缺少针对部分单元的真实编译数据库条目时，聚合报告缺失数量及源码身份。它只证明 **构建语义覆盖缺口**，不是 C++ 源码错误；可用的编译命令也不能代替实际 native compile 或证明已通过全部语义测试。跳过 vendored 翻译单元，历史未变化的当前源码仍要纳入。 |
| `QG207` | **Info**：JS/TS 显式 `any` JSDoc 逃逸数量较多，提醒 `checkJs PASS` 不能代替动态 owner contract 证明。 |
| `QG209` | **Warning / REVIEW，开发中**：Pyright 源定位 `reportCallIssue` + Pyrefly 唯一真实源码声明绑定 + `inspect.Signature.bind` 独立确认实参缺失，才报告 R1 调用契约冲突。证书必须绑定调用方、被调用方路径及 SHA256；`OPEN_WORLD` 永远不能等同运行时封闭或 BLOCKER。未部署已认证离线编译器时不得推断为无问题。现有 Pyright/Pyrefly 认证路径仍需显式配置；另有默认启用的源码快照唯一模块级函数签名绑定（不导入/执行目标代码），支持直接调用和明确 import 绑定。装饰器、动态重绑定、方法分派、星号实参及不可解析来源保持 UNKNOWN；两类 QG209 均为 OPEN_WORLD Warning，不可直接升级 BLOCKER。 |
| `QG211` | **Warning / REVIEW，开发中**：对唯一源码绑定的 Python 函数，识别 `try` 内 `await` 赋值右侧尚未完成时进入匹配异常处理器、处理器又直接读取目标局部变量的条件性路径。只证明该路径的局部变量未绑定风险；外部 API 是否抛出相应异常仍须有独立执行或 Provider 契约证据。不会依据变量/方法名称或缺失调用边升级为 BLOCKER；同步调用、预先初始化和未覆盖的 CFG 返回 UNKNOWN。 |

多语言 parser 失败属于事实层失败，必须 fail-loud。JS/TS usage 同时统计直接 call 与 dispatch/callback symbol reference；`return items.map(callback).join(...)` 这类 callback 内含真实逻辑的表达式，以及 `Boolean(a && b === c)` 这类带非平凡参数表达式的语义谓词，都不得误判为单层 wrapper。C++ 默认只对真实变化文件生成 Clang AST，避免把全仓数百 MB AST dump 塞进每次 scan；完整编译和测试仍在项目验证阶段执行。

### 工具执行和 Git 环境

| 规则 | 检查内容 |
|---|---|
| `QG900` | 未找到 Ruff，且本地 wheel、pip、uvx 等可用路径均未成功。 |
| `QG901` | Ruff lint 执行失败或输出无法解析。 |
| `QG902` | Ruff format check 执行失败。 |
| `QG998` | 缺少可用 Git 接口 diff；报告会区分非 Git、safe.directory、权限和 Git 环境错误。 |
| `RUF:FORMAT` | Ruff formatter 判断源码格式不符合配置。 |

## Git 工作区接口差异

接口 diff 独立于普通结构规则并自动选择累计基线：显式 `--diff-base` 优先；否则先读取当前 upstream reflog 中最后一次明确 `update by push` 的提交，把该提交之后的 A/B/C 本地提交和工作树合并成一个审计批次。无法证明 push 基线时才回退到 upstream、dirty `HEAD -> WORKTREE/INDEX` 或 clean `HEAD~1 -> HEAD`，避免逐提交切片掩盖早期大改。报告覆盖文件、类、函数、方法、全局变量、成员变量、参数、默认值、注解、返回类型、装饰器、继承关系和同步/异步形态。公开和非公开符号都进入接口快照。

生产接口变化必须逐项说明必要性、调用方同步和兼容风险；tests 独立分账：文件级保留 QG170~175 防弱化，本轮修改/删除既有用例和命中 QG192 的高风险新增用例生成 `TEST-CHANGE-*`，必须完成测试契约语义裁决。

新增生产函数/方法/类前，开发流程必须先生成全库 API 目录并执行 BM25 复用检索。报告中的每个 `ADD-*` 必须披露 `接口目录=`、`查询=`、`候选=`、`源码=`，随后才进入 HEAD/父类/MRO/兄弟类/公共能力和四种减法实验。BM25 只提供候选排序，不得据其分数自动豁免、阻断或判断语义所有权。

## 处理顺序

1. 先处理 Ruff 执行失败与 Python 语法错误，确保后续结果可信。
2. 目标始终是尽可能达到 Critical/Error/Warning `0 / 0 / 0`，但门禁采用“零回归 + 有条件延期”，不是“存量必须全清”。
3. 本轮新增或严重级别升级的问题优先修复。仍存在时，扫描器为每项生成 `DELTA-*`；必须分别填写不可避免事实、尝试过的替代、风险和关闭条件，不能由其他档下降抵扣。
4. 存量问题按 Critical → Error → Warning 顺序，在本轮自然触达文件和调用链内尽可能小步削减；不要求逐项解释所有保留项。
5. 若 Git 基线中的三档存量问题一个都没有被删除或降级，报告必须说明为什么继续清理会超出本轮范围，以及最小方案和关闭条件。拆分类、公共接口迁移、继承关系重构、修改大量无关调用方等操作，未经用户明确许可不得仅为清债自动实施。
6. 对 `info` 结合真实调用链判断；不得为了减少数量机械拆分或批量改写。

## 修改说明报告与历史债务

最终 `修改说明.md` 必须回答 11 项压缩合并后的架构与必要性审判；启用 `strict-semantic-question` capability 的 Profile 对第 10 项额外启用 QG178 候选与 Git 基线授权账本的强校验。静态规则提供可复现事实，模型负责职责所有权、跨层越界和新增必要性的语义裁决；validator 只检查证据引用、状态单调性和结论一致性。第 9 项分别核算 Critical、Error、Warning，并检查每个新增或升级问题对应的 `DELTA-*`，三档互不抵扣。

历史债务应在本次自然触达范围内小步清理：优先删除重复实现、不可达代码、未用私有定义、低价值 helper，补齐 docstring，收窄异常与契约兜底。工具自动扫描 Git 基线并比较稳定问题指纹：新增或升级项逐项披露，已删除或降级的存量项自动计数。存量不要求全部清零，也不要求逐项写延期理由；但若一项都未减少，必须给出范围级事实。若修复需要牵连大量无关模块、迁移外部调用方、拆分类或缺少测试支撑，应明确关闭条件，不把高风险大改硬塞进当前需求。

## 特别说明

- 长函数只有超过 500 行才由 `QG014` 提示；不要为了降行数制造大量短小一次性 helper。
- 代码臃肿、重复、边界侵犯和模糊命名构成 Critical 主队列；大类、巨型函数/模块、碎片化方法、薄包装和重复实现按规则证据强度进入 Critical。
- 动态注册、框架反射、插件入口可能让静态调用关系不完整；这类情况必须以显式注册、装饰器或正式契约提供可审查证据。
- 内部数据由生产方保证结构，消费方按正式契约读取；不要在每个下游重复猜测和兜底。
- 业务代码修改任务中不得修改审查规则或文档来影响结论。

## 静态事实与模型语义边界

- 静态规则只判定可复现事实和高置信形态，不用正则证明某项职责在语义上属于某层。
- 工具生成 `tool_status`；模型阅读最新生产方、消费方、调用链和协议后填写 `final_status`。
- 模型可以基于证据降级结论，不能把静态结论抬高。
- `ADD/ARCH/DELTA/TEST-CHANGE/通用或 profile 专项审判` 任一项可标记 `BLOCKING`；此时最终状态必须为 `REJECT`。
- `.patch/.diff/.log/.zip/.tar*` 等交付附件使用 `ARTIFACT-*` 单独披露，不进入生产接口和三档预算。

## 架构差分、报告与发布完整性硬门禁

| 规则 | 检查内容 |
|---|---|
| `QG162` | HEAD 中子类方法可达父类公共能力，但 WORKTREE 中该依赖真正消失时至少 Warning。原 caller 仍存活时，必须继续直接调用、经同一子类 helper 链可达，或把能力显式迁入新出现的父类 typed orchestrator；无关 caller、无类型外部对象、嵌套属性、裸函数不能掩盖该存活 caller 的回归。原 caller 已删除时，不再要求保留历史 caller identity：若同一子类仍有 surviving caller 调用解析到同一父类 symbol 的同一 capability，则视为 caller consolidation；只有该 exact child → parent capability 在当前子类中完全不可达时才命中。 |
| `QG163` | 父类公共能力被删除或绕过后，子类新增名称和行为近似的替代实现；属于公共所有者错误下沉，按 Critical 阻塞。 |
| `QG164` | HEAD 中存在的继承关系在 WORKTREE 消失；按 Error 处理，必须说明组合替代、功能删除或新所有者。 |
| `QG165` | 子类新增与父类实现完全相同的 override；属于确定性重复实现，按 Critical 阻塞。 |
| `QG166` | 多个兄弟子类新增完全相同的方法；属于公共所有者缺失，按 Critical 要求上提父类、中间基类或唯一公共协作者。 |
| `QG167` | 子类新增字段与父类字段保存同一名称、属性或下标来源，形成两套可能漂移的影子状态；按 Critical 处理，字面量和新建对象不命中。 |
| `QG168` | **Critical，绝对阻断**：normalized topology 证明当前生产代码存在连续三层以上 one-shot helper 链；每个中间节点都必须同时满足 QG001 的 ephemeral-helper 条件，链边只能是高置信普通 `DIRECT_CALL`。callback/callable reference、protocol/override、共享 primitive、公开 API 或其他真实复用会断开链，不能被当成普通 helper 分解。即使 Git 基线已存在也直接 REJECT；修复时默认压缩纯转发链，但保留真实事务、资源、并发或生命周期边界。 |
| `QG169` | 相对 Git `--diff-base` 新增缺失 docstring 的生产定义；比较稳定定义指纹而非覆盖率百分比。删除已记录定义或改变分母不命中，结构完整性由 QG028/QG030 独立审计。 |
| `QG176` | **Critical**：质量问题只被换了语法。比较 Git before/after 的 fallback 行为指纹；`.get/hasattr/getattr(default)/setdefault/异常兜底` 改成 membership、三元式、`try/except`、默认合并或显式初始化，而同一 receiver/selector 缺失时仍继续执行。 |
| `QG178` | **Info 语义候选**：Git 新增行出现正则、模糊相似度、字符串词表 membership、多字面量前后缀，或把固定关键词/词表用于 `query/question/message/text/content/prompt/instruction` 等语义文本判断。确定性的 `role == Literal/Enum` 分派和显式 bool 控制参数不因普通分支本身命中。授权只读取 Git 基线中预先存在的账本；报告模型无权自行豁免文件、函数或规则。启用严格语义审判的 Profile 第 10 项必须逐项处理，未授权候选只能 BLOCKING。 |
| `QG179` | **Critical，不可豁免**：按稳定问题指纹比较自动或显式 Git 基线，任一普通生产 Critical、Error、Warning `INTRODUCED` 或 `WORSENED` 直接 REJECT；历史问题删除/降级不得抵消，DELTA 只能 BLOCKING。 |
| `QG180` | **Warning，接口专用**：新增生产函数/方法/类、private→public，或存量参数、返回、装饰器/property 等接口声明发生变化。必须证明绝对必要、不可替代、最小方案、兼容、全部调用方和新鲜验证；只豁免 QG179 的重复计罪。 |
| `QG181` | **Warning，人工复核**：生产函数/方法/类新增数量减删除数量大于 0。优先继续删除、内联、合并或复用；确需保留时必须逐项完成必要性审判，并保持 `REVIEW_REQUIRED` 等待用户人工确认。删除定义只需自动列出，不要求逐项解释。 |
| `QG182` | **Warning，Profile 核心协议专用**：当前 Profile 声明的状态键、事件 envelope/content 或返回/流式映射键相对累计 Git 基线变化。必须逐项证明绝对必要、不可复用既有字段、上下游兼容、验证和回滚；只豁免 QG179 重复计罪。 |
| `QG183` | **Critical，绝对阻断**：tests 之外反射访问 private，反射取得成员后调用或传播为 callback/target，或通过 `__dict__`、`__getattribute__`、MRO、`inspect`、`operator` 等绕过静态成员契约。唯一精确例外是 frozen dataclass 的 `__init__` 用 `object.__setattr__` 初始化字面量字段；其他情形不可用 `修改说明.md` 解释保留。 |
| `QG184` | **Error**：tests 之外，对显式类型、`self/cls`、带注解变量、明确构造实例或固定导入对象使用普通 public 反射探测/修改。未知动态对象不凭猜测升级；应先形成显式 Protocol/联合类型或在唯一适配边界完成类型收敛。 |
| `QG185` | **Critical，零回归阻断**：public 方法只转发 private 方法，形成无独立契约的 facade；本轮新增或升级时由 QG179 直接拒绝，Git 基线存量不单独清算。property/cached_property/setter/deleter 不归入本规则，其新增或声明变化由 QG180 进入人工审核。 |
| `QG186` | **Critical，绝对阻断**：tests 之外 monkey patch、运行时替换或删除类/模块属性、对固定类/模块使用 `setattr/delattr`，或调用 `mock.patch/patch.object/monkeypatch`。必须修改权威定义、显式注入依赖或使用正式插件注册接口。 |
| `QG187` | **Critical，绝对阻断**：仓库只允许根目录唯一 `AGENTS.md`。任何子目录或大小写变体的 `AGENTS.md`（含历史存量、未跟踪和被忽略文件）都会分裂、覆盖统一指令契约，必须删除并把有效约束集中回根文件。 |
| `QG188` | **Warning**：模块级全大写常量在全仓没有静态读取、属性访问或显式导入。应 grep/检索真实调用方后删除死常量；仓库外 API 或动态入口须以显式导出/注册证据说明。历史存量不妨碍 ACCEPT，本轮新增由 QG179 拒绝。 |

> 绝对阻断只作用于生产审计域；Profile 显式声明的非阻断路径仍完整报告，但不进入最终硬门槛。

### 测试代码审计

| 规则 | 检查内容 |
|---|---|
| `QG170` | 删除含既有测试用例的测试文件。 |
| `QG171` | 删除 Git 基线中已有的 `test_*` 测试用例。 |
| `QG172` | 测试断言数量相对基线减少，需检查是否弱化回归门禁。 |
| `QG173` | skip/xfail 数量相对基线增加，需检查是否掩盖失败。 |
| `QG174` | 新增测试文件缺少可识别的 `test_*` 用例或有效断言。 |
| `QG175` | 工作区测试源码存在语法错误，无法作为验证证据。 |
| `QG192` | **Info 语义候选**：本轮新增或修改测试读取/解析 production source、检查源码字符串存在/不存在、使用 `inspect.getsource/signature`、历史 `REMOVED_*`/removed/deleted/no_longer/legacy tombstone 等，疑似把实现历史固化为测试。它不直接判错；命中用例必须进入 `TEST-CHANGE-*`，若不能证明长期稳定架构不变量则必须 BLOCKING，修正为稳定行为/契约验证后重新审计。 |
| `QG193` | **Info 关系语义候选**：本轮修改或删除的既有测试用例，经 RepositoryRelationGraph 证明属于同轮生产变更的 affected RUN set。它不直接判定测试改错；Q11 必须说明旧断言为何因明确需求、Bug 修复或测试缺陷而失效，并给出先运行 accepted-baseline 测试的可观察证据，禁止把 affected tests 当成自动 EDIT set。 |
| `QG194` | **Info 关系语义候选**：本轮函数/方法/类接口变化存在静态可达生产依赖，但 RepositoryRelationGraph 无法证明任何既有测试覆盖。该规则只声明 `coverage relation unknown`，不等于“没有测试”；Q2/Q11 必须给出动态注册、fixture、HTTP/框架入口等真实行为测试路径，或补充稳定接口/返回契约回归测试。 |

### 报告与完整性门禁

| 规则 | 检查内容 |
|---|---|
| `QG980` | `修改说明.md` 缺少当前报告 schema 的固定章节、章节顺序错误，或应答的通用/profile 专项审判标题缺失、重复、改名。 |
| `QG981` | 报告 schema、change digest、tool_status 或 `RQG:AUTO` 自动事实块与当前 Git/接口/架构事实不一致。 |
| `QG982` | 生产 FILE/ARTIFACT/ADD/ARCH/DELTA/INTERFACE/PROTOCOL、测试 TEST-FILE/TEST-RISK/TEST-CHANGE、存量削减结论、通用/profile 专项审判或最终语义结论不完整；自动事实覆盖或符号不一致、重复套话、模型抬高静态状态、QG179 DELTA/QG168/正接口净额未标记 BLOCKING、新增/参数/核心协议变化缺少必要性与验证证据，或存量一个未减却没有范围级说明。 |
| `QG983` | 验证章节缺少 Guard、`git diff --check` 或项目验证命令，记录仍为空泛，或任一命令退出码非零。 |
| `QG984` | 提交章节缺少描述整个 accepted-baseline→target patch 的可执行 `git commit -m` 命令、仍为占位/陈旧命令，或不符合当前 Profile 声明的 `commit_policy`。Core 不固定 Conventional Commits 类型、语言或正文格式；这些属于仓库级 Profile 策略。Git author/committer identity 不属于 QG984 的通用质量规范。 |
| `QG985` | 模型抄写的生产新增函数、变量、类数量或二次减法复审 ADD 数量与工具事实不一致。 |
| `QG990` | Skill 发布清单、release seal、受保护文件数量或文件 SHA-256 不一致，或者受保护目录出现未登记的非派生文件；工具在扫描业务仓库前直接拒绝。`__pycache__`、`*.pyc`、`*.pyo`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache` 等可再生执行缓存不参与 runtime integrity；正式 release builder 仍必须剥离这些缓存。 |

### Profile 当前树绝对阻断与协议语义规则

| 规则 | 检查内容 |
|---|---|
| `QG189` | **Critical，Profile 可启用的当前树绝对阻断**：除 Profile 声明的权威状态类型内部及已登记的受控事务/流水账 API 外，调用方不得通过下标/属性赋值、嵌套容器方法、局部别名、`getattr/setattr/vars/__dict__`、`dict/list/object` 基类写入口、动态成员调用或 ctypes/id 反射修改状态。当前树存在即 REJECT，不适用历史债务豁免。 |
| `QG190` | **Critical，Profile 可启用的当前树绝对阻断**：生产代码禁止 `eval`、`exec`、`compile`、`__import__` 与 `importlib.import_module`。Profile 非阻断域仍扫描并独立分账；生产当前树存在即 REJECT。 |
| `QG191` | **语义候选**：本轮代码手工拼装已有协议/传输对象（例如 OpenAI tool-call wire）而没有复用既有协议 owner；要求先检索并复用唯一所有者，确有独立长期契约时再以 Q3/Q5 证据裁决。 |

生成阶段会立即检查报告；`--final-check` 会在不写文件的情况下重新计算事实。模型最终状态只能等于或严于 `tool_status`：可以把静态 `ACCEPT/REVIEW_REQUIRED` 降为 `REVIEW_REQUIRED/REJECT`，禁止抬高。存在 `BLOCKING` 时最终状态必须为 `REJECT`；接口/协议变化单独形成 `REVIEW_REQUIRED`，纯删除接口不触发；只读审计可让报告门禁通过并以退出码 1 如实拒绝代码。


> RQG-generated `docs/api-reference/` is a disposable tool artifact and is excluded before QG203/QG205 and changed-file accounting. This exemption is path-scoped to the canonical generated output; it does not exempt ordinary project documentation.

### QG212 · 项目原生 C/C++ 编译事实（v0.22.5 开发态）

对自动发现的 `compile_commands.json` 中可匹配的第一方 C++ 翻译单元，
以**原项目编译器和已声明的语言标准/宏/include** 执行无产物语法检查。
退出非零生成 `QG212 ERROR`，附原始编译命令、退出码、诊断与编译器族；
不能证明是源码缺陷还是构建依赖问题，不自动裁决 REJECT。
不存在权威编译命令或工具链不可用仍以 `QG208 INFO` + `REVIEW_REQUIRED`
显式报告。默认 CLI 自动识别；不得要求用户填写语言或猜 `-std=c++20`。
**QG212 属于 Skill 自身 Findings，但其独立 GCC/Clang 真值只作为 Oracle，
正式回归须核对官方 `audit` 生成的 `修改说明.md` 和只读 `verify`。**

### QG210 / QG214 · 受限 R2/R3 源绑定审计（v0.22.5 开发态）

`QG210 WARNING` 只在无父类、未装饰、无自定义动态属性入口的
构造方法直线语句中，确认同一实例字段在任何可见写入之前被读取，
且此直线段随后确实写入该字段。调用、控制分支、形参接收者重绑定、
类级成员与复杂动态构造返回 UNKNOWN。Python 的运行时开放世界不因
AST 顺序而变成封闭世界；这是条件性来源证书，不是生产崩溃证明。

`QG214 WARNING` 只记录 `except Exception` / `except BaseException` / 裸
`except` 的处理器**直接**返回静态可确认的字面量/显式默认参数，
且该处理器没有其他记录动作。`__del__` 的 CPython 析构协议例外。
它只证明发生了静默降级，**业务是否允许必须人工裁决**。窄异常处理、
存在日志/状态记录或无法确定的返回表达式不据此下结论；不与 QG007
简单累计为独立 Bug。这是一个有明确覆盖边界的检测子集，不能代替
一般性跨函数/副作用/提交状态分析。

所有 QG210/QG214 的有效检测验收必须由封印的官方 `audit` 写入
`修改说明.md` 的可复核 Finding（含原始源码 SHA256），并经只读
`verify` 核对产物；外部 Oracle 和 Scanner 直调不能取代该闭环。

### QG213 · 状态部分提交后异常转结果（v0.22.5 开发态）

对于同一个类中由构造函数源码直接初始化的内置 `set` 与 `dict` 状态，
只有在单个 `try` 的连续顶层语句中观察到两个**不同状态字段**的
`set.add` / `dict` 下标写入、且其 `except Exception` 或 `except BaseException`
存在不重新抛出的直接返回路径时，才产生 `QG213 WARNING`。
报告保存原文件 SHA、两次写入位置、catch 位置和 `OPEN_RUNTIME` 标志。
它证明的是“前一次已变更、后一次失败、随后返回”的**条件性路径**，
不是生产环境已发生该故障，更不意味着每个 `return None` 都是错误。
缺少构造端类型证据、字段重绑定不明、未知副作用和额外控制流均保持
未证明，不能靠变量名推断。该规则不替代既有 `QG007` 的泛异常提示，
也不得把两条规则简单计为两个独立 Bug。正式回归必须以经过封印的
官方 `audit` 生成的 `修改说明.md` 和只读 `verify` 为准。

### QG215 · 宽异常无记录吞没路径（v0.22.5 开发态）

`QG215 WARNING` 只在源码确证 `except Exception` / `BaseException` / 裸
`except` 处理器单条语句为 `pass`、`continue` 或将局部变量直接赋为
`None`/`False` 时产生。此类处理器没有日志、调用或显式重抛。
窄异常捕获、析构协议及嵌套函数错误归属不在证明范围。
这是条件性 **异常不可观察路径** 证书，不代表吞没操作违反业务契约；
合法的 best-effort、签名验证失败状态等必须在人工语义账本中判为 JUSTIFIED。
不能与 QG007/QG214 简单相加称作独立真实 BUG。
正式验收必须通过经完整性封印的官方 `audit` 将带源码 SHA 的 QG 写入
`修改说明.md`，再由只读 `verify` 核对产物。

### QG216 · 必需 TypedDict 字段的确定性生产者缺失（v0.22.5 开发态）

`QG216 ERROR` 仅在同一权威源码 Snapshot 的 TypedDict 声明存在必需字段，
且唯一绑定的返回类型为**直接** TypedDict（不包括 `list[TypedDict]` 等容器），
或显式变量注解为该 TypedDict，并且生产者构造的是**所有键均为静态字符串字面量、
无 `**` 展开**的字典时，才比较字段集合。漏掉必需字段构成该路径上的明确
Schema 契约违例，源码、Schema 行号、生产者、必需字段差集写入正式 QG 见证。
判定覆盖生产者自身的直接返回和 `if`/`try` 分支内的静态字典返回；
嵌套函数、lambda 和类作用域属于其他词法 owner，不继承外层返回 Schema。
`total=False`、`NotRequired`、动态键、字典展开、未绑定的跨模块/运行时别名和
没有直接声明的生产者均不可据此认定缺字段。这里只判断局部静态确定的生产者，
尚未证明后续所有调用路径或业务副作用。正式成果必须由封印 Skill 的
`audit → 修改说明.md → verify` 产出，不能用内部 Graph 观测代替。

### QG003/QG004/QG033 实际审计证据索引（Rev25 CLI hotfix）

上述规则发生 CODE_BUG/RULE_BUG 争议时，可对命中源位置执行：

```bash
python scripts/evidence_lookup.py --repo <repo> --rule QG004 \
  --file <repo-relative-path> --line <1-based-line> --name <variable> --kind variable --depth 3
```

必须追查调用现场、参数源、字段写入及独立真实合法 Case；源代码中的注解
`Optional`、`NotRequired` 只是一条类型声明，不能证明业务可缺，`dict.get` 名称也不构成真实风险证明。
若映射是稀疏反向索引，构造逻辑 `index.setdefault(edge.target,...)`、零入度节点 Case
共同提供合法未命中的证据；若未找到调用者，必须报告 provider 可能缺失，禁止把未知当作零调用。
QG033 的 `bool(set)` 正例须结合集合生产路径；不能把集合长度判定误报成字符串布尔解析。
CLI 只提供源码事实，不自动推断 QG003/QG004/QG033 的 CODE_BUG/RULE_BUG 结论。
