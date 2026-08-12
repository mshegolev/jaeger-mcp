"""Entry-point smoke test — «пакет собрался, но не импортируется».

Именно этот класс отказа увёз на PyPI заведомо сломанный 0.6.0: колесо
собиралось, весь набор тестов был зелёный, а модуль ``jaeger_mcp.server`` —
тот, на который указывает console-script ``jaeger-mcp`` — не импортировался
вовсе, потому что ``mcp>=2`` убрал ``mcp.server.fastmcp``. Ни один тест не
трогал точку входа, поэтому никто и не заметил (``server.py`` до этого файла
показывал 0% покрытия).

Тест проверяет ровно три вещи и ничего больше:

1. модуль точки входа импортируется, и его ``mcp`` — настоящий ``FastMCP``;
2. ``main`` — то, что запускает stdio-сервер — существует и вызываемо, а
   строка из ``[project.scripts]`` действительно резолвится в него;
3. на инстансе зарегистрированы инструменты, и их ровно столько, сколько мы
   обещаем.

Сети и кредов не требуется: ``JaegerHTTPClient`` создаётся лениво, на первом
вызове инструмента, — импорт фасада и ``list_tools()`` остаются оффлайн.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata as importlib_metadata
from typing import Any

import pytest

# Точка входа console-script `jaeger-mcp`, как она объявлена в pyproject.toml.
CONSOLE_SCRIPT = "jaeger-mcp"
ENTRY_POINT_TARGET = "jaeger_mcp.server:main"

# Сколько инструментов должен отдавать фасад. Список — в tests/test_protocol.py;
# здесь важно только то, что регистрация вообще произошла и не «похудела».
EXPECTED_TOOL_COUNT = 15


@pytest.fixture(scope="module")
def server_module() -> Any:
    """Импорт модуля точки входа — первый и главный ассерт этого файла."""
    return importlib.import_module("jaeger_mcp.server")


def test_entrypoint_module_imports(server_module: Any) -> None:
    """`jaeger_mcp.server` импортируется и содержит собранный FastMCP-инстанс."""
    # Импорт здесь, а не в шапке: если `mcp.server.fastmcp` исчезнет, упасть
    # должен именно этот тест, а не сбор всего файла.
    from mcp.server.fastmcp import FastMCP

    assert isinstance(server_module.mcp, FastMCP), (
        f"jaeger_mcp.server.mcp должен быть FastMCP, получено {type(server_module.mcp)!r}"
    )
    assert server_module.mcp.name == "jaeger_mcp"


def test_main_is_callable(server_module: Any) -> None:
    """`main()` — то, что вызывает console-script, — на месте и вызываемо."""
    assert callable(server_module.main), "jaeger_mcp.server.main отсутствует или не вызываемо"


def test_console_script_resolves_to_main(server_module: Any) -> None:
    """Строка из [project.scripts] резолвится в ту же самую функцию `main`.

    Ловит рассинхрон между pyproject и кодом: переименовали/перенесли `main` —
    установленный `jaeger-mcp` перестанет стартовать, а обычные тесты
    останутся зелёными.
    """
    try:
        entry_points = importlib_metadata.entry_points(group="console_scripts")
    except TypeError:  # pragma: no cover - Python <3.10 API, здесь недостижимо
        pytest.skip("importlib.metadata.entry_points(group=...) недоступен")

    matching = [ep for ep in entry_points if ep.name == CONSOLE_SCRIPT]
    if not matching:
        pytest.skip(f"console-script {CONSOLE_SCRIPT!r} не найден: пакет не установлен (pip install -e '.[dev]')")

    (entry_point,) = matching
    assert entry_point.value == ENTRY_POINT_TARGET
    assert entry_point.load() is server_module.main


def test_tools_are_registered(server_module: Any) -> None:
    """Импорт точки входа поднимает и регистрацию инструментов, а не пустой сервер."""
    tools = asyncio.run(server_module.mcp.list_tools())

    assert tools, "фасад собрался, но не зарегистрировал ни одного инструмента"
    names = sorted(t.name for t in tools)
    assert len(names) == EXPECTED_TOOL_COUNT, f"ожидалось {EXPECTED_TOOL_COUNT} инструментов, зарегистрировано {names}"
    assert all(n.startswith("jaeger_") for n in names), names
