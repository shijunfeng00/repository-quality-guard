# Repository Quality Guard Rules

本文件是完整告警字典。当前运行时可发出的每一个 `QGxxx` 与 `RUF:FORMAT` 都必须在这里有明确解释。规则实现与最终证据以源码和审计报告为准，本文件负责让使用者定位、理解并继续修复。

## 严重级别

- `critical`：最高优先级，默认必须修复；若为本轮新增且确实无法安全消除，只能通过逐项 `DELTA-*` 强论证进入条件通过。
- `error`：静态证据明确的契约、冗余、边界或执行问题，本轮新增项默认必须修复；例外同样要求逐项 `DELTA-*` 论证。
- `warning`：高风险结构或启发式命中；目标仍是尽可能修复，但存量 Warning 不要求为清零而擅自扩大为大范围重构。
- `info`：候选信号；结合调用链判断，不做机械批量修改。

同一规则在不同证据强度或上下文中可能动态调整级别，因此表格描述“检查内容”，不把级别写死。

## 完整规则索引

### 解析、基础结构与文档

| 规则 | 检查内容 |
|---|---|
| `QG000` | Python 语法解析失败；当前文件无法继续做可靠 AST 审查。 |
| `QG001` | 短小且静态调用/引用很少的函数或方法，检查是否为一次性 helper。 |
| `QG002` | 短小且静态实例化/引用很少的类，检查是否为低价值包装。 |
| `QG003` | 内部稳定映射使用 `.get(key)` 猜测必需字段；静态确认且不是 lookup table/输入边界时按 Critical；类型不明时按 Error，测试代码独立审计。 |
| `QG004` | 内部稳定映射使用 `.get(key, default)` 静默兜底；静态确认且不是 lookup table/输入边界时按 Critical；类型不明时按 Error。 |
| `QG005` | 使用 `hasattr()` 探测静态对象字段，把类型契约降级成运行时猜测；静态类型对象按 Critical，类型不明时按 Error。 |
| `QG006` | 使用 `getattr(..., default)` 静默兜底属性缺失；静态类型对象按 Critical，类型不明时按 Error。 |
| `QG007` | 捕获宽泛异常；吞错并返回默认结果时风险更高。 |
| `QG008` | 单个类直接定义的方法数超过配置阈值，属于类职责臃肿；按 Critical。 |
| `QG009` | 通过 `ImportError/ModuleNotFoundError` 选择备用实现或兼容导入。 |
| `QG010` | 函数只把参数转发给另一个调用；单次使用的 private 薄包装按 Critical，公开协议包装结合调用方审查。 |
| `QG011` | 读取配置时提供默认值，可能掩盖部署缺项。 |
| `QG012` | 稳定映射使用 `setdefault()` 同时读取、兜底和写入。不得机械展开为 `if key not in mapping: mapping[key] = default`；`setdefault(...).add/append/extend/update` 是明确的聚合原语，不命中本规则。其他合法缓存/聚合应保留简洁语义或收拢到唯一状态入口，必需字段则直接索引。 |
| `QG013` | 类中短小方法比例过高，形成明显流程碎片化；按 Critical。 |
| `QG014` | 单个函数超过配置阈值，属于函数职责臃肿；按 Critical，且不得机械拆成碎片 helper。 |
| `QG015` | 函数参数总数过多，可能混合多项职责。 |
| `QG016` | 控制流嵌套层级过深。 |
| `QG017` | 函数分支数量过高。 |
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
| `QG041` | 向已知布尔参数位置传入布尔兼容字面量，降低调用可读性。 |
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
| `QG057` | 使用 `setattr/delattr/__dict__` 动态修改契约对象。 |

### 内部数据契约

| 规则 | 检查内容 |
|---|---|
| `QG060` | 对内部契约对象使用 `isinstance()` 猜测结构。 |
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
| `QG089` | Factory/Builder/Manager/Adapter 等包装类使用点很少。 |
| `QG090` | 存在连续多层无独立契约的转发调用链。 |
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
| `QG110` | Prompt 中禁止/必须类约束过多，应优先下沉到 schema 或代码契约。 |
| `QG111` | Prompt 由过多字符串碎片拼接。 |
| `QG112` | 常量集合枚举大量案例，疑似失败样例特判表。 |
| `QG113` | 同一长 Prompt 在多个模块重复实现。 |

### Profile 状态、Tool、History 与 Trace

| 规则 | 检查内容 |
|---|---|
| `QG120` | Tool 直接修改 Agent State，而非返回结构化结果交给统一入口归并。 |
| `QG121` | Tool 存在多种返回结构。 |
| `QG122` | Tool 返回裸字符串而不是稳定 JSON 结构。 |
| `QG123` | 读取型 Tool 调用了写操作。 |
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
| `QG149` | 跨所有者使用单下划线私有符号：包括其他对象/类的 `_member`、其他模块的 `_function`/`_Class`/`_variable`、私有模块路径及其别名导入；生产运行时代码按 Critical 处理，`tests/**` 单独报告为 Warning。标准库明确公开但采用下划线命名的确定 API（当前仅 `os._exit`）不按项目 private 处理。 |
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
| `QG161` | public 接口发生变化，但当前变更没有同步 README 中的公开能力、参数、返回结构或使用示例。属于接口审查账本，不由 QG179 重复拒绝；必须在 QG180/INTERFACE 的 `同步=` 证据中处理。 |

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
| `QG207` | **Info**：JS/TS 显式 `any` JSDoc 逃逸数量较多，提醒 `checkJs PASS` 不能代替动态 owner contract 证明。 |

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
| `QG168` | **Critical，绝对阻断**：当前生产代码存在连续三层以上的单调用私有 helper 链；每层只有一个静态调用方且无其他引用。即使 Git 基线已存在也直接 REJECT，默认压缩到最上游函数并删除中间层。 |
| `QG169` | 相对 Git `--diff-base` 新增缺失 docstring 的生产定义；比较稳定定义指纹而非覆盖率百分比。删除已记录定义或改变分母不命中，结构完整性由 QG028/QG030 独立审计。 |
| `QG176` | **Critical**：质量问题只被换了语法。比较 Git before/after 的 fallback 行为指纹；`.get/hasattr/getattr(default)/setdefault/异常兜底` 改成 membership、三元式、`try/except`、默认合并或显式初始化，而同一 receiver/selector 缺失时仍继续执行。 |
| `QG177` | **Critical**：规范 AST、APTED 函数映射与跨函数调用摘要证明旧修复义务仍存在。覆盖函数改名、跨文件移动、把问题搬入 helper，以及参数改名后的同一 receiver/selector 行为。 |
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
| `QG170` | 删除含既有测试用例的测试文件。 |
| `QG171` | 删除 Git 基线中已有的 `test_*` 测试用例。 |
| `QG172` | 测试断言数量相对基线减少，需检查是否弱化回归门禁。 |
| `QG173` | skip/xfail 数量相对基线增加，需检查是否掩盖失败。 |
| `QG174` | 新增测试文件缺少可识别的 `test_*` 用例或有效断言。 |
| `QG175` | 工作区测试源码存在语法错误，无法作为验证证据。 |
| `QG192` | **Info 语义候选**：本轮新增或修改测试读取/解析 production source、检查源码字符串存在/不存在、使用 `inspect.getsource/signature`、历史 `REMOVED_*`/removed/deleted/no_longer/legacy tombstone 等，疑似把实现历史固化为测试。它不直接判错；命中用例必须进入 `TEST-CHANGE-*`，若不能证明长期稳定架构不变量则必须 BLOCKING，修正为稳定行为/契约验证后重新审计。 |
| `QG193` | **Info 关系语义候选**：本轮修改或删除的既有测试用例，经 RepositoryRelationGraph 证明属于同轮生产变更的 affected RUN set。它不直接判定测试改错；Q11 必须说明旧断言为何因明确需求、Bug 修复或测试缺陷而失效，并给出先运行 accepted-baseline 测试的可观察证据，禁止把 affected tests 当成自动 EDIT set。 |
| `QG194` | **Info 关系语义候选**：本轮函数/方法/类接口变化存在静态可达生产依赖，但 RepositoryRelationGraph 无法证明任何既有测试覆盖。该规则只声明 `coverage relation unknown`，不等于“没有测试”；Q2/Q11 必须给出动态注册、fixture、HTTP/框架入口等真实行为测试路径，或补充稳定接口/返回契约回归测试。 |
| `QG980` | `修改说明.md` 缺少当前报告 schema 的固定章节、章节顺序错误，或应答的通用/profile 专项审判标题缺失、重复、改名。 |
| `QG981` | 报告 schema、change digest、tool_status 或 `RQG:AUTO` 自动事实块与当前 Git/接口/架构事实不一致。 |
| `QG982` | 生产 FILE/ARTIFACT/ADD/ARCH/DELTA/INTERFACE/PROTOCOL、测试 TEST-FILE/TEST-RISK/TEST-CHANGE、存量削减结论、通用/profile 专项审判或最终语义结论不完整；自动事实覆盖或符号不一致、重复套话、模型抬高静态状态、QG179 DELTA/QG168/正接口净额未标记 BLOCKING、新增/参数/核心协议变化缺少必要性与验证证据，或存量一个未减却没有范围级说明。 |
| `QG983` | 验证章节缺少 Guard、`git diff --check` 或项目验证命令，记录仍为空泛，或任一命令退出码非零。 |
| `QG984` | 提交章节缺少描述整个 accepted-baseline→target patch 的可直接执行多行中文 `git commit -m` 命令，主题/至少两条中文摘要不完整，patch digest 变化后继续沿用旧命令，或提交命令试图用 `git config user.*`、`--author`、`GIT_AUTHOR_*` / `GIT_COMMITTER_*` 覆盖当前贡献者 identity。设计基线按 revision/用户授权确定，绝不以 author 姓名确定；任何身份覆盖均使 `verify` REJECT。 |
| `QG985` | 模型抄写的生产新增函数、变量、类数量或二次减法复审 ADD 数量与工具事实不一致。 |
| `QG990` | Skill 发布清单、release seal、受保护文件数量或文件 SHA-256 不一致，或者受保护目录出现未登记文件；工具在扫描业务仓库前直接拒绝。 |

| `QG189` | **Critical，Profile 可启用的当前树绝对阻断**：除 Profile 声明的权威状态类型内部及已登记的受控事务/流水账 API 外，调用方不得通过下标/属性赋值、嵌套容器方法、局部别名、`getattr/setattr/vars/__dict__`、`dict/list/object` 基类写入口、动态成员调用或 ctypes/id 反射修改状态。当前树存在即 REJECT，不适用历史债务豁免。 |
| `QG190` | **Critical，Profile 可启用的当前树绝对阻断**：生产代码禁止 `eval`、`exec`、`compile`、`__import__` 与 `importlib.import_module`。Profile 非阻断域仍扫描并独立分账；生产当前树存在即 REJECT。 |
| `QG191` | **语义候选**：本轮代码手工拼装已有协议/传输对象（例如 OpenAI tool-call wire）而没有复用既有协议 owner；要求先检索并复用唯一所有者，确有独立长期契约时再以 Q3/Q5 证据裁决。 |

生成阶段会立即检查报告；`--final-check` 会在不写文件的情况下重新计算事实。模型最终状态只能等于或严于 `tool_status`：可以把静态 `ACCEPT/REVIEW_REQUIRED` 降为 `REVIEW_REQUIRED/REJECT`，禁止抬高。存在 `BLOCKING` 时最终状态必须为 `REJECT`；接口/协议变化单独形成 `REVIEW_REQUIRED`，纯删除接口不触发；只读审计可让报告门禁通过并以退出码 1 如实拒绝代码。
