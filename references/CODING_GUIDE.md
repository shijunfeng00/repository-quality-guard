# Repository Coding Guide

本指南用于在代码写入仓库之前约束命名、接口、职责边界、契约、注释和抽象粒度。静态检查器负责事后发现高置信问题，但不能替代事前设计。

### 静态契约禁止猜测

内部上下游必须使用静态类型和固定字段契约。已确认的映射字段使用 `[]`，已确认的对象字段使用直接属性访问；生产代码不得用 `.get()`、`hasattr()`、`getattr()`、`setattr()`、`delattr()`、`vars()`、`__dict__`、`__getattribute__`、`inspect`/`operator` 反射、`setdefault()` 或成员存在性分支猜测正式契约。真正可选字段必须在 TypedDict、Pydantic、Protocol、Optional 或联合类型中显式表达，并只在唯一输入适配边界完成解析。


## 1. 总体原则

1. 先理解现有生产方、消费方、调用链和公共接口，再修改代码。
2. 优先复用已有明确接口；只有现有能力确实不能满足需求时才新增接口、参数、类或 helper。
3. 新增函数、方法、类、参数或 helper 前，必须先运行自动 API 目录生成与 BM25 能力检索；至少阅读 Top10 候选中的高相关源码、签名、docstring、调用方和所有者。BM25 无命中不等于能力不存在，还要继续检查 HEAD、父类、MRO、兄弟类和公共协作者。
4. 生产方负责产出合法结构，消费方按正式契约读取。外部不可信输入只在唯一边界校验和归一化。
5. 保持模块职责和依赖方向清晰，不把业务逻辑塞进 tracing、配置、序列化或适配层。
6. 不为了降低复杂度、函数长度或告警数量，把连续逻辑拆成大量一次性短函数。
7. 本次自然触达范围除完成需求外，应尽可能安全削减局部历史债务；不要借清债名义大范围重构无关模块、拆分类或迁移公共接口。
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
- 类成员 `_method`、`_field`、嵌套 `_Class` 仅由类自身和明确设计的继承路径调用；其他类、对象或模块访问均为 Critical。
- 不能通过 `other._records`、`manager._store`、`session._xxx` 穿透其他对象边界。
- 某能力确实需要跨模块或跨包调用时，应先确认已有 public 接口是否覆盖；没有时再设计正式 public 接口，而不是继续外部调用 private。
- production 中反射访问 private 一律 Critical；反射取得普通成员后再调用或注册为 callback/线程 target 也一律 Critical。tests 之外不得用反射规避静态所有权。
- 禁止 `def public(): return self._private()` 这类没有独立契约的方法套娃。简单 property 字段暴露不自动视为质量错误，但新增或修改 property/setter/deleter 必须作为接口变化进入人工审查；基线存量且未变化的不清算。
- `_method → method`、新增 `@property` 或改变 property/签名/装饰器都是接口变更，必须在报告中证明绝对必要、不可替代并核对全部调用方；“方便调用”不是理由。基线已有且未变化的 property 不作为本轮质量回归清算。
- tests 之外禁止 monkey patch、运行时改写类/模块属性、`mock.patch/patch.object/monkeypatch` 以及对固定类/模块使用 `setattr/delattr`；必须回到权威定义、依赖注入或正式插件注册点。
- 普通 public 反射只有在接收者静态契约可确认时才作为 Error；未知动态对象不凭猜测定罪，但反射结果一旦被调用/注册回调仍直接 Critical。

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
2. 目标尽可能达到 Critical/Error/Warning `0 / 0 / 0`；普通三档问题只要相对累计 Git 基线新增或升级就必须消除，`DELTA-*` 只能记录 BLOCKING 与处置方向，不允许例外保留。累计基线即本轮冻结的 accepted design baseline：用户指定 revision 优先，否则取任务开始时最后一次可确认 push/目标分支提交；不得移动到本轮中间 commit。存量问题不强制清零，但应在自然触达范围内尽可能减少。
3. 原样运行并保留目标项目既有回归测试；只有新增稳定行为或经 TEST-CHANGE 证明的真实契约变化才新增/修改测试，同时按需求同步 README 和调用方。
4. 逐项填写并通过 `修改说明.md` 固定契约。
5. 执行 `python scripts/quality_guard.py verify <repo>`；Portable 模式仅在仓库目录名与实际存在的 Profile 名完全一致时自动选择并打印来源，也可显式追加 `--profile <profile>`；Installed 模式使用冻结策略且不接受运行期 `--profile`。`PASS` 表示质量门禁允许进入任务后续步骤，`REVIEW_REQUIRED` 表示质量裁决仍需人工确认，`REJECT` 必须继续修改。这里的“进入提交”只描述质量状态，不授予 Git 写权限：报告必须给出建议 `git commit -m`，但是否实际执行 `git commit` 只服从当前任务的明确授权。没有 `verify` 结果、缺失建议 Commit 或仅有 `audit GENERATED/UNVERIFIED` 都不算审计完成。
6. 若本轮任务是升级仓库内受跟踪的 `.agents`，只使用当前正式 Skill 的 `python runtime/deploy.py <skill-root> <repo>/.agents/skills/repository-quality-guard [--profile <name-or-path>]`。**升级永远是全量原子替换**：旧版本号、旧目录结构或残缺状态都不是前置条件；staging 目标树必须与当前 release manifest 一致，旧版独有文件全部删除。根 `AGENTS.md` 为宿主共享文件：已有文件逐字节保留；仅在缺失时 create-if-missing，优先使用 Profile 模板，否则使用 generic 模板。Profile 在安装阶段冻结并参与 release seal，运行期不得切换。正式 `.skill.zip` 不携带 tests/缓存/历史 runtime，但必须携带 `offline/wheelhouse` 与 `offline/node_modules.zip`，以支持外部 Skill 在断网环境 bootstrap；部署后的 `.agents` 必须排除 wheel、`node_modules`、`offline/`，但必须包含 `scripts/quality_guard.py`、`scripts/git_hook_install.py`、`runtime/install_dependencies.py` 与完整 `runtime/src`。fresh clone 缺第三方依赖时运行安装器联网 bootstrap；外部 `.skill.zip` 可用 `--offline` 完成离线 bootstrap。**deploy 只是安装，不是持久化完成**：deploy 后检查 `git status --short --untracked-files=all` / `git check-ignore`，再至少执行 `git add -A -- .agents/skills/repository-quality-guard`；仅当本轮 deploy 确实同步了根 `AGENTS.md` 时才把它一并 stage。把新增/修改/删除全部纳入独立升级 commit；随后用 `git ls-files` / `git cat-file` 和 fresh clone 证明完整运行时已在 `HEAD`。fresh clone 必须实际跑 `doc-generate → audit → 填写修改说明.md → audit READY_FOR_VERIFY → verify`；项目要求托管 pre-push 时再安装/触发 hook 验证报告门禁。提交内容可以对齐项目历史，但不得覆盖 Git author/committer 身份；新提交沿用实际执行者 identity。
7. 最终检查后不得再修改代码或报告；否则必须重新执行最终检查。


## 协作设计基线与 Git 身份

- **先冻结 accepted design baseline，再开始修改。** 用户明确指定 commit 时优先使用该 revision；否则使用任务开始时最后一个可确认的目标分支基线。整轮修改、`audit`、`verify` 使用同一累计 baseline，不得把本轮自己生成的中间 commit 重新当 baseline 来隐藏接口或质量漂移。
- **设计权威来自 revision/明确授权，不来自作者姓名。** 禁止通过 `git log --author`、固定姓名/邮箱等方式判断哪份设计才有效；author/committer 只记录实际贡献者。
- **无明确新需求时，accepted baseline 的既有 public interface、参数、返回结构、协议字段与 owner 默认保持不变。** 实现应优先适配既有契约；确需变化时必须在 Q2/接口账本中给出本轮需求授权、调用方兼容性和验证证据。
- **提交身份与设计对齐彻底解耦。** 同事可以基于既有 accepted baseline 修改并延续其设计，但新 commit 使用同事自己的 Git identity；不得为了“和基线一致”设置 `--author`、`git config user.*` 或 `GIT_AUTHOR_*` / `GIT_COMMITTER_*`。
- **交付格式不削弱审计。** 直接修改、patch、mbox、bundle、zip 或完整仓库都必须另行完成并交付 `修改说明.md`。该文件默认不纳入业务 Git，最终回复仍必须直接报告报告门禁、静态门禁、最终状态和验证缺口。


## 变更意图与规格证据

accepted design baseline 是默认契约，但本轮明确需求可以有证据地改变它。需求证据优先使用用户明确指令、issue/任务单、公开接口文档和仓库已有规格；仓库若已经采用 OpenSpec，则把 proposal/spec/design/tasks 与归档后的正式 spec 作为变更意图和验收场景的高质量证据来源。Quality Guard 只吸收“变更意图形成可审计 artifact、完成后回写长期规格”的思想，不要求项目安装 OpenSpec，也不创建第二套规格运行时。

代码关系审计同理：只从统一 RepositoryAnalysisSnapshot 派生关系事实，吸收 CodeGraph 的 callers/callees/impact/affected-tests 思路，不依赖外部 CodeGraph 数据库、daemon、MCP 或第二 parser。

## 测试代码审计边界

测试是稳定行为/契约的回归护栏，不是随 production 实现同步更新的镜像。遵循“行为优先、实现细节最小化、对未来重构有韧性”的单元测试原则：

1. **既有 tests 默认稳定。** 修改 production 后，优先原样运行相关既有测试；失败先判断 production 是否回归，禁止把“修改 assertion 让 pytest 变绿”当作正常修复步骤。
2. **允许改既有测试的原因只有明确类别。** `REQUIREMENT_CHANGE`：真实需求/公开契约有意变化；`BUG_FIX`：旧断言把已确认 Bug 当成正确行为；`TEST_DEFECT`：测试自身错误、flaky 或 fixture/替身缺陷；`TEST_REFACTOR`：只重构测试结构且稳定行为和断言强度不变。除此之外默认 BLOCKING。
3. **测试公开行为，而不是当前实现形状。** 优先输入→输出、异常、状态转换、序列化、协议、可观察 side effect；接口的返回字段、格式、数值语义、默认值、错误码和已经修复的 Bug 应由稳定自动化回归锁住。不要把 private helper 名称、调用层数、源码字符串、某次重构后的函数位置当作产品行为。
4. **历史删除不是永久契约。** `assert "old_helper" not in source`、`REMOVED_*` 名单、“确保刚删除参数永不回来”等 change-detector 测试默认视为实现耦合候选；除非它对应长期安全/协议/架构不变量，否则不得作为稳定回归契约。应优先改为可观察行为验证；确属长期架构不变量时由架构门禁表达。
5. **负向测试必须保护稳定不变量。** 未授权操作必须失败、敏感字段不得泄漏、公开 response 不得含某字段等可以长期保持；“源码里不得出现某名字”本身通常不是稳定行为。
6. **Mock/Stub 用于隔离依赖，不用于证明 mock 自己。** 只有 collaborator 调用本身就是公开契约/事务/资源生命周期要求时，才固定精确调用；不要因 private 调用顺序变化让测试机械失败。
7. **测试变化由机器先静态化。** `QG170~175` 继续检查删除测试、断言下降、skip/xfail、空壳测试和语法错误；`QG192` 仅标记 implementation-coupled/change-detector 候选，不靠关键词直接定罪。既有用例修改/删除与高风险新增用例生成稳定 `TEST-CHANGE-*`，并记录旧可观察断言中本轮消失的表达式，避免“总断言数增加”掩盖某条稳定契约被删。`QG193` 再把真正位于 changed-production affected RUN set 的既有测试用例提升为关系语义候选；由语义审计决定 `JUSTIFIED/BLOCKING`。
8. **实现耦合候选不能靠一句“需求变了”豁免。** 每个 `TEST-CHANGE-*` 必须给出稳定行为/旧断言失效原因、真实需求/Bug/测试缺陷证据和可观察验证路径；裁决只有 `JUSTIFIED/BLOCKING`。命中 QG192 而要 JUSTIFIED 时，只能证明它表达长期 `STABLE_ARCHITECTURE_CONTRACT`；否则必须 BLOCKING，修正测试后重新审计。
9. **历史测试债不因 Skill 升级一次性阻塞。** QG192 只严格审计本轮新增/修改的测试；未触及的历史 implementation-coupled test 继续作为历史债务。任何后续修改一旦触及该测试，即按同一长期规则重新审计，不引入一次性清理状态或处置枚举。
10. fixture、stub、mock、替身字段和嵌套 helper 不进入生产接口 `ADD-*`；不要为测试方便扩大 production public API。


## 新增接口删除审判

新增文件、类、函数、方法、字段或全局变量前，必须依次回答：完全删除是否仍能满足契约；能否内联到唯一调用方；能否与相邻逻辑合并；能否复用 Git HEAD、父类、MRO、兄弟子类或公共协作者。连续单调用私有链默认内联，不允许仅以“封装、解耦、可维护性”为理由拆分。

静态规则只负责报告新增符号、调用边、唯一调用方、行数和差分。职责归属需要结合协议、事务、并发、生命周期和测试证据裁决，不能用正则匹配替代语义审查。

## Docstring 优先级

单个生产定义缺失、过短或章节不完整按 Error 定位修复；docstring 与完整 docstring 覆盖率必须相对 Git 基线单调不降，任何下降只生成一条聚合 Critical。历史低覆盖率本身不阻塞本轮，从而既保留零回退门禁，也避免数百条同质 Critical 淹没真实架构阻断。


### QG177：跨函数与改名移动后的修复洗白

不要把问题从调用者移动到 helper 后声称“调用者已改成直接访问”。Guard 会把参数名归一化，沿本地调用图把 helper 参数代回调用点，并用 APTED 映射改名或跨文件移动后的函数。只有旧的失败路径在调用闭包中真正消失，修复义务才算完成。

APTED 只用于已经通过节点标签预筛且节点数不超过上限的函数；大函数回退为线性标签多重集合相似度，避免最坏情况下的树编辑距离拖慢整个仓库扫描。

## 模糊语义不得未经授权硬编码

当任务需要理解上下文指代、业务意图、自然语言纠正、风险含义或其他模糊语义时，默认所有者是 Agent/LLM/正式分类模型，而不是关键词表、正则、字符串 contains、相似度阈值或手工打分。只有用户逐字明确授权，或仓库内存在可定位的授权政策，才允许引入该类启发式。

启用 `semantic-heuristic-candidates` 与 `strict-semantic-question` 的 Profile 会用 `QG178` 曝光新增候选，并强制在第 10 项写明候选、语义任务、规则机制与授权证据。确定性引用格式、协议语法或标识符校验只能由 Guard Profile 的精确代码指纹静态豁免；其他授权只读取 Git 基线中预先存在的授权账本。当前修改中新建授权或在报告中自称获得授权均无效。没有静态豁免或基线授权时，应删除启发式并把语义判断交回正确所有者。
