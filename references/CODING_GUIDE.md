# Repository Coding Guide

本指南用于在代码写入仓库之前约束命名、接口、职责边界、契约、注释和抽象粒度。静态检查器负责事后发现高置信问题，但不能替代事前设计。

### 静态契约禁止猜测

内部上下游必须使用静态类型和固定字段契约。已确认的映射字段使用 `[]`，已确认的对象字段使用直接属性访问；生产代码不得用 `.get()`、`hasattr()`、`getattr()`、`setattr()`、`delattr()`、`vars()`、`__dict__`、`__getattribute__`、`inspect`/`operator` 反射、`setdefault()` 或成员存在性分支猜测正式契约。真正可选字段应在正式类型契约中显式表达，但 TypedDict、Pydantic、Protocol、Optional 或联合类型的语法可选性不等于真实业务允许缺失；必须再沿生产者的合法初始化/赋值路径及消费端有意义的不同处理独立证明，仅在唯一输入适配边界校验外部数据。外部 framework/plugin 的 optional contract 可以保留动态读取，但必须有静态依赖/stub/继承或唯一 adapter 证据；仅出现一个 import 名、变量名或缺少依赖都不能自动把内部契约降级为 external optional。


## 检索与契约证据

修改前按 [ARCHITECTURE.md](ARCHITECTURE.md) 查询已有能力、符号上下游及变量来源；`evidence --rule QGxxx` 在 [RULES.md](RULES.md) 查检查目标和修复方向。必要时沿 **Producer → Schema/Guard → Consumer → Side Effect** 核对源码及独立正反 Case；`UNKNOWN` 不等于不存在，也不能凭 `.get()`、`hasattr()` 的语法替换宣称问题已解决。

QG003/QG004 尤其要区分稳定内部契约、真实可选输入和外部边界：类型标注可选不足以证明业务缺失合法，必须确认生产者可达路径及消费者的非空操作分支；合同必需字段应 fail-fast。规则的 TP/TN、误报或漏报由独立真实行为验证，不能为通过当前审计修改 Oracle 或扫描器。

## 测试边界

测试应验证公开接口在独立输入下的可观察结果，而不是测试代码组织、私有字段、源码字符串或固定调用路径。Python `TestCase` 只负责目标引用、输入、独立 Oracle；统一执行器 `TestUnit` 调用目标，Case 方法不得自行执行被测入口。测试目录不得自建 TestUnit、包装函数或额外 Case 状态。具体接口、实例方法及执行命令见 [TEST.md](TEST.md)，跨语言/正反例协议见 [TEST_CASE_PROTOCOL.md](TEST_CASE_PROTOCOL.md)；对应静态规则见 `QG240`–`QG246`。

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

## 16. 修改完成标准

1. 使用冻结的 Git 设计基线及实际 Profile 进行接口检索、最小修改与受影响行为测试；不为消除扫描结果改测试 Oracle。
2. 对本轮新增或升级的普通 C/E/W 修复归零；触达范围的存量债务逐项修复或给出合法边界证据和关闭条件。未触达的历史债务不因此要求全仓清零。
3. 运行 `audit`，以实际源码及测试填写 `修改说明.md` 的人工字段，报告中包含覆盖完整 patch 的 Git commit 建议，但不视为执行提交授权。
4. 用同一基线、模式和 Profile 运行只读 `verify`，保留真实退出码、待裁决项及测试结果。若源码/报告再变更，重新完成验收。见 [AUDIT.md](AUDIT.md)。
5. 受托升级宿主 `.agents` 时使用 `runtime/deploy.py` 原子替换、检查 Git 纳入状态、在 fresh clone 验证；发行包与安装态的差异及 hook 见 [SKILL.md](../SKILL.md)。

## 设计基线与变更意图

用户指定 revision 优先，否则冻结任务开始时可确认的目标分支基线；本轮 `audit` / `verify` 不得滚动基线。既有公开 API、参数、返回及 owner 默认沿用，除非需求明确授权改变并验证全部消费方。设计权威来自 revision、需求和公开规格，不来自 Git author 姓名；实际提交者身份不因设计沿用而伪造。

项目采用 OpenSpec 等规格工具时，可把既有任务、设计和正式规格作为变更意图证据，但 RQG 不要求安装第二套规格运行时，也不构建第二套 CodeGraph。测试的可观察行为与独立 Oracle 参见 [TEST_CASE_PROTOCOL.md](TEST_CASE_PROTOCOL.md)。

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


## 跨函数代码更改的契约校验

审计新增债务必须证明 before→after 的真实变化及 Producer→Schema→Consumer→Effect 因果路径；历史未改动代码里的相似 fallback 不自动属于本次新增。
