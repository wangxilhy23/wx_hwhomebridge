"""测试环境准备。

直接把组件目录注册为顶层包 ``hwhomebridge``，避免导入
``custom_components.hwhomebridge.__init__`` 时引入 Home Assistant。
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hwhomebridge"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hwhomebridge" not in sys.modules:
    package = types.ModuleType("hwhomebridge")
    package.__path__ = [str(COMPONENT)]
    package.__package__ = "hwhomebridge"
    sys.modules["hwhomebridge"] = package
