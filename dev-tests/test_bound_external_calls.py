"""Effect rules require source-bound external APIs rather than symbol spellings."""

from __future__ import annotations

from pathlib import Path

from runtime.src.config import GuardConfig
from runtime.src.scanner import RepositoryScanner


def _scan(path: Path, text: str) -> set[str]:
    (path / "module.py").write_text(text, encoding="utf-8")
    findings = RepositoryScanner(path, GuardConfig()).scan().findings
    return {f.code for f in findings if f.code in {"QG053", "QG054", "QG055"}}


def test_canonical_import_and_alias_are_equivalent(tmp_path: Path) -> None:
    direct = _scan(
        tmp_path,
        "import requests\nimport subprocess\nimport time\n"
        "async def operation():\n"
        "    requests.get('https://example.org')\n"
        "    subprocess.run(['echo', 'ok'])\n"
        "    time.sleep(1)\n",
    )
    aliased = _scan(
        tmp_path,
        "import requests as r\nfrom subprocess import run as launch\n"
        "from time import sleep as snooze\n"
        "async def operation():\n"
        "    r.get('https://example.org')\n"
        "    launch(['echo', 'ok'])\n"
        "    snooze(1)\n",
    )
    assert direct == aliased == {"QG053", "QG054", "QG055"}


def test_developer_symbols_never_certify_external_api(tmp_path: Path) -> None:
    unimported = _scan(
        tmp_path,
        "async def operation(requests, subprocess, time):\n"
        "    requests.get('/local')\n"
        "    subprocess.run(['noop'])\n"
        "    time.sleep(1)\n",
    )
    renamed = _scan(
        tmp_path,
        "async def operation(x, y, z):\n"
        "    x.get('/local')\n"
        "    y.run(['noop'])\n"
        "    z.sleep(1)\n",
    )
    assert unimported == renamed == set()


def test_shadowing_rebinding_and_dynamic_receiver_remain_unknown(
    tmp_path: Path,
) -> None:
    cases = (
        "import requests\nasync def operation(requests):\n    requests.get('x')\n",
        "import requests\nrequests = object()\nasync def operation():\n    requests.get('x')\n",
        "import requests as r\ndef operation():\n    global r\n    r = object()\n"
        "async def other():\n    r.get('x')\n",
        "import requests\nasync def operation():\n    requests = object()\n"
        "    requests.get('x')\n",
        "from requests import get\nasync def operation(get):\n    get('x')\n",
        "async def operation(client):\n    client.get('x')\n",
        "if True:\n    import requests\nasync def operation():\n    requests.get('x')\n",
    )
    for text in cases:
        assert not _scan(tmp_path, text), text


def test_unknown_splat_and_explicit_timeout_do_not_invent_missing_options(
    tmp_path: Path,
) -> None:
    assert _scan(
        tmp_path,
        "import requests\nimport subprocess\n"
        "async def operation(options):\n"
        "    requests.get('https://example.org', **options)\n"
        "    subprocess.run(['true'], **options)\n",
    ) == {"QG055"}
    with_explicit_timeout = _scan(
        tmp_path,
        "from requests import get as fetch\n"
        "def operation():\n    fetch('https://example.org', timeout=3)\n",
    )
    assert "QG054" not in with_explicit_timeout


def test_imported_blocking_calls_inside_async_are_found(tmp_path: Path) -> None:
    result = _scan(
        tmp_path,
        "from time import sleep\nasync def operation():\n    sleep(1)\n",
    )
    assert "QG055" in result


def test_locally_shadowed_external_module_is_not_trusted(tmp_path: Path) -> None:
    (tmp_path / "requests.py").write_text(
        "class DynamicClient:\n    def get(self, path):\n        return path\n"
        "get = DynamicClient().get\n",
        encoding="utf-8",
    )
    assert "QG054" not in _scan(
        tmp_path,
        "import requests\ndef operation():\n    requests.get('local')\n",
    )


def test_comprehension_lexical_shadow_blocks_module_guess(tmp_path: Path) -> None:
    assert "QG054" not in _scan(
        tmp_path,
        "import requests\n"
        "def operation(objects):\n"
        "    return [requests.get('local') for requests in objects]\n",
    )


def test_httpx_response_constructor_is_not_a_network_request(
    tmp_path: Path,
) -> None:
    findings = _scan(
        tmp_path,
        "import httpx\n"
        "def operation():\n"
        "    return httpx.Response(200), httpx.Request('GET', 'http://test')\n",
    )
    assert "QG054" not in findings
