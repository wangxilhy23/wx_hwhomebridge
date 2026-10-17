"""C 侧能力表 —— 与预编译 libhilink_bridge.so 的设备类型表保持同步。

背景
----
本集成的业务逻辑在 Python，真正把子设备注册到华为云的是预编译的
``libhilink_bridge.so``。C 侧维护一份固定长度的设备类型表（``map_prodIds``），
``OnTypeCheckCB`` 返回的索引就是该表的下标；索引不存在时 C 侧会跳过该设备。

因此：

* Python 侧可以新增品类定义、适配器和控制/上报逻辑；
* 但只有 C 侧同时具备对应的 SvcInfo / 设备类型，并且华为侧分配了 prodId，
  设备才会真正出现在华为智慧生活 / 小艺中。

当前映射
--------
index 0 -> "001" 床头灯
index 1 -> "002" 灯泡
index 2 -> "003" 小夜灯
index 3 -> "004" 压力传感器
index 4 -> "005" 电水壶
index 5 -> "006" 灭蚊器
index 6 -> "007" 电饭煲
index 7 -> "008" 断路器

``009`` / ``00A`` / ``00B``（空调 / 窗帘 / 风扇）是 Python 侧占位 PID：
Python 侧映射已经就绪，但当前预编译 .so 没有对应设备类型，设备会被安全跳过。
C 侧就绪后，只需把新的 PID -> index 加进 ``PID_INDEX_MAP`` 并替换 .so，
无需改动控制与上报代码。
"""

# 与 C 侧 map_prodIds 数组严格一一对应，顺序不能变。
PID_INDEX_MAP = {
    "001": 0,
    "002": 1,
    "003": 2,
    "004": 3,
    "005": 4,
    "006": 5,
    "007": 6,
    "008": 7,
}

# 仅 Python 侧实现、等待 C 侧支持的 PID。
# 保留该集合用于文档、测试与启动日志，不参与能力判断。
PYTHON_ONLY_PIDS = frozenset({"009", "00A", "00B"})


def get_pid_index(pid: str) -> int:
    """返回 PID 在 C 侧设备类型表中的索引，未知 PID 返回 -1。"""
    return PID_INDEX_MAP.get(str(pid), -1)


def is_c_supported(pid: str) -> bool:
    """判断 PID 是否已被当前预编译 C 库支持。"""
    return str(pid) in PID_INDEX_MAP
