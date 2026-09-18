# 功能缺口盘点

对比官方 ClassIsland 客户端（2.1.0.1）集控体系与自研 classisland-server（v1.2.0）。

**源数据：**
- 官方源码：`github.com/ClassIsland/ClassIsland`（master 分支）
- 本地二进制：`app-2.1.0.1-0` 下的 XML 文档注释与程序集
- 自研代码：`src/build.py` / `src/split.py` / `src/yaml_edit.py` / `tools/webui.html` / `tools/webui.py`

---

## 1. 档案类型缺口

### 1.1 ComponentsSource / ComponentsVersion（缺失 · P1）

**现状：** 自研 manifest 没有 `ComponentsSource`，客户端跳过组件集控。

**官方行为：** 客户端读取 `manifest["ComponentsSource"].Value` 指向 `components.json`，
JSON 体内有一个字段 `ComponentConfigName`（字符串），对应客户端设置的
`Settings.CurrentComponentConfig`。客户端比对本地组件配置文件名与服务器的值：
不同 → 替换组件布局文件并重启。

**建议：** 在 manifest 加入 ComponentsSource + 提供 `components.json`。
WebUI 增加页签管理组件配置——对大多数学校来说只是从 WebUI
或默认配置文件里选一个名字推下去。

---

### 1.2 CredentialSource / CredentialVersion（缺失 · P0）

**现状：** 自研 manifest 没有 `CredentialSource`，客户端使用空凭据：
管理员无密码、授权等级全为最低（任意操作都允许）。

**官方行为：** 客户端读取 `manifest["CredentialSource"].Value` 指向 `credential.json`，
结构为：

```jsonc
{
  "AdminCredential": {
    "Method": "",     // "password" | "pin" | "pattern" | "" 等
    "Credential": ""  // 哈希值
  },
  "UserCredential": { /* 同上 */ },
  "ChangeLessonsAuthorizeLevel": 0,              // 0~3
  "EditAuthorizeSettingsAuthorizeLevel": 0,      // 0~3
  "EditPolicyAuthorizeLevel": 0,
  "EditProfileAuthorizeLevel": 0,
  "EditSettingsAuthorizeLevel": 0,
  "ExitApplicationAuthorizeLevel": 0,
  "ExitManagementAuthorizeLevel": 0
}
```

授权等级：
- 0 = Everyone（所有人）
- 1 = User（需要用户密码/模式）
- 2 = Admin（需要管理员密码/模式）
- 3 = Never（不允许任何人操作）

**建议：** WebUI 新增「凭据/密码」页签。管理员可设置管理员密码和用户密码，
并为 7 个操作各自选择授权等级。文件写入 `dist/credential.json`。

---

## 2. 领域模型字段缺口

### 2.1 Subject（科目）—— 缺两个字段

| 字段 | 官方 | 自研 | 缺口 |
|------|------|------|------|
| `TeacherName` | ✅ string | ❌ | P0。显示任课老师，WebUI 列头可显示 |
| `IsOutDoor` | ✅ bool | ❌ | P1。户外课特殊显示 |
| `Name` / `Initial` | ✅ | ✅ | 一致 |

### 2.2 ClassInfo（课程条目）—— 缺两个字段

| 字段 | 官方 | 自研 | 缺口 |
|------|------|------|------|
| `IsChangedClass` | ✅ bool | ❌ | P0。标记为换课（临时调整） |
| `IsEnabled` | ✅ bool | ❌ | P2。单节课停用（前后课程吸拢） |

`IsEnabled=false` 还会联动 `ClassPlan.ValidTimeLayoutItems`：
前后连续停课时，显示区域会吸拢（正/反双向搜索空区域）。

### 2.3 ClassPlan（课表）—— 缺临时课表体系

| 字段 | 官方 | 自研 | 缺口 |
|------|------|------|------|
| `IsOverlay` | ✅ bool | ❌ | P0。是否为临时层课表 |
| `OverlaySourceId` | ✅ Guid? | ❌ | P0。对应原始课表 |
| `OverlaySetupTime` | ✅ DateTime | ❌ | P1。设置时间 |
| `AssociatedGroup` | ✅ Guid | ❌ | P2。课表群归属 |

官方临时课表机制：
1. 客户端日常使用 `IsOverlay=false` 的常规课表
2. 老师或集控推送「明天换个课」→ 创建新的 `ClassPlan`，
   设置 `IsOverlay=true`、`OverlaySourceId` 指向原课表、
   `OverlaySetupTime=现在`，挂到同一天的 `TimeRule` 上
3. 客户端发现同一天有 overlay 和常规课表 → **只使用 overlay**

自研实现等价课表拆分即可提供此能力，不需要复杂的数据结构变更。

### 2.4 TimeLayoutItem（时间点）—— 缺行动点

| 字段 | 官方 | 自研 | 缺口 |
|------|------|------|------|
| `TimeType=3` + `ActionSet` | ✅ | ❌ | P2。倒计时/秒表等行动组件 |
| `IsHideDefault` | ✅ bool | ❌ | P2。是否默认隐藏该时间点 |
| `BreakName` | ✅ string | ❌ | P2。自定义课间名 |

自研的 yaml_edit.py 其实在 YAML 层面用 dict 形式存 `type`, `start`, `end`，
ActionSet 不需要改 YAML 格式，但 WebUI 不提供管理入口。

### 2.5 TimeLayoutItem.TimeType 枚举值（时间类型）

官方 4 种 vs 自研映射：

| 值 | 含义 | 自研 |
|----|------|------|
| 0 | 上课 | ✅ |
| 1 | 课间 | ✅ |
| 2 | 分割线 | ✅（`type: divider`） |
| 3 | 行动 | ❌（`type: action` 可解析但无编辑入口） |

### 2.6 ClassPlanGroup（课表群）—— 完全未实现

官方课表以群组织（默认群、全局群等），自研未使用。
不影响当前功能（所有课表都在默认群），但如果需要跨年级/跨类型
课表隔离，这是基础设施。P3。

### 2.7 Subject 中的 `GetFirstName()` 裁姓逻辑

纯客户端 UI 功能，服务端不关心。无缺口。

---

## 3. 默认设置缺口（Settings 属性对照）

自研 dist/Default.json 是 `SettingsOverlays` 格式，下发时覆盖顶层设置。
以下字段在官方 SettingsModel 中有，自研不下发，可能缺的是**默认值差异**：

### 3.1 高价值（学校教学场景会用到的）

| Settings 属性 | 类型 | 说明 | 评级 |
|--------------|------|------|------|
| `ClassPrepareNotifySeconds` | int | 课前提醒秒数（默认 60） | P0 |
| `IsClassPrepareNotificationEnabled` | bool | 是否开启课前提醒 | P0 |
| `IsClassChangingNotificationEnabled` | bool | 课间切换提醒 | P0 |
| `IsClassOffNotificationEnabled` | bool | 下课通知 | P0 |
| `ShowDate` | bool | 显示日期 | P1 |
| `HideOnClass` | bool | 上课时隐藏主窗口 | P1 |
| `HideMode` | int | 隐藏模式（0=不隐藏 1=左边 2=顶边…） | P1 |
| `HideOnFullscreen` / `HideOnMaxWindow` | bool | 全屏/最大化时隐藏 | P2 |
| `IsSplashEnabled` | bool | 启屏/开屏画面 | P2 |
| `SplashCustomText`, `SplashCustomLogoSource` | string | 启屏自定义文本/logo | P2 |
| `Theme` | int | 主题（0=浅色,1=深色,2=跟随系统） | P2 |
| `IsNotificationEnabled` | bool | 总通知开关 | P2 |
| `IsSpeechEnabled` | bool | 语音播报 | P2 |
| `SpeechVolume` | double | 语音音量 | P2 |
| `SelectedSpeechProvider` | string | 语音提供方 | P2 |
| `IsExactTimeEnabled` | bool | 精准对时 | P2 |
| `ExactTimeServer` | string | NTP 服务器 | P2 |
| `IsMouseInFadingEnabled` | bool | 鼠标淡出 | P2 |
| `AnimationLevel` | int | 动画级别（0=关闭） | P3 |

### 3.2 高价值 · 课表控件外观

这些属于 `LessonControlAttachedSettings`（附加设置，按时间点/课表级别配置）：

| 设置 | 类型 | 说明 | 评级 |
|------|------|------|------|
| `ShowExtraInfoOnTimePoint` | bool | 显示额外信息 | P1 |
| `ExtraInfoType` | int | 0=倒计时 1=剩余百分比 | P1 |
| `IsCountdownEnabled` | bool | 显示倒计时 | P1 |
| `CountdownSeconds` | int | 倒计时秒数（如 60 秒开始倒计时） | P1 |
| `ScheduleSpacing` | double | 课表间距缩放（1=正常） | P2 |
| `ShowCurrentLessonOnlyOnClass` | bool | 仅显示当前课 | P2 |

注意：这些是**课表级附加设置**，以 `AttachedObjects` 的方式
附着在 `ClassPlan`/`TimeLayoutItem` 上。自研的 `AttachedObjects` 字段
虽然存在但无编辑入口。

### 3.3 低价值（与屏幕显示无关、系统级或个人操作偏好）

| 属性 | 理由 |
|------|------|
| `WindowDocking*`, `WindowLayer`, `UseRawInput` | 大屏固定位置，跨屏由硬件决定 |
| `Debug*` | 非教学场景，或在集控策略禁止 |
| `Diagnostic*` | 客户端内部诊断，与默认设置无关 |
| `UpdateMode`, `SelectedChannel`, `LastCheckUpdateTime` | 大屏不主动更新，由 IT 统一升级 |
| `PluginIndexes`, `IsPluginsAutoUpdateEnabled` | 没有安装插件的大屏不需要 |
| `NotificationProviders*` | 通知方式偏个人偏好 |
| `LastWeatherInfo`, `CityId`, `CityName` | 天气信息，非默认设置范畴 |
| `IsNetworkConnect`, `IsSystemSpeechSystemExist` | 运行时状态，非设置 |

---

## 4. WebUI 功能缺口

### 4.1 课表编辑（P0）

| 功能 | 官方桌面编辑 | 自研 WebUI | 缺口 |
|------|-------------|-----------|------|
| 教师字段 | ✅ 科目显示任课老师 | ❌ | P0 |
| 新增/删除班级 | ✅ | ✅ v1.2.0 已加 | ✅ |
| 多周轮换 | ✅ | ✅ v1.2.0 已加 | ✅ |
| 临时课表/换课 | ✅ 桌面点选 | ❌ | P0。WebUI 应在某天上提供「另存为临时课表」按钮 |
| 科目 IsOutDoor | ✅ 标签显示 | ❌ | P1 |
| 单节停用 | ✅ 复选框 | ❌ | P2 |
| 分割线编辑 | ✅ | ❌ | P2 |
| 行动点编辑 | ✅ | ❌ | P2 |

### 4.2 默认设置编辑（P0）

当前 WebUI「默认设置」页签支持分发 settings overlays，但缺口是：

- 缺少高价值字段（课前提醒、课间通知等）的编辑入口
- 当前只提供了一个自由 JSON 输入框，不够直观
- 建议：转为结构化表单 + JSON 回退模式

### 4.3 组件设置编辑（P1）

无编辑界面。WebUI 应增加一个页签，列出可用组件配置名，
管理员选择要推的配置，或上传 `components.json` 文件。

### 4.4 凭据/密码编辑（P0）

WebUI 无密码管理界面。需要新增页签。
含：管理员密码设置、用户密码设置、7 个操作的授权等级下拉。

---

## 5. 教学场景缺口汇总

### P0（在 1.3.0 中做）

1. **凭据/密码集控** — 防止学生退出集控/修改设置。WebUI 新增页签。
2. **科目教师字段** — 在 WebUI 列头和科目页签显示/编辑任课老师。
3. **换课/临时课表** — WebUI 课表页签增加「创建临时课表」功能。
4. **默认设置补充** — WebUI 默认设置页签结构化课前/课间/下课通知、日期显示等字段。

### P1（在 1.4.0 中做）

5. **Components 组件集控** — 从 WebUI 选择组件配置名下发。
6. **户外课标记** — 科目加 `IsOutDoor` 标记。
7. **课表附加设置编辑** — 按时间点级别的倒计时/隐藏等。
8. **Settings 外观相关** — 启屏自定义、自动隐藏等。

### P2（在 1.5.0 中做）

9. **行动点编辑**。
10. **分割线编辑**。
11. **单节停用**。
12. **课表群管理**。
13. **默认设置主题/语音/通知** 深度参数。

---

## 6. 架构结论

**当前架构（Serverless 静态清单）是正确的，不应改为 gRPC。**

- 临时课表、换课都不需要实时通信——都是在下一拉取时生效
- 凭据是静态文件，没有服务端验证——客户端自己在本地验证密码
- Components 也是静态文件——推送配置名即可
- 唯一的实时性场景是「紧急通知」，但也可以设计为短 TTL（60s）的静态 JSON

**建议：先做 P0 四项，然后做 P1。顺序：**
1. Credential（密码保护是各学校需求最强烈的）
2. TeacherName（数据完整性问题）
3. 临时课表（日常教学刚需）
4. 默认设置 UI 完善（课前提醒等）

---

## 附录 A：已有能力（勘误与澄清）

以下能力已有缺口盘点中未明确提及，或已有部分支持，在此澄清：

### A.1 多周轮换

✅ 已支持。YAML 层通过 `mon@1` / `mon@2` 语法定义，
yaml_edit.py 的 `iter_day_classes()` 正确解析；
WebUI v1.2.0 的周轮换编辑和自动拆分/合并功能已通过 28/28 CDP 测试。

### A.2 新增/删除班级

✅ 已支持。WebUI v1.2.0 通过 `addClass()` / `delClass()` 实现，
先创建预留班（`reserved: true`），选好作息后转为正式班级。
删除操作仅修改 `schedule.yaml`；清理线上旧目录需手动运行
`prune_ids.py --apply`。

### A.3 Windows 集控整合包

✅ 已生成。脚本 `tools/build_windows_bundle.py` 下载官方
2.1.0.1 Windows x64 自包含包（126 MB），在 `data/` 下注入
`ManagementPreset.json`（预填 Gitee 集控地址），并在
`data/class-presets/` 下为每个班级生成已填好 ID 的预设。
装机流程：解压 → 运行 ClassIsland.exe → 加入管理 → 点确认即可入控。

---

## 附录 B：管理端流程总览

```
schedule.yaml (.git 私有配置)
  │
  ├── src/build.py           ← 校验 + 构建 dist/
  ├── src/split.py           ← 拆分到班级目录 + manifest
  ├── tools/webui.py/html    ← Web 编辑界面
  ├── tools/import_live.py   ← 反向导入线上配置
  ├── tools/prune_ids.py     ← 清理已删除班级的线上目录
  ├── tools/preflight.py     ← 发版前预检
  ├── tools/push-all.sh      ← 一条命令推到 Gitee + GitHub 并校验
  └── tools/build_windows_bundle.py  ← 生成 Windows 开箱包

dist/                        ← 构建产物，push 到你自己的集控配置仓库
  ├── manifest.json
  ├── policy.json
  ├── credential.json        （待实现）
  ├── components.json        （待实现）
  ├── Default.json
  ├── 班级ID/
  │   ├── classplans.json
  │   ├── timelayouts.json
  │   └── subjects.json
  └── ManagementPreset.json  （生成的，放在 dist 供下载）
```