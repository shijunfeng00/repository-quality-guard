# Repository Coding Guide

本指南用于在代码写入仓库之前约束命名、接口、职责边界、契约、注释和抽象粒度。静态检查器负责事后发现高置信问题，但不能替代事前设计。

### 静态契约禁止猜测

内部上下游必须使用静态类型和固定字段契约。已确认的映射字段使用 `[]`，已确认的对象字段使用直接属性访问；生产代码不得用 `.get()`、`hasattr()`、`getattr()`、`setattr()`、`delattr()`、`vars()`、`__dict__`、`__getattribute__`、`inspect`/`operator` 反射、`setdefault()` 或成员存在性分支猜测正式契约。真正可选字段应在正式类型契约中显式表达，但 TypedDict、Pydantic、Protocol、Optional 或联合类型的语法可选性不等于真实业务允许缺失；必须再沿生产者的合法初始化/赋值路径及消费端有意义的不同处理独立证明，仅在唯一输入适配边界校验外部数据。外部 framework/plugin 的 optional contract 可以保留动态读取，但必须有静态依赖/stub/继承或唯一 adapter 证据；仅出现一个 import 名、变量名或缺少依赖都不能自动把内部契约降级为 external optional。


### QG Finding 的标准修复入口

任何 `QGxxx` 告警在修改代码前，先运行 `python scripts/quality_guard.py evidence --rule QG123 --rule QG153`（以实际编号替换；可批量），读取 `references/RULES.md` 的权威定义、修复方向及禁止做法；已有能力/接口定位时同一个命令加 `--api "能力描述" --repo <仓库根>`（复用正式 BM25 doc-search 和源码保鲜），再加 `--symbol <完整符号ID> --direction callers --depth 3` 查询上下游，取得既有图的静态已解析调用关系及真实源码定位，随后手动阅读完整函数体和 Producer / Schema / Consumer / Effect，不能凭片段猜测。图中无边、多态或 UNKNOWN 不等于调用不可能发生。Case 驱动证明合法输入/状态确实可达、缺失是否属于业务结果还是 invariant 违约、上游 guard 是否支配且未发生重赋值。**类型声明 NotRequired/Optional、框架允许缺失不构成业务 Case 证据**。必要时构造正反 Case 并与稳定基线比较；告警与真实契约冲突时只登记 RULE_BUG 待裁决，未经授权不改稳定 QG 的判定算法。Finding 消失不等于修复成功。

### 已裁决 QG003 / QG004 的业务 Case 要求

检测器与业务代码的正确性都应由真实输入→输出 Case 判断，而不是由语法、
默认值样式或实现细节直接判定。对于 QG003，先查接收者的类型、真实
方法 owner、父/祖父类和初始化字段；普通 `get()` 与 Mapping.get
必须区别，内部委托给字典的调用依旧审查。对于 QG004，先区分
`None` / 空字符串 / 空列表等有明确接口含义的返回与伪造的非空
`UNKNOWN` / 假 ID；`Optional` 注解只是返回值允许为空的证据，
仍需调用方及业务 Producer 验证空结果确实可达，不能将必需字段
静默变为默认。人工裁决以正反 Case 和上下游源码为准。
QG033 暂无本轮规则修改授权，不得借 QG003/004 变更一起豁免。

### 有效缺失 Case 的验证门槛

判断 `.get(key)` 是否必要时，不以“类型标了可选”或“语法允许”为结论。
先证明 producer 可能产生未命中，之后验证 consumer 将其转入
明确、非空操作的业务分支；如果缺失违反 schema/invariant，应 Fail-Fast。
静态证明有限时，继续以 UNKNOWN 保留复核，不得改写为等价 `if key in`
规避检测。正反 Case 必须使用实际输入→输出和真实 Scanner Finding 验收，
并使用其它仓库同类调用/字段保留原 Finding 身份的回归对照。

### 仓库记忆：先查询、再裁决

针对源码 `文件:行号`，优先用 `quality_guard.py evidence --repo ... --file ... --line ... --name ... --kind function|class|variable`。
变量追踪必须显示真实调用边上 **实参→形参**、所在函数候选赋值及附近源码，而不是只显示函数头。
类追踪展示继承/覆盖关系；函数追踪展示调用位置和多层静态链。
`DIRECT_CALL` 与 `MAY_DISPATCH` 是两种不同证据：前者源码精确绑定，后者
由类型注解、局部构造或构造函数初始化字段建立可能调用边（多态仍 OPEN）。
`--symbol`、`--file`/`--line` 统一展示调用点和上游来源；使用前先看边置信度，
不允许把 MAY_DISPATCH 当做程序必然执行/Guard 支配的证明。
可用 `--graph-stats` 量化已解析的函数调用连通性，用 `--variable self.field`
追查跨构造器注入字段的来源：核对 `Class.__init__` 里的字段写入、上游构造调用
的 keyword/positional 实参以及源字段自己的构造表达式。多继承冲突、不完整调用点
或未标注动态接收者保持 UNKNOWN；不要通过同名函数近似匹配提高连通率。
`--cache-dir` 生成仓库外内容摘要索引，改动源码或者 RQG Provider 后均须重建，
不能复用过期图。CodeGraph 1.6.2 可借鉴抽取/解析解耦、别名、反向 BFS、源码位点、
未解析事实保留的架构，但低置信猜测边不得直接成为质量审计的确定证据。
查询输出的 `[UNKNOWN]` 和缓存 `HIT` 只说明 provider 覆盖与快照状态；
**不是** runtime Traceback、字段必有证据、CFG 支配或业务 Case 合法性的替代物。

Case 驱动单元测试应检查：对真实受影响源码运行 CLI 后，是否能够读到正确生产者、形参绑定、调用点、缺失 Case 及必要的反例。
避免测试固定内部辅助函数名、实现步骤或 AST 解析器组织方式；只要证据的业务语义不变，内部实现可重构。
当现有图不足以回答必需证据链时，应暴露缺口并保留 UNKNOWN，不能用猜测补齐或据此擅改稳定 QG。

### 单元测试行为契约

单元测试使用输入、公开接口、预期输出或异常和独立比较器验证可观察行为。正常实现重构不得要求修改原业务预期。只保留最小且充分的终态行为与关键契约正反例；已由相同可观察结果覆盖的实现细节测试应删除，而不是永远叠加。业务契约变化时允许有证据地更新预期。禁止将非契约内部函数、局部变量、调用路径、装饰器、源码字符串、私有属性是否存在作为通过条件。测试实现方式与业务功能正确性分开评价；实际执行失败仍由测试执行器阻断交付。

#### 统一 Case / TestUnit 编写合同（Python）

1. **先列具体 Case 再写测试类**：明确目标公开接口、输入、真实应出现的返回/异常/状态转换、独立期望及相邻反例。测试代码按照 `references/TEST.md` 直接继承 `TestCase` 或 `StatefulTestCase`；数据可硬编码或通过标准 DataLoader 迭代。测试类与运行入口由统一框架提供。
2. **测试目录边界**：以测试根目录（常规 `tests/`、`unit_tests/`、`src/test/` 或目标仓库 `[tool.repo_quality_guard].test_roots`，pytest `testpaths`）为单位识别，所有后代 Python 源码都接受对应测试规范审查；`test_*.py` 是文件命名规范，不能反向凭文件名决定任意生产文件是测试。非常规 CTest/GTest 等构建目录可显式配置，不以排除具体生产目录的方式修误报。
3. 标准 Case 子类为封闭的数据/Oracle 容器：只有标准行为方法、`__init__` 和 docstring；不允许定义 `_scan_fixture`/`observe` 等额外测试包装方法、staticmethod/classmethod、类属性、嵌套 `def` 或 `lambda`。`get_enterpoint()` 应直指既有生产函数或可以证明只是引用的纯别名。四个 Case 方法均不可执行这个真实入口（直接调用、已证明别名或源码图 CALLS）；只有 TestUnit 可以调用生产目标，get_input_data/get_output_data/equals 必须保持独立。叶子 Case 的 `__init__` 只负责调用 `super().__init__(type=..., config=...)` 固定预期类型及进程策略，禁止新建或修改任何实例属性（含动态 `setattr` 和 `__dict__`）；不因子类形参名称另造额外检验规则，测试输入/Oracle 使用 `get_input_data()`、`get_output_data()` 和外部 fixture。不得用 `object.__new__` 等绕过受测目标真实初始化。`get_enterpoint()` 必须证明引用的函数来自本仓库的生产源码，而不是直接测试 Python 内置函数、TensorFlow 等外部 SDK 或测试类内部的包装 Callable。仓库 SDK 公开入口即使没有内部调用者，其向仓库生产函数/类/类型的源绑定调用或引用可以证明真实业务联系；纯叶子生产函数可以直接实现业务计算，不能仅凭没有其它内部出边报错。直接测试 TensorFlow 等第三方原生接口而未调用真实仓库函数的孤立测试必须拒绝，动态所有权保持 UNKNOWN。遇到旧测试优先质疑是否存在独立终态输出或关键转换义务；没有的应删除而不是包装迁移，保留最少且充分的直接函数输入/输出 Case。测试文件顶部的自由函数和变量已由 QG240 禁止；测试目录（`tests/`、`unit_tests/` 等）内即使文件名不以 `test_` 开头也必须接受相同检测，不能把包装器藏在 helper 文件名中。对生产代码的“仅测试使用”需要完整业务调用/导出/回调可达性证据，不得仅因调用图目前缺边就推断它是测试专用代码。
3. 标准 Case 方法、数据迭代方式及执行命令参见 `references/TEST.md`。`get_enterpoint()` 返回真实生产入口，`get_output_data()` 提供独立期望，`equals(expected, actual)` 必须观察实际结果并返回 `bool`。运行统一使用 `python -m rqgtest.run`，以真实调用和比较结果作为裁决依据。
3. **QG 检测器专用 Case 的两种预期都必须存在**：`TP` = 由独立规则合同/业务 Oracle 确认**应报告**指定 QG Finding（对语义审计规则也可只是预期 REVIEW 候选，不等于已证明生产缺陷）；`TN` = 输入合法或证据不足以达到该规则报告门槛，Scanner **不得**返回该 QG。*TP/TN 描述的是检测器判断，不是 TestUnit 执行状态*；两个实例在检测器实现正确时都应当 `PASS`。有混淆语义或灰区时先记录独立证据和预期，不得仅靠当前 Scanner 输出反填真值。边界缺陷应补相应 FN/FP 的修复回归对照，并使用独立 Oracle 裁决。
4. **一个 Case 是一份可独立重放的行为事实**：建议记录 `id`、`rule`、`kind`（TP/TN）、`source`、`expected`（至少明确 Finding 身份/数量/严重度；位置与证据在规则要求时也要核验）。同一规则至少覆盖一对 TP/TN；还应覆盖类型、继承、别名、上下游 Producer/Consumer、可达性以及不确定来源等适用边界。对真实业务代码，应同时冻结可观察业务输入→输出；只匹配语法模式或过度断言内部实现都不合格。
5. **先验收已完成的 QG，再迁移旧单测**：每批新增 Case 立刻用 QG240–245 扫描新测试源码、运行 `python -m rqgtest.run`、故意篡改 Oracle 验证确实会 FAIL，再运行原生回归和正式 `audit → verify`。未迁移的有效旧行为在新覆盖已证明等价之前不得删除。不要为减少 Finding 数量而放宽 QG。历史目录迁移独立处理。

例如，数据集可针对一个实际生产规则构造命中与不命中两个独立输入，并分别冻结预期 Finding。
每组数据由统一测试框架调用真实生产入口，并对比真实 Scanner 的公开结果；严禁复制检测器代码作为 Oracle。


## 1. 总体原则

1. 先理解现有生产方、消费方、调用链和公共接口，再修改代码。
2. 优先复用已有明确接口；只有现有能力确实不能满足需求时才新增接口、参数、类或 helper。
3. 新增函数、方法、类、参数或 helper 前，必须先运行自动 API 目录生成与 BM25 能力检索；至少阅读 Top10 候选中的高相关源码、签名、docstring、调用方和所有者。BM25 无命中不等于能力不存在，还要继续检查 HEAD、父类、MRO、兄弟类和公共协作者。
4. 生产方负责产出合法结构，消费方按正式契约读取。外部不可信输入只在唯一边界校验和归一化。
5. 保持模块职责和依赖方向清晰，不把业务逻辑塞进 tracing、配置、序列化或适配层。
6. 不为了降低复杂度、函数长度或告警数量，把连续逻辑拆成大量一次性短函数。
7. 本轮实际触达的生产 `.py` 文件必须同时清理其真实历史 Critical / Error / Warning 债务：历史 baseline 不能成为跳过修复的理由。合法的外部配置默认、协议兼容或边界行为只有在语义账本给出源码/调用方证据并裁决为 JUSTIFIED 后才不视为债务；严禁通过等价语法改写把 fallback finding 洗掉。若某文件并非本轮需求 owner，应优先撤销无关改动、缩小 touched scope，而不是借清债名义扩大重构。
8. 修改公共接口、配置、命令、HTTP/SSE/Tool 协议或返回结构时，同步调用方、测试和 README。Profile 声明的状态键、事件 envelope/content 与返回/流式键属于核心协议；新增前必须先证明不能复用现有字段，并给出上下游兼容、验证和回滚。
9. 代码、注释和 prompt 只陈述当前真实契约；历史兼容结论放在基于 Git before/after 的修改报告中。
10. 模块级常量必须存在真实静态调用方、显式导出或正式注册证据；全仓检索后确认未使用的常量直接删除，不以“兼容”或“以后可能用”保留。静态可证明不可达的分支或语句同样直接删除。

## 2. 基础命名

| 对象 | 规范 | 示例 |
|---|---|---|
| Python 文件、模块、包 | `snake_case` | `slot_router.py` |
| 类、异常、枚举 | `PascalCase` | `HistorySession` |
| 函数、方法 | 动词开头的 `snake_case` | `load_config()` |
| 参数、局部变量 | 语义明确的 `snake_case` | `session_id` |
| 常量 | `UPPER_SNAKE_CASE` | `DEFAULT_TOPK` |
| 布尔值 | `is_`、`has_`、`enable_`、`use_`、`allow_`、`should_` | `enable_thinking` |

避免在长作用域、公共签名和核心状态中使用 `data`、`obj`、`item`、`info`、`tmp`、`handler`、`process`、`helper` 等模糊名称。短循环中的 `item` 可以使用，但离开局部上下文后必须换成领域名称。

函数名必须说明真实动作：

```python
load_project_config()
build_query_routes()
serialize_tool_result()
resolve_session_id()
validate_chunk_contract()
```

不要使用不能说明行为的名称：

```python
process()
handle()
do_work()
helper()
normalize_data()
```

## 3. 统一项目词汇

新代码和本次修改代码统一采用下列名称。既有公共接口不能为了统一拼写随手全仓改名；需要迁移时必须作为明确的接口变更处理。

| 概念 | 统一名称 | 不再新增 |
|---|---|---|
| 单个文件名 | `filename` | `file_name` |
| 多个文件名 | `filenames` | `file_names` |
| 截断数量 | `topk` | `top_k` |
| 配置 | `config` | 公共签名中的 `cfg`、`configuration` |
| 单个元数据映射 | `metadata` | — |
| 多个元数据 | `metadata_list` | `metadatas` |
| 用户原始问题 | `question` | 无语义变化的 `query_text`、`text` |
| 单条检索查询 | `query` | `question` |
| 多条检索查询 | `queries` | `query_list` |
| 文档标识 | `doc_id` | 同一链路中混用 `document_id` |
| 会话标识 | `session_id` | 泛化的 `id` |
| Agent 名称 | `agent_name` | `tool_name` 代指 Agent |
| Tool 名称 | `tool_name` | `agent_name` 代指 Tool |

`question` 表示用户原始输入；`query`/`queries` 表示进入检索系统的查询。上下游没有语义变化时，不要在每一层换一个同义名称。

## 4. 常用数据名称

- `response`：HTTP、LLM、OpenSearch 等外部响应对象。
- `payload`：待传输或已反序列化的传输结构。
- `result`：一次业务函数执行的结果。
- `record`：持久化或历史记录。
- `state`：项目运行状态对象。
- `config`：配置对象或配置映射。

优先使用 `http_response`、`tool_result`、`request_payload`、`history_record`、`agent_state`、`runtime_config`，不要让所有对象都叫 `data` 或 `result`。

## 5. Public / Private 边界

### Public

不带下划线的模块符号、方法和属性代表可被边界外调用的稳定接口：

- 必须有清晰类型。
- 必须有完整多行 docstring。
- 修改签名、参数、返回类型或字段时，必须检查调用方、测试和 README。
- 应通过明确的模块或包出口暴露。

### Private

单下划线表示内部实现：

- 模块级 `_function`、`_Class`、`_variable` 仅由定义模块内部使用；任何其他模块的直接导入、别名导入或 `module._symbol` 访问均为 Critical。
- 类成员 `_method`、`_field`、嵌套 `_Class` 仅由定义类自身调用；子类不能直接访问父类 `_member`（包括 `self._member`、`super()._member`），需要继承复用则应为其设计无下划线的正式可继承方法。其他类、对象或模块访问均为 Critical。
- 不能通过 `other._records`、`manager._store`、`session._xxx` 穿透其他对象边界。
- 某能力确实需要跨模块或跨包调用时，应先确认已有 public 接口是否覆盖；没有时再设计正式 public 接口，而不是继续外部调用 private。
- production 中反射访问 private 一律 Critical；反射取得普通成员后再调用或注册为 callback/线程 target 也一律 Critical。tests 之外不得用反射规避静态所有权。
- 禁止 `def public(): return self._private()` 这类没有独立契约的方法套娃。简单 property 字段暴露不自动视为质量错误，但新增或修改 property/setter/deleter 必须作为接口变化进入人工审查；基线存量且未变化的不清算。
- `_method → method`、新增 `@property` 或改变 property/签名/装饰器都是接口变更，必须在报告中证明绝对必要、不可替代并核对全部调用方；“方便调用”不是理由。基线已有且未变化的 property 不作为本轮质量回归清算。
- tests 之外禁止 monkey patch、运行时改写类/模块属性、`mock.patch/patch.object/monkeypatch` 以及对固定类/模块使用 `setattr/delattr`；必须回到权威定义、依赖注入或正式插件注册点。
- 普通 public 反射只有在接收者静态契约可确认时才作为 Error；未知动态对象不凭猜测定罪，但反射结果一旦被调用/注册回调仍直接 Critical。

`__init__.py` 不得重导出外部私有符号，即使使用公开别名；`__all__` 和公开赋值别名也不得把私有实现变成对外 API。Profile 明确声明的非阻断路径（例如专用 profiling 工具）按其已声明策略审查，不得由通用扫描器猜路径豁免。

测试代码允许一个受控例外：为了验证内部不变量、资源生命周期或回归缺陷，`tests/**` 可以有限访问私有接口。此时应把访问集中在明确的 `Test*` 类、fixture 或测试模块中，并说明测试目的；不要为了测试便利给生产代码新增测试专用 public API，也不要把私有访问扩散到运行时代码或通用测试工具层。质量检查仍保留 Warning，便于审查是否真的有必要。

双下划线只用于 Python 标准协议，例如 `__iter__`、`__enter__`、`__len__`。不要用 name mangling 模拟普通私有成员。

## 6. `normalize` 使用限制

`normalize` 只用于数学意义明确、从名称即可识别的归一化。除明确数学对象外，滥用 `normalize*` 属于 Critical，例如：

```python
normalize_vector()
normalize_scores()
normalize_matrix()
normalize_probabilities()
normalize_embeddings()
```

外部输入的解析、强制转换、清洗、规范表示和字段补全不属于数学归一化，必须按实际行为命名。不要新增：

```python
_normalize_data()
_normalize_value()
_normalize_result()
_normalize_payload()
_normalize_response()
_normalize_records()
_normalize_content_blocks()
```

根据真实行为使用更精确的动词：

| 行为 | 动词 |
|---|---|
| 解析文本或协议 | `parse_*` / `decode_*` |
| 校验契约 | `validate_*` |
| 强制转换 | `coerce_*` |
| 建立标准表示 | `canonicalize_*` |
| 清理不安全内容 | `sanitize_*` |
| 查找实际对象 | `resolve_*` |
| 构造结构 | `build_*` |
| 序列化 / 反序列化 | `serialize_*` / `deserialize_*` |
| 提取字段 | `extract_*` |

不要仅把 `normalize` 换成另一个模糊词；名称必须暴露真实行为。

## 7. 父类、子类与唯一能力所有者

面向对象设计必须为每项能力确定唯一、稳定的所有者。不能为了减少父类方法数量，把仍被多个子类共享的公共能力删除后复制到具体子类；也不能因为局部调用方便，在子类新增与父类、MRO 或公共协作者近似的 helper。

静态差分采用偏保守但接收者感知的策略：

- HEAD 中子类经当前实例/类形参、其方法体顶层简单别名、`super(...)` 或显式父类接收者调用父类方法，WORKTREE 中调用边消失而父类仍在：至少 Warning，必须披露新调用链。外部对象、嵌套属性或裸函数的同名调用不属于继承边。
- 父类公共方法被删除或绕过，子类新增近似替代实现：Critical，应恢复正确公共所有者并删除子类复制。
- HEAD 继承关系消失：至少 Warning，必须证明组合替代、整体功能删除或新所有者完整。
- 子类 override 与父类实现完全相同：Critical，删除重复 override。
- 多个兄弟子类存在相同实现：Critical，检查上提父类、中间基类或提取公共协作者。
- 子类字段与父类字段保存同一名称、属性或下标来源：Critical；字面量默认值或各自新建对象不视为共享状态。

共同逻辑不机械地全部塞入父类：若逻辑只依赖纯输入且不属于继承生命周期，优先领域函数或组合对象；若只有部分子类共享，考虑中间基类；只有语义真正不同才分别实现，并在报告中给出行为差异和测试证据。

## 8. Helper 与抽象粒度

不要新增含义模糊的 `helpers.py`、`common_helpers.py`、`misc.py`、`utils2.py`，也不要使用 `*_helper()` 掩盖真实职责。

只调用一次、逻辑短、没有独立契约的一次性 helper 默认内联。不要为了降低圈复杂度、缩短函数、减少类方法数或让审计数字变好，拆出大量 5～15 行函数。

抽象必须 **earn its keep**：新增 wrapper/helper/facade 后，读者需要同时理解的 concept、mode、state、layer 或依赖关系应当减少，或者明确隔离事务、资源、协议等稳定边界。若只是把原表达式搬到新名字、再多跳一层调用，优先删除。重构比较的是整体认知负担，不是单个函数的行数。

满足以下任一条件时再抽取函数或类：

- 被多个调用方真实复用。
- 表达独立业务概念。
- 隔离资源生命周期、并发、事务、重试或协议边界。
- 可独立测试且有稳定输入输出契约。
- 消除真实重复实现。

单函数超过约 500 行才把长度本身作为问题信号。长函数真正需要检查的是不可达分支、重复逻辑、异常吞噬、状态边界混乱、多种无关职责和隐藏副作用。

## 9. Manager / Factory / Adapter / Builder

这些后缀只有在语义真实成立时使用：

- `Manager`：维护一组对象的生命周期和一致性。
- `Factory`：存在多实现构造分派。
- `Adapter`：连接两个不同协议。
- `Builder`：存在有意义的分阶段构建状态。

否则优先使用领域名称，例如 `RuntimePolicy`、`SlotRouter`、`HistoryStore`、`ToolRegistry`，不要为了显得架构化添加后缀。

## 10. 契约与防御式编程

内部必需字段由生产方、schema 或类型模型保证：

```python
chunk["ref_id"]
result["sub"]
config["reranker"]
```

不要在每个消费方写：

```python
chunk.get("ref_id")
chunk["ref_id"] if "ref_id" in chunk else None
getattr(chunk, "ref_id", None)
```

这些只是用不同语法继续猜契约。以下“修复”同样禁止：

```python
# `.get()` 洗成 membership，缺失语义未变
field = fields[path] if path in fields else None

`mapping.setdefault(key, empty).add/append/extend/update(...)` 是明确、原子的聚合表达，不应仅为了消除 API 名称而展开。QG012 会直接排除这类链式聚合；如果缺键初始化不是聚合语义，才继续审查其状态副作用。

# `setdefault()` 洗成显式初始化，代码更长且行为未变
if path not in writable:
    writable[path] = set()
writable[path].update(modes)

# `hasattr()` 洗成 AttributeError 兜底
try:
    serializable = value.serializable
except AttributeError:
    serializable = None

# 宽异常缩窄后继续返回默认值，仍然吞掉必需字段缺失
try:
    ref_id = build_ref(chunk)
except (KeyError, TypeError, ValueError):
    ref_id = chunk["ref_id"] if "ref_id" in chunk else ""
```

修复只有两种合法结果：

1. 字段必需：直接读取，例如 `ref_id = chunk["ref_id"]`，让生产方契约错误暴露；
2. 缺失本来合法：在唯一 schema/输入适配边界显式建模，或保留原有简洁 lookup 写法并给出边界证据。不得为了消除某个 API 名称把一行代码扩成多分支。

`QG176` 会比较 before/after 的 AST 行为指纹。只要 receiver、字段和“缺失时继续执行”的行为相同，换成三元式、membership、异常捕获、默认合并或显式初始化仍视为同一个问题。

外部不可信输入只在唯一边界完成：

1. 校验；
2. 转换；
3. 形成正式内部结构；
4. 下游严格消费。

LLM 生成 JSON、HTTP 外部输入和正式 schema 边界可以使用 Pydantic。已受控的 Agent 运行状态和 Tool 内部结果不要为了“更严格”重复包成多套互转模型。

## 11. 异常处理

- 不使用 `except Exception: pass`。
- 不记录异常后继续返回一个看似正常的默认值，除非接口明确属于 best-effort 观测能力。
- 转换异常时保留 cause：`raise DomainError(...) from error`。
- 对必需内部契约，错误应尽早暴露并修生产方。
- 对外部边界，错误应带足够上下文并转换成稳定协议错误。

## 12. 注释与 Docstring

标识符使用英文；业务、架构和约束注释使用中文；协议名和字段名保留原文。同一 docstring 不无理由中英混杂。

公开函数和方法使用完整多行 docstring，说明参数、返回值和必要异常。property 类接口以 QG180 的必要性、同步、调用方和验证证据为主，不为满足格式机械扩写 Args/Returns；简单 private 结构允许一行 docstring，但不能缺失，复杂 private 结构仍需完整说明。

注释解释为什么、边界、不变量和风险，不解释肉眼可见的代码。不要在运行时代码、prompt 或字段描述中写“保持旧版结构不变”“兼容此前行为”等无基线历史声明。

## 13. Import 规范

Import 排序和基础风格交给 Ruff：

1. module docstring；
2. `from __future__ import annotations`；
3. 标准库；
4. 第三方库；
5. 本项目包；
6. `TYPE_CHECKING` 类型依赖。

禁止 `from xxx import *`。避免 `sys.path.insert(...)`；独立脚本确实需要时统一使用明确的 `PROJECT_ROOT` 或 `REPO_ROOT`。函数内部 import 只用于可选依赖、延迟加载重型模型或解决明确循环依赖，并写明理由。

## 14. README 与接口同步

下列变化发生时必须检查并同步 README、示例、测试和调用方：

- public 类、函数、方法和成员。
- HTTP 路由、Header、SSE 事件。
- Tool schema、返回结构、配置字段。
- CLI 参数、公开文件协议和状态字段。

Private 实现变化不机械要求修改 README。README 只描述公开能力和真实使用路径，不记录内部 helper。

## 15. Profile 专项

项目专属状态边界、协议字段、检索策略、非阻断路径和附加规则必须由对应 Profile 声明；Core 不内置仓库名、业务类名、固定源码路径或组织策略。Portable 模式可显式/目录名精确匹配选择 Profile；Installed 模式只使用 deploy 时冻结并 seal 的 Profile。

## 17. 修改完成标准

修改完成前必须：

1. 运行 repository-quality-guard 生成或刷新自动事实。
2. 全仓目标仍是尽可能达到 Critical/Error/Warning `0 / 0 / 0`；普通三档问题只要相对累计 Git 基线新增或升级就必须消除，`DELTA-*` 只能记录 BLOCKING 与处置方向，不允许例外保留。累计基线即本轮冻结的 accepted design baseline：用户指定 revision 优先，否则取任务开始时最后一次可确认 push/目标分支提交；不得移动到本轮中间 commit。对**本轮实际触达的生产 `.py` 文件**实行更严格的 touched-debt gate：既有 C/E/W 必须逐项复核并修复真实债务；仅有明确边界契约证据的语义候选可以 JUSTIFIED，不允许用 baseline 身份或语法洗白保留真实债务。
3. 原样运行并保留目标项目既有回归测试；新增真实业务能力或修复缺陷时增加 Cases；仅在可观察业务契约变化时更新预期，同时按需求同步 README 和调用方。
4. 逐项填写并通过 `修改说明.md` 固定契约。
5. 执行 `python scripts/quality_guard.py verify <repo>`；Portable 模式仅在仓库目录名与实际存在的 Profile 名完全一致时自动选择并打印来源，也可显式追加 `--profile <profile>`；Installed 模式使用冻结策略且不接受运行期 `--profile`。`PASS` 表示质量门禁允许进入任务后续步骤，`REVIEW_REQUIRED` 表示质量裁决仍需人工确认，`REJECT` 必须继续修改。这里的“进入提交”只描述质量状态，不授予 Git 写权限：报告必须给出建议 `git commit -m`，但是否实际执行 `git commit` 只服从当前任务的明确授权。没有 `verify` 结果、缺失建议 Commit 或仅有 `audit GENERATED/UNVERIFIED` 都不算审计完成。
6. 若本轮任务是升级仓库内受跟踪的 `.agents`，只使用当前正式 Skill 的 `python runtime/deploy.py <skill-root> <repo>/.agents/skills/repository-quality-guard [--profile <name-or-path>]`。**升级永远是全量原子替换**：旧版本号、旧目录结构或残缺状态都不是前置条件；staging 目标树必须与当前 release manifest 一致，旧版独有文件全部删除。根 `AGENTS.md` 为宿主共享文件：已有文件逐字节保留；仅在缺失时 create-if-missing，优先直接复制 Profile 提供的完整 `AGENTS.md`，否则使用 generic bootstrap 资源。Profile 在安装阶段冻结并参与 release seal，运行期不得切换。Canonical full-repository ZIP 必须携带 `.git`、本地 Profile catalog，并可以携带 `README.md`、`README_zh.md` 与 authoring/release tools under `scripts/`，并必须携带 `offline/wheelhouse` 与 `offline/node_modules.zip` 以支持断网 bootstrap；不得携带构建缓存或历史 runtime。部署后的 `.agents` 必须排除 README、测试、`scripts/` authoring/release tools、wheel、`node_modules`、`offline/` 与 Profile authoring 文件，但必须包含 `scripts/quality_guard.py`、`scripts/git_hook_install.py`、`runtime/install_dependencies.py` 与完整 `runtime/src`。fresh clone 缺第三方依赖时运行安装器联网 bootstrap；canonical full-repository ZIP 可用 `--offline` 完成离线 bootstrap。**deploy 只是安装，不是持久化完成**：deploy 后检查 `git status --short --untracked-files=all` / `git check-ignore`，再至少执行 `git add -A -- .agents/skills/repository-quality-guard`；仅当本轮 deploy 确实同步了根 `AGENTS.md` 时才把它一并 stage。把新增/修改/删除全部纳入独立升级 commit；随后用 `git ls-files` / `git cat-file` 和 fresh clone 证明完整运行时已在 `HEAD`。fresh clone 必须实际跑 `doc-generate → audit → 填写修改说明.md → audit READY_FOR_VERIFY → verify`；项目要求托管 pre-push 时再安装/触发 hook 验证报告门禁。提交内容可以对齐项目历史，但不得覆盖 Git author/committer 身份；新提交沿用实际执行者 identity。
7. 最终检查后不得再修改代码或报告；否则必须重新执行最终检查。


## 协作设计基线与 Git 身份

- **先冻结 accepted design baseline，再开始修改。** 用户明确指定 commit 时优先使用该 revision；否则使用任务开始时最后一个可确认的目标分支基线。整轮修改、`audit`、`verify` 使用同一累计 baseline，不得把本轮自己生成的中间 commit 重新当 baseline 来隐藏接口或质量漂移。
- **设计权威来自 revision/明确授权，不来自作者姓名。** 禁止通过 `git log --author`、固定姓名/邮箱等方式判断哪份设计才有效；author/committer 只记录实际贡献者。
- **无明确新需求时，accepted baseline 的既有 public interface、参数、返回结构、协议字段与 owner 默认保持不变。** 实现应优先适配既有契约；确需变化时必须在 Q2/接口账本中给出本轮需求授权、调用方兼容性和验证证据。
- **提交身份与设计对齐彻底解耦。** author/committer 记录真实协作历史，默认由各仓库/开发者自己的 Git 配置与既有补丁元数据决定；RQG 不硬编码姓名邮箱，也不根据身份判断设计权威。需要保留他人真实 authorship 的导入/补丁流程可以按仓库协作规范处理。
- **交付格式不削弱审计。** 直接修改、patch、mbox、bundle、zip 或完整仓库都必须另行完成并交付 `修改说明.md`。该文件默认不纳入业务 Git，最终回复仍必须直接报告报告门禁、静态门禁、最终状态和验证缺口。


## 变更意图与规格证据

accepted design baseline 是默认契约，但本轮明确需求可以有证据地改变它。需求证据优先使用用户明确指令、issue/任务单、公开接口文档和仓库已有规格；仓库若已经采用 OpenSpec，则把 proposal/spec/design/tasks 与归档后的正式 spec 作为变更意图和验收场景的高质量证据来源。Quality Guard 只吸收“变更意图形成可审计 artifact、完成后回写长期规格”的思想，不要求项目安装 OpenSpec，也不创建第二套规格运行时。

代码关系审计同理：只从统一 RepositoryAnalysisSnapshot 派生关系事实，吸收 CodeGraph 的 callers/callees/impact/affected-tests 思路，不依赖外部 CodeGraph 数据库、daemon、MCP 或第二 parser。

## 单元测试

以独立的业务输入、公开接口输出、异常和状态转换 Cases 验证功能。测试不得使非契约的私有字段、内部调用路径、源码字符串或重构方式成为 PASS 条件。合法业务变更允许同步更新预期；有效回归案例按独立可观察价值保留；已经被更强终态 Case 完整覆盖的重复用例应合并或删除。单元测试的实际执行失败由原生执行器判定，不以历史测试文件身份或断言数量替代接口验收。

## Release identity 不得成为项目契约

正式源码、测试、fixture、artifact path、配置和运行分支应描述稳定能力、协议与领域身份，而不是某次发布的版本、RC、Git tag、commit SHA 或固定实现 digest。README.md 与 README_zh.md 可面向人类说明当前版本，因此是仅有的文档豁免；仓库外独立 release evidence 不进入扫描域。

- `QG203` 是通用 CRITICAL：只检查路径/文件名中的 release-like identity，例如版本化测试、fixture、artifact、history/release 目录名；它继续服从 baseline/history-aware delta，不把历史 CRITICAL 自动升级成绝对阻断。
- `QG205` 是通用 SEMANTIC 候选：依赖/API/协议/schema/迁移版本、普通源码/配置/正式文档中的版本、tag、commit/SHA/digest 可能完全合理，也可能是 release lineage 污染，必须逐项裁决；静态命中本身不直接 REJECT。
- Git tag 会被读取为额外证据；项目代码或测试如果显式依赖仓库已有 tag，不能因为“tag 确实存在”就自动合理化。

## 新增接口删除审判

新增文件、类、函数、方法、字段或全局变量前，必须依次回答：完全删除是否仍能满足契约；能否内联到唯一调用方；能否与相邻逻辑合并；能否复用 Git HEAD、父类、MRO、兄弟子类或公共协作者。连续单调用私有链默认内联，不允许仅以“封装、解耦、可维护性”为理由拆分。

静态规则只负责报告新增符号、调用边、唯一调用方、行数和差分。职责归属需要结合协议、事务、并发、生命周期和测试证据裁决，不能用正则匹配替代语义审查。

## Docstring 优先级

单个生产定义缺失、过短或章节不完整按 Error 定位修复；docstring 与完整 docstring 覆盖率必须相对 Git 基线单调不降，任何下降只生成一条聚合 Critical。历史低覆盖率本身不阻塞本轮，从而既保留零回退门禁，也避免数百条同质 Critical 淹没真实架构阻断。


### 跨函数代码更改的契约校验

审计新增债务必须证明 before→after 的真实变化及 Producer→Schema→Consumer→Effect 因果路径；历史未改动代码里的相似 fallback 不自动属于本次新增。
