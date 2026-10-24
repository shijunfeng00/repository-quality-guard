# TestCase / TestUnit · 独立行为协议 v1

本协议的依据是公开接口的可观察行为，不是受测函数的内部实现形状。机器可读契约见 `references/test_case_schema_v1.json`。

## 一个 Case 对应一个独立的行为义务

- `case_id`：与旧测试函数名无关、在同一冻结集内唯一的行为身份。
- `language`：被测试的目标语言；Python、C++、Java、JavaScript 共用相同 Case Schema。
- `subject`：公开可调用或可运行的接口身份。临时测试框架内部名字不是行为身份。
- `kind`：`return`、`exception`、`instance` 或 `stateful`，分别描述返回/检测结果、异常、对象状态、状态序列。
- `input`：必须显式提供输入文件、调用实参或状态步骤；不允许无输入且无步骤的空跑。
- `expected`：独立冻结的期望输出/异常/状态/规则 Finding。任何缺失期望都不能通过。
- `contract_reason`：为什么期望结果代表有效公开行为，不写受测检测器的辅助函数名。
- `source_sha256`：当输入包含源码文件时，以源码 SHA256 与 Case 身份绑定。

## 可比的结果，而不是固定实现

`return_equals` 对照公开返回值，`exception_type` 对照异常类别，`state_sequence_equals` 对照每一步可观察状态，`finding_severities_exact` 对照指定规则实际 Finding 级别序列，`maybe_callback_present` 对照 Graph 是否保留有来源的可能回调边。

不能通过测试要求某个私有函数、局部变量、AST 模式、目录布局或旧规则编号永久存在/不存在。等价实现允许任意内部重构，但必须满足相同 Case 输入与公开结果。

## 缺少执行能力不得默认为 PASS

本版本只冻结了行为协议与真实 Python Scanner/Graph 基准；C++/Java/JS Runner 在各自节点实施。Provider 未具备、Oracle 未绑定、Runner 未执行、测试超时及等价变异无法区分时，分别报告 `UNSUPPORTED` / `INCONCLUSIVE` / `FAIL`，不能静默写成 `PASS`。

测试目的审查只针对新增或实质修改的当前 TestCase；已经删除的旧功能不再具有特定成功、拒绝或“永远不能存在”的测试责任。

## 执行结果账本（UT21–UT23）

Python `TestUnit.run` 的成功返回值与失败异常携带的 `report` 都可以调用 `to_dict()`，得到 `rqg.testunit.result.v1`；C++ 原生 `rqg::tests::run/execute` 输出同样的键，结构约束见 `references/test_result_schema_v1.json`。

- `cases` 对应原始输入顺序；单个 Case 错误不会使后续有效 Case 失去执行机会。
- `PASS` 表示实际公开行为满足独立预期，`FAIL` 表示输出、异常或状态序列不符合预期，`INVALID_TEST` 表示 Case 本身缺少合法输入、预期或比较器。缺执行能力不得用 `PASS` 替代，保留 `UNSUPPORTED` / `INCONCLUSIVE`。
- `steps` 按实际执行顺序给出每步的公开方法、执行序号、状态与结果摘要。出现第一次不符的真实执行时停止该 Case 后续状态步骤；**不会**继续在已错乱的状态上制造更多失败。迭代器定义错误时，保留之前已经执行过的步及最后一项 `INVALID_TEST`。
- 预期异常必须同时匹配异常类别与独立比较器；比较器必须返回 `bool`，不能以任意真值替代。普通公开调用抛出未预期异常是 `FAIL`，不是测试自身不可用。
- JSON 仅是结果表示。Oracle 对被测函数的公开值/异常/副作用负责；不能把 Python/C++ 测试框架自身的私有函数结构绑定为行为契约。

当前过渡期的 C++17 测试适配位于 `runtime/contracts/cpp/tests.hpp`：`ITestCase` 使用纯虚函数规定身份、种类、公开调用及步骤访问，`Case` 是具体实现；使用 C++ 编译器分别核验正确实现可编译、漏实现不可实例化。Java `TestUnit.ITestCase` 为真实 `interface`，`Case` 明确 `implements`，执行器仅接受接口实现；必须分别核验正例编译执行与漏实现反例编译拒绝。C11 不具备继承/抽象方法机制，保留显式回调结构和运行时校验，不能把 C 的成功编译冒充完整 Case 接口保证；后续 AST/Graph 静态分析需要基于实际接口消费者与调用路径证明。独立 CMake/CTest 正反样本必须随现行框架另行验收。这不代表已经对真实 SlowJSON 仓库完成验收；UT05 的正式结束仍要求独立目标实仓验证。

## 历史测试功能化迁移裁决（v0.22.6）

迁移的统计单位是**独立功能合同**，而不是旧 pytest 函数名、类名、源码形状或 PASS 数量。必须保留真正有价值的缺陷回归，但不因为某个旧测试已存在就认为它有永久保留义务。

1. **KEEP**：QG 规则实际命中 TP、明确不应命中 TN、已复现 FN/FP；关系图边的来源与绑定强度、UNKNOWN 边界；公开 CLI、Profile 解析、报告、发行封印等外部可观察合同。
2. **REWRITE**：旧测试虽定位真实业务风险，但断言仅涉及私有 helper 名、函数签名、AST 组织、调用步骤等内部形状。迁成独立输入→公开输出/异常/Finding/Graph 查询的 Case，冻结 Oracle 并验证变异后确实 FAIL，才删除原断言。
3. **DELETE**：只检查文档含某个文句、旧辅助函数存在、源码恰好按某种方式写、或被其他完整功能 Case 严格覆盖的冗余测试；删除前登记具体理由，不为维持测试数量增加空转发 Case。
4. **保留例外**：某种形状若属于正式外部合同（例如 Profile Schema、报告字段、Skill 元数据、可执行包内容和公开 CLI 参数），仍必须以**其真实使用方**验证，而不是硬编码源码字符串。
5. **四库 Case 来源**：从真实项目寻找正反场景，将源行为最小化、去敏并冻结独立真值。公共仓库仅保存匿名通用样本，真实项目路径和来源追踪放到外部私有证据账本。真实漏报补 TP、误报补 TN，并保留永久回归；不能从检测器当下的输出自动生成预期。
6. **覆盖率分账**：规则级 TP/TN 覆盖、公开 Scanner 的端到端触发、Graph/CLI 行为、历史测试复审数量及完整回归各自统计。对四库静态扫描从未触发过的 QG，仍须独立人工授权的 TP/TN，不允许将零 Finding 当成测试通过的证据。

结构重构前后使用同一源码 SHA256、同一实际加载 Profile、同一真实 Git/mbox 快照，比较 Finding 所有字段和顺序；变更测试源码而不改变规则语义也应保持同源 Scanner 指纹一致。完整 pytest 不代替私有 Profile 的正式 `audit → 裁决 → verify`。

### Java/C 原生 Provider 能力边界（UT06）

`runtime/contracts/java/TestUnit.java` 通过 `javac`/`java` 原生运行，支持 RETURN、EXCEPTION、INSTANCE、STATEFUL，输出与 Python/C++ 相同的 `rqg.testunit.result.v1` JSON 字段。`runtime/contracts/c/testunit.h` 通过 C11 编译器原生运行，支持返回结果和显式初始化操作的独立 Oracle。纯 C 缺少语言级异常机制；本版本也没有通用的有状态步骤序列适配，因此这些 Case 一律明确输出 `INCONCLUSIVE`，`succeeded=false`，绝不编造通过结果。

每个 Native Provider 必须对错误预期输出 `FAIL`、对重复身份输出 `INVALID_TEST`，而不能只提供 happy path。无编译工具链时属于未执行（`INCONCLUSIVE`），不能把 pytest 的 SKIP 当作对应节点 DONE。Node.js/前端事件行为需要真实前端项目单独验证，不能由 Java/C 的通用 Runner smoke 冒充。

## 唯一 Case 身份字段（v0.22.6）

对所有新建/迁移的跨语言测试合同，**唯一身份字段是 `case_id`**。`id` 不再作为别名或静默兜底。历史 JSON 只能在离线迁移脚本中经逐条唯一性校验显式转成 `case_id`，旧测试执行器、废弃的继承接口与旧测试步骤协议全部退役；旧来源台账是审计证据，不等于新测试 PASS。


### TypeScript/Go 原生抽象接口（v0.22.6，只提供接口，不提前迁移 IR）

- TypeScript: `runtime/contracts/typescript/testunit.ts` 定义 `ITestCase`，由 `Case implements ITestCase` 实现；`tsc --strict` 证明缺失抽象方法会产生 `TS2420`，真实 Node 正例提供可观察结果。
- Go: `runtime/contracts/go/testunit.go` 定义 `ITestCase`，`var _ ITestCase = (*Case)(nil)` 是编译期接口约束；实际 `go test` 正例与缺失接口方法的编译失败反例均属于发行验证门禁。
- 两者仅为当前原生接口可用性，**不代表 TypeScript/Go 扫描、Graph、全量 QG 或未来统一多语言 IR 已完成**。过渡适配器须在真正 IR 迁移节点按授权统一移交和删除；不能据此增加独立的运行时 Case 身份索引。
- Python 仍由独立 `rqgtest` 的 `ABC` 四方法合同保证；C++ 由纯虚接口、Java 由 `interface` 保证；C11 无继承/接口语言特性，只能采用显式 callback 合同。此处的原生编译成功、失败负例不能冒充 QG241 跨语言规则覆盖。
