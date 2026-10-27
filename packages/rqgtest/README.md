# rqgtest

面向 Python 3.10+ 的单元测试框架，支持普通函数测试、有状态测试、文件数据和并行执行。

## 目录

- [快速开始](#快速开始)
- [TestCase](#testcase)
- [StatefulTestCase](#statefultestcase)
- [DataLoader](#dataloader)
- [异常与执行配置](#异常与执行配置)
- [命令行](#命令行)

## 快速开始

从 RQG 源码安装到 `.agents` 时自动从 `packages/rqgtest` 构建并安装；单独使用也可从源码安装：

```bash
python -m pip install ./packages/rqgtest
```

在项目根目录创建两个文件：

**`calculator.py`**

```python
def add(a, b):
    return a + b
```

**`tests/test_calculator.py`**

```python
from calculator import add
from rqgtest import TestCase

class AddCase(TestCase):
    def get_enterpoint(self):
        return add

    def get_input_data(self):
        return ((2, 3), {})

    def get_output_data(self):
        return 5

    def equals(self, expected, actual):
        return expected == actual
```

运行 `python -m rqgtest.run`，应看到 `TestUnit: 1/1 PASS`。

## TestCase

测试类以 `Case` 结尾，直接继承 `TestCase` 或 `StatefulTestCase`，实现四个方法：

| 方法 | 返回值 |
| --- | --- |
| `get_enterpoint()` | 被测函数、类或可调用对象 |
| `get_input_data()` | `(args, kwargs)`，或多组输入 |
| `get_output_data()` | 独立确定的预期值，或多组预期值 |
| `equals(expected, actual)` | 比较结果（`bool`） |

`args` 是位置参数元组，`kwargs` 是关键字参数字典。测试类只提供数据和比较方法；生产入口由 `TestUnit` 调用，不能在 Case 内执行后再生成预期值。测试类名、直接继承关系及测试目录结构须符合框架发现约定。

多组独立测试使用相同顺序的生成器：

```python
def get_input_data(self):
    yield ((2, 3), {})
    yield ((-2, 1), {})

def get_output_data(self):
    yield 5
    yield -1
```

## 异步生产入口

无需 `is_async` 或额外的 Async Case 类，框架会自动等待协程和其他 Awaitable；同步、异步测试可在同一批执行。四个 Case 协议方法保持同步，`equals` 返回 `bool`：

```python
async def fetch_count():
    return 7

class FetchCountCase(TestCase):
    def get_enterpoint(self): return fetch_count
    def get_input_data(self): return ((), {})
    def get_output_data(self): return 7
    def equals(self, expected, actual): return expected == actual
```

`StatefulTestCase` 的异步初始化与后续异步方法在一个事件循环中执行，后台 Task 可跨步骤存活；Case 完结后清理剩余 Task。无需将同步 Case 和异步 Case 拆分执行。

## StatefulTestCase

用于需要顺序操作同一实例、闭包或有状态函数的场景。下面的 `Counter(10)` 依次执行两次 `add`：

```python
from rqgtest import StatefulTestCase

class CounterCase(StatefulTestCase):
    def get_enterpoint(self):
        return Counter

    def get_input_data(self):
        yield ((10,), {})         # 初始化，不消耗预期值
        yield ("add", (2,), {})  # 调用实例方法
        yield ("add", (3,), {})

    def get_output_data(self):
        yield 12
        yield 15

    def equals(self, expected, actual):
        return expected == actual
```

其中操作项为 `(方法名或 None, args, kwargs)`；`None` 表示调用保留的可调用对象。无须实例初始化的有状态函数直接从操作项开始。

## DataLoader

`JSONDataLoader`、`JSONLDataLoader`、`CSVDataLoader`、`PickleDataLoader` 读取单个文件，分别产出整个 JSON 值、每行 JSON、CSV 字典行及逐条 Pickle 记录。容器类型记录不自动展开；Pickle 仅用于可信文件。

```python
from rqgtest import JSONLDataLoader

def get_input_data(self):
    for row in JSONLDataLoader("tests/cases/inputs.jsonl"):
        yield ((row["a"], row["b"]), {})
```

特殊格式可继承 `DataLoader`，实现 `_iter(path)`；需要预处理时可额外实现 `preprocessing(source, output)` 并声明 `_suffix`。缓存放在数据文件相邻的 `.cache/`（不提交 Git），不会自动检测源文件变化；变更后运行 `python -m rqgtest.run --rebuild-cache`。

测试文件放在 `tests/test_*.py`，数据放在 `tests/cases/`；共享的自定义读取器可放在 `tests/dataloaders/`。不要用 Loader 调用被测函数、构造依赖实际结果的预期值或替代 TestUnit 执行测试。

## 异常与执行配置

默认测试普通返回值，并使用共享进程池。若预期抛出异常，可在 Case 内设置：

```python
from rqgtest import CaseConfig, Outcome

def __init__(self):
    super().__init__(type=Outcome.EXCEPTION, config=CaseConfig(process="main"))
```

此时 `equals` 接收捕获到的异常实例。`process="main"` 在当前进程运行，`process="pool"` 使用进程池。Stateful 的单次操作也可用异常类作为预期值。

## 命令行

```bash
python -m rqgtest.run                                      # 全部测试
python -m rqgtest.run --list                               # 查看 Case
python -m rqgtest.run --file tests/test_calculator.py      # 指定文件
python -m rqgtest.run --case tests/test_calculator.py::AddCase
python -m rqgtest.run --report test-results.json           # JSON 报告
python -m rqgtest.run --help                               # 更多选项
```

支持 `--files`、`--tests-root` 与 `--max-samples`。无匹配项、无效 Case 和输入/预期数量不一致会失败；运行期间源码变化标记 `INVALID`。
