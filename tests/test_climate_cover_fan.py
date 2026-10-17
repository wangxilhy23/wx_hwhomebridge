"""climate / cover / fan 品类（Python 侧）测试。

测试不依赖 Home Assistant：用 FakeHass / FakeState 提供实体属性，
并替换真正的 HA service 调用与 C 上报入口，只断言映射结果。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hwhomebridge.c_capabilities import PYTHON_ONLY_PIDS, get_pid_index, is_c_supported
from hwhomebridge.product_matcher import DeviceInfo, ProductMatcher
from hwhomebridge.product_registry import ProductRegistry
from hwhomebridge.service_action import ServiceActionDispatcher
from hwhomebridge.service_router import ServiceRouter

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hwhomebridge"


class FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self, states):
        self._states = states

    def get(self, entity_id):
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self, states=None):
        self.states = FakeStates(states or {})


def _load_registry() -> ProductRegistry:
    registry = ProductRegistry()
    framework = json.loads(
        (COMPONENT / "config" / "product_registry.json").read_text(encoding="utf-8")
    )
    assert registry._parse_products(framework)

    adapters_dir = COMPONENT / "config" / "adapters"
    default_dir = adapters_dir / "default"
    for path in sorted(adapters_dir.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        adapter = registry._parse_adapter(data, str(path))
        assert adapter is not None
        if path.parent == default_dir:
            registry._defaults[adapter.pid] = adapter
        else:
            registry._adapters.append(adapter)
    return registry


@pytest.fixture(scope="module")
def registry() -> ProductRegistry:
    return _load_registry()


def _merged(registry: ProductRegistry, pid: str):
    adapter = next((a for a in registry.get_all_adapters() if a.pid == pid), None)
    return registry.merge(registry.get_product(pid), adapter)


def _dispatcher(registry, pid, entity_id, state=None):
    dispatcher = ServiceActionDispatcher()
    dispatcher._hass = FakeHass({entity_id: state or FakeState("off")})
    calls = []
    dispatcher._call_service_async = (
        lambda domain, service, entity_id, data=None: calls.append(
            (domain, service, entity_id, data or {})
        )
    )
    return dispatcher, _merged(registry, pid), calls


def _router_with_recorder(registry):
    router = ServiceRouter()
    router.registry = registry
    router.matcher = ProductMatcher(registry)
    router._hass = FakeHass({})
    recorded = []
    router._update_hw_bridge_status = (
        lambda sn, svc_id, payload: recorded.append((svc_id, payload))
    )
    return router, recorded


# ---------------------------------------------------------------------------
# 配置与匹配
# ---------------------------------------------------------------------------

def test_new_products_and_adapters(registry):
    assert registry.get_product_count() == 11
    assert set(registry.get_product("009").services) == {
        "switch", "mode", "temperature", "fanSpeed",
    }
    assert set(registry.get_product("00A").services) == {"switch", "position"}
    assert set(registry.get_product("00B").services) == {"switch", "fanSpeed"}

    climate = _merged(registry, "009")
    assert climate.services["mode"].ha_mapping.action == "set_hvac_mode"
    assert climate.services["temperature"].ha_mapping.action == "set_temperature"
    assert climate.services["temperature"].ha_mapping.value_range == (16, 30)
    assert climate.services["fanSpeed"].ha_mapping.action == "set_fan_speed"

    cover = _merged(registry, "00A")
    assert cover.services["position"].ha_mapping.action == "set_position"

    fan = _merged(registry, "00B")
    assert fan.services["fanSpeed"].ha_mapping.action == "set_fan_speed"


def test_match_new_categories(registry):
    matcher = ProductMatcher(registry)

    cases = [
        (DeviceInfo(
            device_id="ac-xiaomi",
            name="客厅空调",
            model="xiaomi.aircondition.mt8",
            manufacturer="Xiaomi",
            entity_domains=["climate"],
            entity_device_classes={},
        ), "009"),
        (DeviceInfo(
            device_id="ac-midea",
            name="主卧空调",
            model="Air Conditioner 22040087 (0)",
            manufacturer="Midea",
            entity_domains=["climate"],
            entity_device_classes={},
        ), "009"),
        (DeviceInfo(
            device_id="curtain",
            name="客厅窗帘",
            model="linp.curtain.ec1db",
            entity_domains=["cover"],
            entity_device_classes={},
        ), "00A"),
        (DeviceInfo(
            device_id="fan",
            name="风扇",
            model="dmaker.fan.p45",
            entity_domains=["fan"],
            entity_device_classes={},
        ), "00B"),
        # 无厂家适配器时由 auto_match 兜底
        (DeviceInfo(
            device_id="ac-other",
            name="书房空调",
            model="unknown.ac",
            entity_domains=["climate"],
            entity_device_classes={},
        ), "009"),
    ]

    for device, expected_pid in cases:
        product, strategy = matcher.match_with_detail(device)
        assert product is not None, device
        assert product.pid == expected_pid, (device, product.pid)
        assert strategy in ("model_keyword", "auto_match")

    # 现有灯品类行为不变
    bulb = DeviceInfo(
        device_id="light",
        name="客厅灯",
        model="yeelight.light",
        entity_domains=["light"],
        entity_device_classes={},
    )
    product, strategy = matcher.match_with_detail(bulb)
    assert product.pid == "002"
    assert strategy == "auto_match"
    assert set(product.services) == {"switch", "brightness", "cct"}


# ---------------------------------------------------------------------------
# 控制下发
# ---------------------------------------------------------------------------

def test_climate_dispatch(registry):
    state = FakeState("cool", {
        "hvac_modes": ["off", "cool", "heat", "dry", "fan_only", "auto"],
        "fan_modes": ["Low", "Medium", "High", "Auto"],
    })
    dispatcher, product, calls = _dispatcher(registry, "009", "climate.living", state)

    assert dispatcher.dispatch("SN", "climate.living", product.services["switch"].ha_mapping, {"on": 1})
    assert calls[-1] == ("climate", "turn_on", "climate.living", {})

    assert dispatcher.dispatch("SN", "climate.living", product.services["switch"].ha_mapping, {"on": 0})
    assert calls[-1] == ("climate", "turn_off", "climate.living", {})

    assert dispatcher.dispatch("SN", "climate.living", product.services["mode"].ha_mapping, {"mode": "1"})
    assert calls[-1] == ("climate", "set_hvac_mode", "climate.living", {"hvac_mode": "cool"})

    # 超出 value_range 时裁剪到 30
    assert dispatcher.dispatch(
        "SN", "climate.living", product.services["temperature"].ha_mapping, {"temperature": 35}
    )
    assert calls[-1] == ("climate", "set_temperature", "climate.living", {"temperature": 30.0})

    assert dispatcher.dispatch(
        "SN", "climate.living", product.services["fanSpeed"].ha_mapping, {"fanSpeed": "3"}
    )
    assert calls[-1] == ("climate", "set_fan_mode", "climate.living", {"fan_mode": "High"})


def test_cover_dispatch(registry):
    dispatcher, product, calls = _dispatcher(
        registry, "00A", "cover.living", FakeState("closed", {"current_position": 0})
    )

    assert dispatcher.dispatch("SN", "cover.living", product.services["switch"].ha_mapping, {"on": 1})
    assert calls[-1] == ("cover", "open_cover", "cover.living", {})

    assert dispatcher.dispatch("SN", "cover.living", product.services["switch"].ha_mapping, {"on": 0})
    assert calls[-1] == ("cover", "close_cover", "cover.living", {})

    assert dispatcher.dispatch(
        "SN", "cover.living", product.services["position"].ha_mapping, {"position": 42}
    )
    assert calls[-1] == ("cover", "set_cover_position", "cover.living", {"position": 42})

    # 越界裁剪
    assert dispatcher.dispatch(
        "SN", "cover.living", product.services["position"].ha_mapping, {"position": 150}
    )
    assert calls[-1] == ("cover", "set_cover_position", "cover.living", {"position": 100})


def test_fan_dispatch(registry):
    dispatcher, product, calls = _dispatcher(
        registry, "00B", "fan.living", FakeState("off", {"percentage": 0})
    )

    assert dispatcher.dispatch("SN", "fan.living", product.services["switch"].ha_mapping, {"on": 1})
    assert calls[-1] == ("fan", "turn_on", "fan.living", {})

    assert dispatcher.dispatch("SN", "fan.living", product.services["switch"].ha_mapping, {"on": 0})
    assert calls[-1] == ("fan", "turn_off", "fan.living", {})

    assert dispatcher.dispatch(
        "SN", "fan.living", product.services["fanSpeed"].ha_mapping, {"fanSpeed": 55}
    )
    assert calls[-1] == ("fan", "set_percentage", "fan.living", {"percentage": 55})


# ---------------------------------------------------------------------------
# 状态上报 / 查询
# ---------------------------------------------------------------------------

def test_climate_reporting(registry):
    router, recorded = _router_with_recorder(registry)
    product = _merged(registry, "009")

    router._report_by_type("SN", "switch", product.services["switch"], FakeState("cool"))
    assert recorded[-1] == ("switch", {"on": 1})

    router._report_by_type("SN", "switch", product.services["switch"], FakeState("off"))
    assert recorded[-1] == ("switch", {"on": 0})

    router._report_by_type("SN", "mode", product.services["mode"], FakeState("heat"))
    assert recorded[-1] == ("mode", {"mode": 2})

    router._report_by_type(
        "SN", "temperature", product.services["temperature"],
        FakeState("cool", {"temperature": 26}),
    )
    assert recorded[-1] == ("temperature", {"temperature": 26})

    router._report_by_type(
        "SN", "fanSpeed", product.services["fanSpeed"],
        FakeState("cool", {"fan_mode": "Medium"}),
    )
    assert recorded[-1] == ("fanSpeed", {"fanSpeed": 2})


def test_cover_and_fan_reporting(registry):
    router, recorded = _router_with_recorder(registry)
    cover = _merged(registry, "00A")
    fan = _merged(registry, "00B")

    router._report_by_type("SN", "switch", cover.services["switch"], FakeState("opening"))
    assert recorded[-1] == ("switch", {"on": 1})

    router._report_by_type(
        "SN", "position", cover.services["position"],
        FakeState("open", {"current_position": 40}),
    )
    assert recorded[-1] == ("position", {"position": 40})

    router._report_by_type("SN", "switch", fan.services["switch"], FakeState("on"))
    assert recorded[-1] == ("switch", {"on": 1})

    router._report_by_type(
        "SN", "fanSpeed", fan.services["fanSpeed"],
        FakeState("on", {"percentage": 60}),
    )
    assert recorded[-1] == ("fanSpeed", {"fanSpeed": 60})


def test_payload_key_routing(registry):
    from hwhomebridge.virtual_device import ServiceEntry, VirtualDevice

    router = ServiceRouter()

    climate = _merged(registry, "009")
    vd = VirtualDevice("SN-CLIMATE", climate, "dev-climate")
    for svc_id in climate.services:
        vd.add_service_entry(svc_id, ServiceEntry("climate.x", "climate", svc_id))
    assert router._determine_target_service({"on": 1}, vd) == "switch"
    assert router._determine_target_service({"mode": "1"}, vd) == "mode"
    assert router._determine_target_service({"temperature": 26}, vd) == "temperature"
    assert router._determine_target_service({"fanSpeed": "2"}, vd) == "fanSpeed"

    cover = _merged(registry, "00A")
    vd = VirtualDevice("SN-COVER", cover, "dev-cover")
    for svc_id in cover.services:
        vd.add_service_entry(svc_id, ServiceEntry("cover.x", "cover", svc_id))
    assert router._determine_target_service({"position": 30}, vd) == "position"


def test_build_char_state_json(registry):
    router = ServiceRouter()
    climate = _merged(registry, "009")
    cover = _merged(registry, "00A")
    fan = _merged(registry, "00B")

    def payload(pid_product, svc_id, state):
        return json.loads(router._build_char_state_json(
            svc_id, pid_product.services[svc_id], state, "test.entity"
        ))

    assert payload(climate, "switch", FakeState("cool")) == {"on": 1}
    assert payload(climate, "switch", FakeState("off")) == {"on": 0}
    assert payload(climate, "mode", FakeState("dry")) == {"mode": 3}
    assert payload(climate, "temperature", FakeState("cool", {"temperature": 24})) == {
        "temperature": 24
    }
    assert payload(climate, "fanSpeed", FakeState("cool", {"fan_mode": "High"})) == {
        "fanSpeed": 3
    }

    assert payload(cover, "switch", FakeState("open")) == {"on": 1}
    assert payload(cover, "position", FakeState("open", {"current_position": 30})) == {
        "position": 30
    }

    assert payload(fan, "switch", FakeState("on")) == {"on": 1}
    assert payload(fan, "fanSpeed", FakeState("on", {"percentage": 80})) == {
        "fanSpeed": 80
    }


# ---------------------------------------------------------------------------
# C 能力门控
# ---------------------------------------------------------------------------

def test_c_capability_table():
    assert PYTHON_ONLY_PIDS == {"009", "00A", "00B"}
    assert get_pid_index("001") == 0
    assert get_pid_index("008") == 7
    assert get_pid_index("009") == -1
    assert is_c_supported("008") is True
    assert is_c_supported("009") is False
    assert is_c_supported("00A") is False
    assert is_c_supported("00B") is False


def test_supported_product_still_registers(registry):
    """能力门控不能误伤 001–008 的既有品类。"""
    router = ServiceRouter()
    router.registry = registry
    router.matcher = ProductMatcher(registry)
    router.set_hass(FakeHass())

    vd = router.register_device(
        device_id="bulb-device",
        device_name="客厅灯",
        entity_list=[{"entity_id": "light.living", "domain": "light", "device_class": None}],
        model="yeelight.light",
    )

    assert vd is not None
    assert vd.product.pid == "002"
    assert set(vd.service_entries) == {"switch", "brightness", "cct"}
    assert router.skipped_unsupported == 0


def test_unsupported_products_are_skipped(registry):
    router = ServiceRouter()
    router.registry = registry
    router.matcher = ProductMatcher(registry)
    router.set_hass(FakeHass())

    vd = router.register_device(
        device_id="ac-device",
        device_name="客厅空调",
        entity_list=[{"entity_id": "climate.ac", "domain": "climate", "device_class": None}],
        model="xiaomi.aircondition.mt8",
        manufacturer="Xiaomi",
    )

    assert vd is None
    assert router.skipped_unsupported == 1
    assert router.sn_manager.get_device_count() == 0


def test_validate_config_passes():
    result = subprocess.run(
        [sys.executable, "validate_config.py"],
        cwd=COMPONENT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
