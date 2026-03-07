"""地点与坐标 — 解析 locations.md，计算距离和旅行时间。"""
import math
import re
from typing import Optional

from .utils import read_file, load_config, LOCATIONS_PATH, log


class SubLocation:
    """一个子地点。"""

    def __init__(self, name: str, description: str, is_default: bool = False):
        self.name = name
        self.description = description
        self.is_default = is_default

    def __repr__(self):
        return f"SubLocation({self.name}, default={self.is_default})"


class Location:
    """一个地点。"""

    def __init__(self, name: str, x: float, y: float, description: str,
                 sub_locations: list[SubLocation] | None = None):
        self.name = name
        self.x = x
        self.y = y
        self.description = description
        self.sub_locations: list[SubLocation] = sub_locations or []

    def distance_to(self, other: "Location") -> float:
        """欧几里得距离。"""
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def travel_minutes_to(self, other: "Location", speed: float = 1.0) -> int:
        """旅行时间（分钟）。

        坐标单位 = 1 公里。步行速度默认 walking_speed_kmh km/h。
        speed = 1.0 表示步行，>1 表示更快（如骑车、开车）。
        """
        config = load_config()
        walking_speed = config["world"].get("walking_speed_kmh", 5)
        dist_km = self.distance_to(other)
        return max(1, round(dist_km * 60 / (walking_speed * speed)))

    def __repr__(self):
        return f"Location({self.name}, ({self.x}, {self.y}))"


class LocationManager:
    """地点管理器，从 locations.md 加载所有地点。"""

    def __init__(self):
        self._locations: dict[str, Location] = {}
        self._load()

    def _load(self):
        """从 locations.json 加载地点。"""
        from .utils import read_json
        data = read_json(LOCATIONS_PATH) if LOCATIONS_PATH.exists() else []
        if not data:
            log("warning", f"locations.json 为空或不存在: {LOCATIONS_PATH}")
            return

        for loc in data:
            name = loc.get("name", "")
            if not name:
                continue
            # 解析子地点
            sub_locs = []
            for sl in loc.get("sub_locations", []):
                sl_name = sl.get("name", "")
                if sl_name:
                    sub_locs.append(SubLocation(
                        sl_name,
                        sl.get("description", ""),
                        sl.get("is_default", False),
                    ))
            self._locations[name] = Location(
                name,
                float(loc.get("x", 0)),
                float(loc.get("y", 0)),
                loc.get("description", ""),
                sub_locs,
            )

        log("info", f"加载了 {len(self._locations)} 个地点: {list(self._locations.keys())}")

    def get(self, name: str) -> Optional[Location]:
        """获取地点。"""
        return self._locations.get(name)

    def all_locations(self) -> dict[str, Location]:
        """获取所有地点。"""
        return dict(self._locations)

    def travel_time(self, from_name: str, to_name: str, speed: float = 1.0) -> Optional[int]:
        """计算两点间旅行时间（分钟）。"""
        loc_from = self.get(from_name)
        loc_to = self.get(to_name)
        if not loc_from or not loc_to:
            return None
        return loc_from.travel_minutes_to(loc_to, speed)
    def reload(self):
        """重新加载地点数据（Dashboard 编辑后调用）。"""
        self._locations.clear()
        self._load()


# 全局单例
_manager: Optional[LocationManager] = None


def get_location_manager() -> LocationManager:
    """获取全局 LocationManager 实例。"""
    global _manager
    if _manager is None:
        _manager = LocationManager()
    return _manager


def reload_location_manager():
    """重新加载地点数据（单例保留，数据刷新）。"""
    global _manager
    if _manager is not None:
        _manager.reload()
    else:
        _manager = LocationManager()


def get_known_locations(character: str) -> list[str]:
    """获取角色的已知地点列表。

    如果 state.json 中没有 known_locations 字段，回退为所有地点（向后兼容）。
    """
    from .utils import load_state
    state = load_state()
    char_state = state.get("characters", {}).get(character, {})
    known = char_state.get("known_locations")
    if known is not None:
        return list(known)
    # 回退：返回所有地点
    mgr = get_location_manager()
    return list(mgr.all_locations().keys())


def discover_location(character: str, location: str):
    """将新地点加入角色的已知地点列表（如果尚未知道）。"""
    from .utils import load_state, save_state
    state = load_state()
    char_state = state.get("characters", {}).get(character)
    if char_state is None:
        return
    known = char_state.get("known_locations")
    if known is None:
        # 尚未初始化 known_locations，跳过（等初始化后再说）
        return
    if location not in known:
        known.append(location)
        save_state(state)
        log("info", f"地点发现 [{character}]: {location}")


def get_sub_locations(location_name: str) -> list[SubLocation]:
    """获取某地点的所有子地点。"""
    mgr = get_location_manager()
    loc = mgr.get(location_name)
    if loc is None:
        return []
    return list(loc.sub_locations)


def get_default_sub_location(location_name: str) -> str:
    """获取某地点的默认子地点名。无子地点或无默认则返回空字符串。"""
    subs = get_sub_locations(location_name)
    if not subs:
        return ""
    for s in subs:
        if s.is_default:
            return s.name
    # 没有标记 is_default 就取第一个
    return subs[0].name


def get_known_sub_locations(character: str, location_name: str) -> list[str]:
    """获取角色在某地点的已知子地点列表。

    默认子地点始终包含在结果中。
    如果 state 中没有 known_sub_locations，回退为所有子地点（兼容）。
    """
    from .utils import load_state
    state = load_state()
    char_state = state.get("characters", {}).get(character, {})

    # 获取该地点的所有子地点
    all_subs = get_sub_locations(location_name)
    if not all_subs:
        return []
    all_sub_names = [s.name for s in all_subs]
    default_sub = get_default_sub_location(location_name)

    known_map = char_state.get("known_sub_locations")
    if known_map is None:
        # 回退：返回所有子地点
        return all_sub_names

    known_list = known_map.get(location_name, [])
    # 合并默认子地点
    result = list(known_list)
    if default_sub and default_sub not in result:
        result.insert(0, default_sub)
    # 过滤掉实际不存在的
    return [s for s in result if s in all_sub_names]


def discover_sub_location(character: str, location_name: str, sub_location: str):
    """将新子地点加入角色的已知子地点列表。

    默认子地点自动已知，无需调用此函数。
    """
    if not sub_location:
        return
    # 默认子地点不需要记录
    default_sub = get_default_sub_location(location_name)
    if sub_location == default_sub:
        return

    from .utils import load_state, save_state
    state = load_state()
    char_state = state.get("characters", {}).get(character)
    if char_state is None:
        return
    known_map = char_state.get("known_sub_locations")
    if known_map is None:
        # 尚未初始化，跳过
        return
    known_list = known_map.setdefault(location_name, [])
    if sub_location not in known_list:
        known_list.append(sub_location)
        save_state(state)
        log("info", f"子地点发现 [{character}]: {location_name}/{sub_location}")
