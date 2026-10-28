# TestCase / TestUnit · 可观察行为协议

单元测试验证**公开接口在独立输入下的可观察结果**，而不是当前实现的函数名、AST 形状或源码组织。实际 Python 用法和命令见 [`TEST.md`](TEST.md)。

## Case 合同

每个 Case 需要明确：

| 项目 | 要求 |
| --- | --- |
| 身份 | 独立、唯一的 `case_id`；不依赖旧测试函数名 |
| 对象 | 被测试的公开入口及目标语言 |
| 输入 | 调用参数、源文件或状态操作序列 |
| 期望 | 独立确认的返回值、异常、状态变化或规则 Finding |
| 比较 | 明确的等价判定与失败条件 |
| 依据 | 公开行为/协议为何要求该结果；源码输入应固定来源或摘要 |

多组输入彼此独立；有状态步骤按顺序执行，并在首次不符时停止该 Case，避免继续基于错误状态产生虚假失败。普通受测入口抛出非预期异常是 `FAIL`；Case 缺输入、期望或合法比较器是 `INVALID_TEST`。能力缺失或未真正执行时保持 `UNSUPPORTED` / `INCONCLUSIVE`，不能记为 `PASS`。

## 测试迁移原则

- **保留**真实 TP/TN、复现的 FP/FN、公开 CLI/Graph/Profile/报告/封印及行为契约的回归。
- **重写**仅断言私有 helper、调用步骤、硬编码源码片段或实现形状的测试：保留业务场景，改用独立输入、期望及可观察结果。
- **删除**无实际行为意义的文案检测、历史实现锁定和被更完整 Case 覆盖的冗余测试；应说明删除依据，不用空转发 Case 维持测试数量。
- 对规则误报、漏报或变异测试，从真实正反场景固定 Oracle。不能以当前扫描器输出作为预期，也不能修改 Oracle 迎合被测实现。
- 规则 TP/TN、Graph/CLI、原生编译、完整单测和正式 `audit → 裁决 → verify` 分别报告，不互相冒充。

## 不同语言的接口与结果边界

| 语言 | 可用合同 | 边界 |
| --- | --- | --- |
| Python | `rqgtest.TestCase` / `StatefulTestCase`，`ITestCase` 四个方法 | 实际 `BatchResult.to_dict()` 返回 `passed`、`total`、`succeeded`、`invalid`、`results` |
| C++ | `runtime/contracts/cpp/tests.hpp` 的 `ITestCase` 与执行器 | 编译期纯虚方法检查；原生 JSON 结果 |
| Java | `runtime/contracts/java/TestUnit.java` 的 `ITestCase` 与执行器 | 编译期接口检查；原生 JSON 结果 |
| C11 | `runtime/contracts/c/testunit.h` 的显式回调 | 无语言级异常、通用状态序列；无法执行的类别为 `INCONCLUSIVE` |
| TypeScript | `runtime/contracts/typescript/testunit.ts` 的 `ITestCase` | 接口与编译期约束不代表 TS 源码审计能力已完成 |
| Go | `runtime/contracts/go/testunit.go` 的 `ITestCase` | 编译期接口与测试，不代表 Go 静态审计已完成 |

C、C++、Java 的原生执行器使用结果标识 `rqg.testunit.result.v1`，输出 `schema_version`、`passed`、`succeeded`、`cases`；每项 Case 有 `case_id`、`status`、`detail`、`steps_executed`、`steps`。这是**现有原生执行器的序列化格式**，不是 Python `rqgtest` 的 `to_dict()` 格式。没有统一的跨语言 JSON Schema 校验器；不得声称所有 Runner 产生相同 JSON。

接口存在、单个 smoke 编译通过都不能代替真实正反测试、目标项目部署回归或完整质量门禁。
