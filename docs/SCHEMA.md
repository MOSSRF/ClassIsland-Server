# ClassIsland 2.1.0.1 数据契约（实测·权威）

> 来源：用户提供的 `ClassIsland_app_linux_x64_selfContained_folder` 官方构建
> 方法：IL / .NET 元数据反射（dnfile）直读 `ClassIsland.Shared.dll`
> 核实时间：2026-08-27
> 程序集版本：ClassIsland.dll / Core.dll / Shared.dll 均为 **2.1.0.1**（net8.0，自包含）

这份文件取代 PLAN.md 中所有"推断"和"待实测"的 schema 结论。

---

## 1. 🔴 时间格式分叉 —— 已定论

PLAN.md 头号阻塞风险的答案：

| 属性 | 特性标记 | 是否序列化 | 结论 |
|------|----------|-----------|------|
| `StartSecond` | `[Obsolete("请使用 StartTime 属性。")]` | **是** | 需写出 |
| `EndSecond` | `[Obsolete("请使用 EndTime 属性。")]` | **是** | 需写出 |
| `StartTime` | 无 | 是 | **权威字段** |
| `EndTime` | 无 | 是 | **权威字段** |
| `Last` | `[JsonIgnore]` | 否 | 不要写 |
| `BreakNameText` | `[JsonIgnore]` | 否 | 不要写 |

### 关键发现：四个字段是**彼此独立的后备字段**，不是别名

IL 反编译证实：

```
get_StartSecond -> ldfld _startSecond     // 独立字段
get_StartTime   -> ldfld _startTime       // 独立字段
```

字段表实际包含：`_startSecond, _endSecond, _startTime, _endTime`（4 个独立 backing field）。

**没有任何 setter 做 StartSecond ↔ StartTime 的互相同步。**

⇒ PLAN.md 中"1.x 格式喂给 2.x 会全部塌成 00:00:00"的推论 **成立且已被证实**。
⇒ 而 `[Obsolete]` **没有配 `[JsonIgnore]`**，所以 STJ 仍会序列化它们。

### 生成器策略（最终）

**必须以 `StartTime` / `EndTime` 为准，`TimeSpan` 序列化成 `"07:20:00"`。**

`StartSecond` / `EndSecond` 处理二选一：
- **推荐**：完全省略。反序列化时 `_startSecond` 落为默认值，而客户端逻辑只读 `StartTime`，无影响。
- 或：同时写出 ISO 日期形式做向下兼容（仅在需要兼容 1.x 客户端时）。

⚠️ 绝对不能只写 `StartSecond`。

### 附带发现：分割线的 EndTime 会被强制覆写

`set_StartTime` 的 IL 含一个隐藏副作用：

```
if (TimeType == 2)      // 2 = 分割线
    EndTime = StartTime;
```

⇒ 生成分割线时间点时，`EndTime` 写什么都会被客户端改成等于 `StartTime`。生成器直接令两者相等，避免 diff 噪音。

---

## 1.5 TimeSpan 的线格式（已确认）

序列化器确认是 **System.Text.Json**（`ClassIsland.dll` / `Shared.dll` / `Core.dll` 中
只出现 `System.Text.Json.JsonSerializer.Serialize/Deserialize` 的 MemberRef，
虽然包里也带了 `Newtonsoft.Json.dll`，但档案读写没用它）。

`System.Text.Json.dll` 内含 `Serialization.Converters.TimeSpanConverter`，
且 ClassIsland 自己**没有**任何 TimeSpan 的自定义 converter
（自定义的只有 `ActionSetStatusJsonConverter` / `GuidEmptyFallbackConverter` /
`ColorHexJsonConverter`）。

⇒ `TimeSpan` 走 STJ 内置格式：**`"07:20:00"`**（`[-][d.]hh:mm:ss[.fffffff]`）

### 属性精确类型（签名 blob 解析得出）

```
StartSecond   string          ← 1.x 时代塞 ISO 日期字符串，所以是 string
EndSecond     string
StartTime     System.TimeSpan ← 权威
EndTime       System.TimeSpan ← 权威
TimeType      int
DefaultClassId  System.Guid
BreakName     string
ActionSet     ClassIsland.Shared.Models.Automation.ActionSet
```

## 1.6 ⚠️ static 属性不要写进 JSON

IL 方法标志显示以下成员是 **static**，STJ 不序列化静态成员，写了纯属噪音：

| 成员 | 说明 |
|------|------|
| `ClassInfo.Empty` | static，**不要写** |
| `ClassPlanGroup.DefaultGroupGuid` | static 常量 |
| `ClassPlanGroup.GlobalGroupGuid` | static 常量 |

⇒ `ClassInfo` 实际只写 4 个键：`SubjectId` / `IsChangedClass` / `IsEnabled` / `AttachedObjects`
⇒ `ClassPlanGroup` 实际只写 2 个键：`Name` / `IsGlobal`

## 1.7 课表群默认 GUID（2.1 新增，填错会导致课表不显示）

`ClassPlanGroup..cctor` 的 `ldstr` + `Guid.Empty`：

```
DefaultGroupGuid = ACAF4EF0-E261-4262-B941-34EA93CB4369
GlobalGroupGuid  = 00000000-0000-0000-0000-000000000000  (Guid.Empty)
```

`Profile..ctor` 的 IL 显示客户端会自动补齐两个组并选中默认组：

```
SelectedClassPlanGroupId = DefaultGroupGuid
ClassPlanGroups[DefaultGroupGuid] = { Name: "默认",       IsGlobal: false }
ClassPlanGroups[GlobalGroupGuid]  = { Name: "全局课表群", IsGlobal: true  }
TempClassPlanGroupType   = 1
```

`ClassPlan..ctor` 中 `AssociatedGroup = DefaultGroupGuid`，`IsEnabled = true`。

⇒ 生成器必须显式写出这些，否则十几个班的课表可能挂在错误的组上而不显示。

---

## 2. CoreVersion —— 已定论

`IAppHost..cctor` 的 IL：

```
ldc.i4.2  ldc.i4.0  ldc.i4.0  ldc.i4.0
newobj Version::.ctor
stsfld CoreVersion
```

⇒ **`CoreVersion = "2.0.0.0"`**（注意：与程序集版本 2.1.0.1 不同，不要混用）

manifest 里就写 `"CoreVersion": "2.0.0.0"`。PLAN.md 的假设正确。

---

## 3. 完整属性清单（实测，含 JsonIgnore 标注）

标 ❌ 的是 `[JsonIgnore]`，生成器**不得输出**。

### Profile
```
Name, TimeLayouts, ClassPlans, Subjects,
IsOverlayClassPlanEnabled, OverlayClassPlanId,
TempClassPlanId, TempClassPlanSetupTime,
ClassPlanGroups, SelectedClassPlanGroupId,
TempClassPlanGroupId, TempClassPlanGroupExpireTime,
IsTempClassPlanGroupEnabled, TempClassPlanGroupType,
Id, OrderedSchedules
❌ EditingSubjects, HasOverlayClassPlan
```

⚠️ **PLAN.md 的 Profile 结构已过时**。2.1 新增了一整套「课表群」机制：
`ClassPlanGroups` / `SelectedClassPlanGroupId` / `OrderedSchedules` / `Id`。
拆分文件的 Profile 骨架需要带上这些键（可为空/默认值）。

### TimeLayout
```
Name, Layouts, IsOverlay, OverlaySourceId
❌ IsActivated, IsActivatedManually
```

### TimeLayoutItem
```
StartTime, EndTime, TimeType, IsHideDefault,
DefaultClassId, BreakName, ActionSet
(StartSecond / EndSecond = Obsolete，见 §1)
❌ Last, BreakNameText
```
TimeType: `0`=上课 `1`=课间 `2`=分割线 `3`=行动

新增字段（PLAN.md 未记录）：`BreakName`（自定义课间名）、`ActionSet`（行动组）

### ClassPlan
```
TimeLayoutId, TimeRule, Classes, Name,
IsOverlay, OverlaySourceId, OverlaySetupTime,
IsEnabled, AssociatedGroup
❌ ValidTimeLayoutItems, LastTimeLayoutCount, ClassPlans,
   TimeLayouts, TimeLayout, IsActivated
```
新增：`AssociatedGroup`（关联课表群）

### ClassInfo
```
SubjectId, IsChangedClass, IsEnabled, Empty
❌ Index, CurrentTimeLayout, CurrentTimeLayoutItem, IsEmpty
```

### Subject
```
Name, Initial, TeacherName, IsOutDoor
```

### TimeRule
```
WeekDay, WeekCountDiv, WeekCountDivTotal
```
⇒ `WeekCountDivTotal` **确认存在**（1.x gist 样例没有，2.x 必须带）

### AttachedObjects（继承而来）
`TimeLayout` / `TimeLayoutItem` / `ClassPlan` / `ClassInfo` / `Subject`
全部继承 `AttachableSettingsObject`，该基类提供唯一属性 **`AttachedObjects`**。

⇒ 这 5 类对象都要有 `AttachedObjects` 键，空写 `{}`。
⇒ `Profile` 和 `TimeRule` 继承 `ObservableRecipient`，**没有** `AttachedObjects`。

（注：`IsActive` 出现在 1.x gist 样例里，但 2.1 元数据中已无此属性 —— 属残留字段，可不写。）

---

## 4. ManagementManifest（实测确认，与 PLAN.md 一致）
```
ClassPlanSource, TimeLayoutSource, SubjectsSource,
DefaultSettingsSource, PolicySource, ComponentsSource,
CredentialSource, ServerKind, OrganizationName, CoreVersion
```
每个 `*Source` 均为 `ReVersionString { Value, Version }`

### 4.1 🔴🔴 Version 是唯一的更新闸门（IL 定论，最易踩的坑）

`ReVersionString::IsNewerAndNotNull` 的 IL（25 字节，完整反汇编）：

```
ldarg.0 | call get_Value | call String::IsNullOrWhiteSpace | brtrue.s ->23
ldarg.0 | call get_Version | ldarg.1 | cgt | ret
ldc.i4.0 | ret
```

等价于：

```csharp
return !string.IsNullOrWhiteSpace(Value) && this.Version > localVersion;
//                                                        ^^^ cgt = 严格大于
```

`<MergeManagementProfileAsync>d__27` / `<LoadManagementConfig>d__25` 中，
ClassPlan / TimeLayout / Subjects / Components 每一项都先过 `IsNewerAndNotNull`，
通过后才 `get_Value` 去下载，下载完再 `set_XxxVersion` 写回本地 `Versions.json`。

**⇒ 推论（务必遵守）**

1. **只改文件内容、不涨 `Version` ⇒ 客户端永不更新。** 无声失败，最危险。
2. 比较是 `>` 而非 `!=` ⇒ **Version 不可回退**。一旦误发大数（如 999），
   之后所有小于它的版本全部失效，只能继续往上加。
3. `Value` 为空/空白 ⇒ 该项直接跳过，同样静默。
4. 本地版本存在 `Management/Versions.json`（`ManagementVersions` 七个字段：
   `ClassPlanVersion` / `TimeLayoutVersion` / `SubjectsVersion` /
   `DefaultSettingsVersion` / `PolicyVersion` / `CredentialVersion` / `ComponentsVersion`）。
   调试时删掉此文件即可强制全量重拉。

> ⚠️ 实践中常见的坑：手工维护的集控仓库里，四个 source 的 `Version`
> 往往**一直是 1 从未变动** —— 于是改了 json 客户端根本不会拉新内容，
> 而且不报错。这正是本工具必须自动管理版本号的原因。

### 4.2 URL 模板占位符（IL 实测）

`ServerlessConnection::DecorateUrl` 的 IL 含两次 `String::Replace`：

| 占位符 | 替换来源 | Serverless | gRPC server |
|---|---|---|---|
| `{id}` | `ManagementSettings.ClassIdentity` | ✅ | ✅ |
| `{cuid}` | 客户端唯一 ID | ✅ | ✅ |
| `{host}` | 服务器地址 | ❌ | ✅ 仅 `ManagementServerConnection` |

⇒ **`{id}` 可直接写在 `ClassPlanSource.Value` 里**，客户端按自身 `ClassIdentity` 替换。
一份 manifest 即可服务全部班级，无需每班一个 manifest 文件。

### 4.3 Gitee raw 缓存 TTL（实测）

```
Cache-Control: public, max-age=60
Via: 1.1 varnish     X-Cache: MISS → HIT     Age: 0,3,4… 递增
```

⇒ TTL 仅 **60 秒**，且 `Age` 会递增（确实过 CDN）。
配合 4.1 的 Version 闸门，**无需再用版本化文件名绕缓存** —— 等 60 秒即可。
保持 URL 稳定 + 只涨 `Version` 是更简单也更贴合官方设计的做法。

## 5. ManagementPolicy —— 比 PLAN.md 多一个字段
```
DisableProfileClassPlanEditing, DisableProfileTimeLayoutEditing,
DisableProfileSubjectsEditing,  DisableProfileEditing,
DisableSettingsEditing,         DisableSplashCustomize,
DisableDebugMenu,               AllowExitManagement,
DisableEasterEggs   ← 新增，PLAN.md 的 8 字段表已过时（实为 9 个）
```

## 6. ManagementSettings（客户端侧接入配置）
```
IsManagementEnabled, ManagementServerKind, ManagementServer,
ManagementServerGrpc, ManifestUrlTemplate, ClassIdentity
```
⇒ 比 PLAN.md §2.5 的 `ManagementPreset.json` 多了 `ManagementServerGrpc` 和 `IsManagementEnabled`。

---

## 7. 白送的 21 个官方科目 GUID

`Assets/default-subjects.json` 已确认存在，本身就是一个完整 Profile 骨架
（`{"Name":"","TimeLayouts":{},"ClassPlans":{},"Subjects":{...}}`）——
**这正好是官方拆分文件格式的活样本**，直接拿它当 `subjects.json` 模板。

节选：
```
97d0bf3f-137f-4f8a-87d6-ff387063bbd3  语文
1154d452-5ede-4194-b4dd-cb40c956c8ed  数学
3bbed0c0-bcf3-4dfe-a78d-5da9b02cf8bd  英语
44ec22d3-3dde-40e6-8eb7-a1f4cd378a04  历史
```
完整文件已留存：见下方"产物位置"。

---

## 8. 复现方法

本机无 dotnet SDK，用 Python 直读 CLI 元数据即可，无需编译：

```bash
python3 -m venv /tmp/cienv && /tmp/cienv/bin/pip install dnfile
# 读 TypeDef/PropertyMap/CustomAttribute 表拿属性+特性
# 读 MethodDef.Rva + IL 字节流拿 backing field 与常量
```

关键：`ClassIsland.Shared.xml`（XML 文档注释）随包发布，含全部成员语义说明，是极好的交叉验证源。

## 产物位置
- 解包目录：`/tmp/ci_app/app-2.1.0.1-0/`（临时，重启即失）
- 官方科目表：`/tmp/ci_app/app-2.1.0.1-0/Assets/default-subjects.json`
- 原始 zip：`~/.openclaw/media/inbound/ClassIsland_app_linux_x64_selfContained_folder---9b4e872f-….zip`

⚠️ `/tmp` 会被清理。若要长期保留，需把 `default-subjects.json` 和 `ClassIsland.Shared.xml` 复制进项目目录。
