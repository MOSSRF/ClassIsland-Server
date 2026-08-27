#!/usr/bin/env python3
"""
ClassIsland 2.1.0.1 数据契约常量与骨架构造器。

所有结论来自 IL/元数据实测，见 ../SCHEMA.md。
不要凭记忆改这里的常量。
"""
import json
import uuid
from pathlib import Path

# ── 实测常量（IL 反射得出） ──────────────────────────────────────────
CORE_VERSION = "2.0.0.0"          # IAppHost..cctor: ldc.i4.2,0,0,0
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

# ClassPlanGroup..cctor 中的 ldstr
DEFAULT_GROUP_GUID = "acaf4ef0-e261-4262-b941-34ea93cb4369"
GLOBAL_GROUP_GUID = EMPTY_GUID     # ClassPlanGroup.GlobalGroupGuid = Guid.Empty

# TimeType 语义
TT_CLASS = 0      # 上课
TT_BREAK = 1      # 课间
TT_DIVIDER = 2    # 分割线
TT_ACTION = 3     # 行动

# TimeRule.WeekDay: 0=周日 .. 6=周六
WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
WEEKDAY_CN = {0: "周日", 1: "周一", 2: "周二", 3: "周三",
              4: "周四", 5: "周五", 6: "周六"}

# DateTime 默认值：用 MinValue 保证确定性（客户端 ctor 用 DateTime.Now，
# 但这些字段在 TempClassPlanId=null 时不参与逻辑）
DT_MIN = "0001-01-01T00:00:00"

# 确定性 GUID 命名空间
NS = uuid.uuid5(uuid.NAMESPACE_DNS, "classisland.pipeline")

# 所有模型都继承 CommunityToolkit.Mvvm 的 ObservableRecipient，
# 它提供一个会被序列化的实例属性 IsActive（Messenger 带 [JsonIgnore] 所以不写）。
# 客户端实写的 Default.json 里每个对象都有它。
IS_ACTIVE = False

REF_DIR = Path(__file__).resolve().parent.parent / "reference"


def sid(kind: str, key: str) -> str:
    """确定性 GUID：同输入永远同输出 → git 无噪音 diff。"""
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


def load_official_subjects() -> dict:
    """读官方 default-subjects.json，返回 {科目名: guid}。

    复用官方 GUID 可让学生机上不出现重复科目。
    """
    return {v["Name"]: k for k, v in _official_raw()["Subjects"].items()}


_OFFICIAL_CACHE: dict | None = None


def _official_raw() -> dict:
    global _OFFICIAL_CACHE
    if _OFFICIAL_CACHE is None:
        p = REF_DIR / "default-subjects.json"
        _OFFICIAL_CACHE = json.loads(p.read_text(encoding="utf-8"))
    return _OFFICIAL_CACHE


def official_subject_record(guid: str) -> dict | None:
    """返回官方科目的**原始记录**（浅拷）。

    ⚠️ 必须用官方值，不能按名字重新推导。里面有两类推不出来的信息：
      * `Initial` 不一定是首字：通用技术→「技」、周测→「测」
      * `IsOutDoor`：体育 / 信息技术 为 true
    这两项已由客户端实写的 Default.json 交叉验证。
    """
    rec = _official_raw()["Subjects"].get(guid)
    return dict(rec) if rec else None


# ── 骨架构造器 ──────────────────────────────────────────────────────
# 注意：以下字段清单是「STJ 实际会序列化的实例属性」。
# 已排除 [JsonIgnore] 与 static 成员（ClassInfo.Empty /
# ClassPlanGroup.DefaultGroupGuid / GlobalGroupGuid 都是 static，不序列化）。

def empty_profile(profile_id: str, name: str = "") -> dict:
    return {
        "Name": name,
        "TimeLayouts": {},
        "ClassPlans": {},
        "Subjects": {},
        "IsOverlayClassPlanEnabled": False,
        "OverlayClassPlanId": None,
        "TempClassPlanId": None,
        "TempClassPlanSetupTime": DT_MIN,
        "ClassPlanGroups": {
            DEFAULT_GROUP_GUID: {"Name": "默认", "IsGlobal": False,
                                 "IsActive": IS_ACTIVE},
            GLOBAL_GROUP_GUID: {"Name": "全局课表群", "IsGlobal": True,
                                "IsActive": IS_ACTIVE},
        },
        "SelectedClassPlanGroupId": DEFAULT_GROUP_GUID,
        "TempClassPlanGroupId": None,
        "TempClassPlanGroupExpireTime": DT_MIN,
        "IsTempClassPlanGroupEnabled": False,
        "TempClassPlanGroupType": 1,
        "Id": profile_id,
        "OrderedSchedules": {},
        "IsActive": IS_ACTIVE,
    }


def hhmm_to_timespan(s: str) -> str:
    """'7:20' -> '07:20:00'  (System.TimeSpan 的 STJ 线格式)"""
    s = str(s).strip()
    parts = s.split(":")
    if len(parts) == 2:
        h, m, sec = int(parts[0]), int(parts[1]), 0
    elif len(parts) == 3:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError(f"时间格式无法解析: {s!r}（应为 HH:MM 或 HH:MM:SS）")
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        raise ValueError(f"时间超出范围: {s!r}")
    return f"{h:02d}:{m:02d}:{sec:02d}"


def time_layout_item(start: str, end: str, time_type: int,
                     break_name: str = "") -> dict:
    """构造一个时间点。

    ⚠️ 只写 StartTime/EndTime（TimeSpan）。不写 StartSecond/EndSecond —
    它们是独立 backing field 且已 [Obsolete]，写了只会造成误导。
    ⚠️ 分割线(TimeType=2)：客户端 set_StartTime 的 IL 里有
       `if (TimeType==2) EndTime = StartTime;`，所以强制两者相等。
    """
    st = hhmm_to_timespan(start)
    et = st if time_type == TT_DIVIDER else hhmm_to_timespan(end)
    return {
        "StartTime": st,
        "EndTime": et,
        "TimeType": time_type,
        "IsHideDefault": False,
        "DefaultClassId": EMPTY_GUID,
        "BreakName": break_name or "",
        "ActionSet": None,
        "AttachedObjects": {},
        "IsActive": IS_ACTIVE,
    }


def time_layout(name: str, items: list) -> dict:
    return {
        "Name": name,
        "Layouts": items,
        "IsOverlay": False,
        "OverlaySourceId": None,
        "AttachedObjects": {},
        "IsActive": IS_ACTIVE,
    }


def class_info(subject_id: str) -> dict:
    return {
        "SubjectId": subject_id,
        "IsChangedClass": False,
        "IsEnabled": True,
        "AttachedObjects": {},
        "IsActive": IS_ACTIVE,
    }


def time_rule(week_day: int, week_div: int = 0, week_div_total: int = 2) -> dict:
    return {
        "WeekDay": week_day,
        "WeekCountDiv": week_div,
        "WeekCountDivTotal": week_div_total,
        "IsActive": IS_ACTIVE,
    }


def class_plan(name: str, time_layout_id: str, rule: dict,
               classes: list) -> dict:
    return {
        "TimeLayoutId": time_layout_id,
        "TimeRule": rule,
        "Classes": classes,
        "Name": name,
        "IsOverlay": False,
        "OverlaySourceId": None,
        "OverlaySetupTime": DT_MIN,
        "IsEnabled": True,
        "AssociatedGroup": DEFAULT_GROUP_GUID,
        "AttachedObjects": {},
        "IsActive": IS_ACTIVE,
    }


def subject(name: str, initial: str = "", teacher: str = "",
            outdoor: bool = False, guid: str | None = None) -> dict:
    """构造科目。

    ⚠️ 若 guid 命中官方 21 科，**以官方记录为基底**，不按名字推导。
    因为 Initial 不一定是首字（通用技术→技、周测→测），
    IsOutDoor 也推不出来（体育 / 信息技术 为 true）。
    这三处差异是被客户端实写的 Default.json 里抓出来的。
    """
    base = official_subject_record(guid) if guid else None
    if base is None:
        base = {
            "Name": name,
            "Initial": name[0],
            "TeacherName": "",
            "IsOutDoor": False,
            "AttachedObjects": {},
            "IsActive": IS_ACTIVE,
        }
    # 仅当 yaml 里显式给了值才覆盖官方默认
    if initial:
        base["Initial"] = initial
    if teacher:
        base["TeacherName"] = teacher
    if outdoor:
        base["IsOutDoor"] = True
    base["Name"] = name
    base.setdefault("AttachedObjects", {})
    base.setdefault("IsActive", IS_ACTIVE)
    return base


def re_version(url: str | None, version: int) -> dict:
    """ReVersionString。更新判定：Value 非空 且 Version > 本地版本。"""
    return {"Value": url, "Version": version}


def dump_json(obj, path: Path) -> str:
    """确定性写盘，返回内容字符串。"""
    txt = json.dumps(obj, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt, encoding="utf-8")
    return txt
