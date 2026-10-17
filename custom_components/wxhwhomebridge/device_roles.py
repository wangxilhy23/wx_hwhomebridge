"""HA 设备 → 华为子设备角色拆分规则。

当前预编译 C 库没有风扇设备类型，风扇灯只能复用灯类产品（PID 002）
的 switch/brightness 服务。为了让同一台 HA 设备既能桥接灯、又能桥接风扇，
这里把设备拆成多个 VirtualDevice，各自使用独立 SN。
"""

# 需要拆分的实体 domain 组合
KEY_LIGHT = "light"
KEY_FAN = "fan"


def build_sub_device_roles(entity_list: list) -> list:
    """根据 HA 设备实体决定是否拆成多个华为子设备

    规则：
    - 同时有 light + fan：拆成 light / fan 两个角色
    - 只有 fan：尝试 fan 角色（纯风扇设备；产品匹配不到时正常跳过）
    - 其他：不拆分

    Returns:
        [{"name": role, "domains": set, "suffix": str}, ...]
        空列表表示不拆分。
    """
    domains = {entity.get("domain") for entity in entity_list}

    if KEY_LIGHT in domains and KEY_FAN in domains:
        return [
            {"name": "light", "domains": {KEY_LIGHT}, "suffix": ""},
            {"name": "fan", "domains": {KEY_FAN}, "suffix": " 风扇"},
        ]

    if KEY_FAN in domains:
        return [{"name": "fan", "domains": {KEY_FAN}, "suffix": " 风扇"}]

    return []


def role_display_name(name: str, role: str) -> str:
    """生成子设备在华为侧显示的名称"""
    base = name or "未命名设备"
    if role == "fan":
        return f"{base} 风扇"
    return base
