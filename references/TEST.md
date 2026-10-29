# rqgtest · 测试编写与运行

Python 3.10+ 单元测试框架。测试类声明真实入口、输入和独立预期值；`TestUnit` 负责调用、比较、进程池调度与结果报告。

## 目录

1. [安装与运行](#安装与运行)
2. [编写 TestCase](#编写-testcase)
3. [状态与多组数据](#状态与多组数据)
4. [异常和执行配置](#异常和执行配置)
5. [命令行与结果](#命令行与结果)

## 安装与运行

在 RQG 源码根目录安装（部署 `.agents` 时自动完成）：

```bash
python -m pip install ./packages/rqgtest  # RQG 源码安装到 .agents 时会自动构建安装
```

项目目录：

```text
project/
├── calculator.py
└── tests/
    ├── test_calculator.py
    └── cases/                  # 可选的 JSON/JSONL/CSV/Pickle 测试数据
```

`calculator.py`：

```python
def add(a, b):
    return a + b
```

`tests/test_calculator.py`：

```python
from calculator import add
from rqgtest import TestCase

class AddCase(TestCase):
    def get_enterpoint(self):
        return add

    def get_input_data(self):
        return ((2, 3),)

    def get_output_data(self):
        return 5

    def equals(self, expected, actual):
        return expected == actual
```

在项目根目录运行 `python -m rqgtest.run`。预期 `TestUnit: 1/1 PASS`。

## 编写 TestCase

`ITestCase` 是唯一抽象接口，定义下列四个方法。业务测试直接继承 `TestCase` 或 `StatefulTestCase`，类名以 `Case` 结尾。

| 方法 | 职责 |
| --- | --- |
| `get_enterpoint(self)` | 返回生产函数、可调用对象或类对象本身 |
| `get_input_data(self)` | 返回 `(args,)` 或 `(args, kwargs)`，多组样本逐项提供 |
| `get_output_data(self)` | 提供独立确定的预期值，或与输入逐项对应的预期值 |
| `equals(self, expected, actual)` | 消费执行器传入的实际结果，返回 `bool` |

例如调用 `f({"a": 1})`，输入写作 `(({"a": 1},), {})`。类的构造调用也使用普通 `TestCase`，`get_enterpoint()` 返回类本身。

**只有 `TestUnit` 执行生产入口。** 四个 Case 方法只负责提供入口、数据和比较，不主动调用受测函数，不在测试目录定义自有 Runner，也不使用 lambda 包装入口。

## 状态与多组数据

多组独立输入使用 `TestCase`：

```python
def get_input_data(self):
    yield ((2, 3),)
    yield ((-2, 1),)

def get_output_data(self):
    yield 5
    yield -1
```

### 类的实例方法

`get_enterpoint()` 返回未绑定方法；`get_input_data()` 提供实例和方法参数，`TestUnit` 执行实际调用。例：

```python
# 生产代码：Calculator(c).calculate(a, b) -> (a + b) * c
class CalculatorMethodCase(TestCase):
    def get_enterpoint(self):
        return Calculator.calculate

    def get_input_data(self):
        return ((Calculator(10), 2, 3),)

    def get_output_data(self):
        return 50

    def equals(self, expected, actual):
        return expected == actual
```

连续调用同一个实例时，使用 `StatefulTestCase`；每个输入对应一项预期：

```python
class CalculatorSequenceCase(StatefulTestCase):
    def get_enterpoint(self):
        return Calculator.calculate

    def get_input_data(self):
        calculator = Calculator(10)
        yield calculator, (2, 3)
        yield calculator, (4, 5)

    def get_output_data(self):
        yield 50
        yield 90

    def equals(self, expected, actual):
        return expected == actual
```

实例方法和异步入口的可运行示例见 `packages/rqgtest/README.md`。

输入和预期数据可由 `JSONDataLoader`、`JSONLDataLoader`、`CSVDataLoader`、`PickleDataLoader` 读取 JSON、JSONL、CSV 或可信的单文件 Pickle 序列化记录；Loader 原样输出记录，不自动展开记录内部列表或字典。只接受明确的文件路径，不扫描目录。可选 `preprocessing(source, output)` 由具体项目定义，按需生成 `tests/cases/.cache/` 缓存；输入、预期组数必须相等。

## 异常和执行配置

默认 `Outcome.RETURN` 和 `CaseConfig(process="pool")`。预期异常使用：

```python
from rqgtest import Outcome, CaseConfig

def __init__(self):
    super().__init__(type=Outcome.EXCEPTION, config=CaseConfig(process="main"))
```

此时 `equals` 的 `actual` 为捕获到的异常实例。`process="pool"` 使用本次运行共享的、按可用 CPU 调度的进程池；`process="main"` 在协调进程执行。Stateful 每次操作的预期值若为异常类（如 `ValueError`），该操作按预期异常处理。

## 命令行与结果

```bash
python -m rqgtest.run                                    # 全部测试
python -m rqgtest.run --list                             # 列出 Case
python -m rqgtest.run --file tests/test_calculator.py    # 单文件
python -m rqgtest.run --files tests/test_a.py tests/test_b.py # 多文件
python -m rqgtest.run --case tests/test_calculator.py::AddCase
python -m rqgtest.run --report test-results.json         # 输出 JSON 账本
python -m rqgtest.run --rebuild-cache                    # 清理或强制覆盖本次测试范围的预处理缓存
python -m rqgtest.run --help                             # 其他选项
```

定向选择只加载所选文件，并完整检查被选文件；全量运行严格检查整个测试目录。空匹配、无效合同、输入预期不等长均失败。报告包含 PASS、FAIL、INVALID 及样本结果；源码运行期间被修改则标记 INVALID。

异步入口：`TestCase` 和 `StatefulTestCase` 自动等待 `async def` 返回的 Awaitable，支持同步/异步 Case 混跑，不需要 `is_async` 或新基类。Stateful 的异步状态操作共享事件循环，详情见 `packages/rqgtest/README.md`。
