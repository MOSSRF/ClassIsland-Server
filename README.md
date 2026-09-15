# ClassIsland Server

给 [ClassIsland](https://github.com/ClassIsland/ClassIsland) 集控（管理服务器）用的**可视化课表管理界面 + 静态配置生成器**。

写一份 YAML，或者直接在网页上点几下，就能生成整套集控静态文件并一键发布到 Gitee / GitHub。不需要服务器，不需要数据库，不需要 Node。

![界面预览](docs/screenshot.png)

作息表编辑（1.1.0 新增）：

![作息表编辑](docs/screenshot-timelayout.png)

---

## 它解决什么问题

ClassIsland 的集控支持「无服务端模式」（`ServerKind: 0`）——把几个 JSON 静态文件丢到任意能公开访问的地方（Gitee raw、GitHub raw、对象存储、自建 nginx），客户端就能拉到统一下发的课表。

但手工维护这些 JSON 很难受：

- 十几个班就是十几份 `classplans.json`，每个课程都是一串 GUID
- 作息、科目是全校共用的，改一处要保证所有班级的引用都还有效
- **最坑的一点**：客户端只有在 `manifest.json` 里的 `Version` 整数**变大**时才会重新下载。改了内容忘了改 Version，客户端永远拉不到新配置，而且不报错

这个项目把上面这些都自动化了，并且在发布前做安全检查。

## 特性

- **每班独立作息与科目（1.2.0 新增）**：作息、科目也按班下发，可以给每个班单独配任课老师
- **网页可视化编辑**：表格式课表（行=节次并显示真实时间，列=星期），下拉选科目；集控策略做成开关面板
- **档案编辑全搬进网页**：作息表（增删节次/课间/分割线）、自定义科目、客户端默认设置、组织名、发布地址都能在界面上改，不用手写 YAML
- **改作息自动同步课表**：增删「上课」节次时，用此作息的所有班级课表跟着补空位/删格子，不会卡在「节数不一致」的死胡同
- **一键装机预设**：直接下载 `ManagementPreset.json`，放进客户端程序目录即自动接入集控
- **单文件 HTTP 服务**：只用 Python 标准库 + PyYAML，不需要 npm / 前端构建
- **多周轮换**：`mon@1` / `mon@2` 语法，支持任意 N 周轮换；网页上切到「第 N 周」直接改某天即可自动拆分，列头可一键合并回「每周相同」
- **按天切换作息**：周五提前放学、周日晚自习都能单独指定作息
- **Version 自动管理**：内容哈希驱动自增，杜绝「改了没生效」
- **发布安全闸门**：发布前检查悬空引用、覆盖影响、Version 单调性，不通过就拒绝发布
- **不破坏既有配置**：能把线上已有的人工配置反向导入成 YAML，让生成结果成为**超集**而不是覆盖
- **只改动过的段**：保存时只重写你真正编辑过的 YAML 段，其余字节不动；`guid:` 钉桩与其警示注释、条目上方的手写说明都会原样保留
- **确定性输出**：同样输入永远生成同样字节，git diff 干净
- **预留班级**：先占住 id，以后再排课，不会生成空课表把大屏刷白

## 快速开始

需要 Python 3.9+（唯一第三方依赖是 PyYAML）。下载发布包解压后：

- **Windows**：双击 `start-webui.bat`
- **Linux / macOS**：`./start-webui.sh`

脚本会在缺依赖时自动 `pip install -r requirements.txt`，默认端口 8848
（也可带参数：`./start-webui.sh 8000`）。

然后浏览器打开 `http://<本机IP>:8848`。

第一次打开时：

1. 程序发现还没有 `schedule.yaml`，会自动用内置示例**播种一份**
   （能直接构建通过，绝不在这一步报错挡住你）；
2. 点页面右上角的 **「首次使用向导」**，粘贴你自己新建的**公开**仓库地址，
   向导会自动 clone、识别默认分支、算出客户端访问的 raw 发布地址并写回；
3. 在各页签里把示例改成自己学校的课表，点「保存」；
4. 点「发布到线上」即可。

> 手动方式（不用向导）：
> `cp examples/schedule.example.yaml schedule.yaml`、
> `cp config.example.json config.json`，再 `python3 tools/webui.py`。

页面上方六个页签：

| 页签 | 能改什么 |
|---|---|
| 课表 | 新增/删除班级、按班级排课（行=节次并标真实时间）、班级名、默认作息、单日换作息、任意周数轮换 |
| 作息 | 新建/改名/删除作息表，增删节次与课间、调时间、插分割线 |
| 科目 | 登记官方 21 科之外的自定义科目（简称/教师/户外） |
| 默认设置 | 下发给客户端的默认设置（`DefaultSettingsSource`） |
| 集控策略 | 9 个开关，锁住客户端本机的修改权限 |
| 基本信息 | 档案名、组织名、发布地址，以及下载装机预设 |

改完点「保存」—— 保存前会先跑一次完整构建校验，不通过就拒绝写入，
并且**只重写你真正改过的 YAML 段**，其余字节一个不动。

只想用命令行也行：

```bash
python3 src/split.py            # 生成集控文件树到 dist/
python3 tools/preflight.py --dist dist --live ./live-repo   # 发布前安全检查
```

## 配置

`config.json`（从 `config.example.json` 复制，不进 git）：

| 键 | 说明 |
|---|---|
| `live_repo` | 目标仓库的本地克隆路径，发布时写入这里再 push |
| `backup_dir` | 备份目录。每次保存 YAML、每次覆盖线上文件前都会自动备份。**置为空字串即关闭备份**（真正的历史在目标仓库的 git 里，备份只是本地保险） |
| `git_exec_path` | 一般留空。仅当你的 git 的 `--exec-path` 指向错误目录时才需要（某些 NAS 套件版 git 有这个打包问题，会导致 https 传输不可用） |
| `git_branch` | 推送分支，默认 `master`。用网页向导连接仓库时会自动探测远端默认分支并写回，一般无需手填 |
| `git_user_name` | 自动提交时的 commit 作者名，默认 `classisland-server` |
| `git_user_email` | 自动提交时的 commit 邮箱，默认 `classisland-server@localhost` |

也可以用环境变量覆盖：`CISRV_LIVE_REPO`、`CISRV_BACKUP_DIR`、
`CISRV_GIT_BRANCH`、`CISRV_GIT_USER_NAME`、`CISRV_GIT_USER_EMAIL` 等。

### 按班配任课老师（1.2.0）

科目的老师名写在**班级内部**的 `subjects:` 里，只对本班生效：

```yaml
classes:
  - id: "101"
    name: 高一1班
    timelayout: 标准作息
    subjects:                      # 只影响本班
      数学: { teacher: 张老师 }
      语文: { teacher: 李老师 }
    schedule:
      mon: [语文, 数学, ...]
```

可用字段：`teacher` / `initial` / `outdoor`。写错字段名会直接报错，
不会静默忽略（否则老师名没生效你也不知道）。

顶层的 `subjects:` 仍然存在，但用途不同 —— 它是**声明官方 21 科之外的新科目**；
班级内的 `subjects:` 是**覆盖已有科目在本班的属性**。

## 部署到 Gitee / GitHub

1. 新建一个**公开**仓库存放**生成出来的配置**（跟本项目分开放）。
   ⚠️ 必须公开：客户端不带任何凭据去拉这些 JSON，私有仓库大屏会拉不到。
2. 在网页「首次使用向导」里粘贴该仓库地址，向导会自动 clone 到本地的
   `live-repo/` 并识别默认分支；也可以手动克隆后把路径填进
   `config.json` 的 `live_repo`。
3. 向导会自动算出并写回 `publish.base_url`（也可在「基本信息」页手改）：
   - Gitee：`https://gitee.com/<用户>/<仓库>/raw/master`
   - GitHub：`https://raw.githubusercontent.com/<用户>/<仓库>/main`
4. 网页上点「发布到线上」，或手动 `git push`。

**为什么用 raw 而不是 Pages**：Gitee 免费版 Pages 每次更新都要手动点一次「部署」，没法自动化。raw 地址推送后即时生效（CDN 缓存约 60 秒）。

客户端那边在「设置 → 集控」里填 `manifest.json` 的完整地址，班级标识填你在 YAML 里定的 `id`。

生成出来的仓库长这样（1.2.0 起作息与科目也按班）：

```
manifest.json          集控入口（单份，URL 里的 {id} 由客户端自己替换）
policy.json            集控策略（全校统一）
101/classplans.json    课表
101/timelayouts.json   作息（只含该班用到的）
101/subjects.json      科目（含本班任课老师）
102/...
```

每个班目录都是**自包含**的：课表里引用的作息与科目，一定在同目录下能找到。
发布前的安全检查会逐班验证这一点。

### 批量装机：ManagementPreset.json

不想给每台大屏手填服务器地址，就在网页「基本信息」页点**下载 ManagementPreset.json**，
放到客户端的**程序目录**下，ClassIsland 启动时会自动加载集控配置。
文件里的 `ClassIdentity` 留空 —— 每台大屏装好后填自己的班级 id。

## 客户端怎么拿到更新

`manifest.json` 里每个数据源都有一个 `Version` 整数。客户端的判定逻辑是：

```
本地没有该配置 或 远端 Version > 本地 Version  →  下载
```

**严格大于**。所以：

- 改了内容必须让 `Version` 变大 —— 本项目用内容哈希自动处理
- `Version` **不能回退**，写小了客户端就再也不更新了
- 因此不需要「版本化文件名」那类绕缓存技巧，URL 保持稳定即可

> ⚠️ **Version 是全局的，不是按班的。**
> 客户端本地只存 `ClassPlanVersion` / `TimeLayoutVersion` / `SubjectsVersion`
> 这几个**标量**。所以哪怕文件已按班拆开，只改一个班也会抬高全局
> Version，导致**所有班**重新拉取自己那份。不会出错，但请求量会
> 随班级数放大（详见 `docs/SCHEMA.md` §4.2.2）。

## 目录结构

```
start-webui.sh / .bat  一键启动（缺依赖自动安装）
requirements.txt       唯一第三方依赖（PyYAML）
src/
  appconfig.py    环境配置（路径、git 参数、提交身份）
  repourl.py      仓库地址推导（粘贴 URL → clone/raw 地址，纯函数）
  ci_schema.py    ClassIsland 数据结构常量与构造器
  build.py        YAML → 完整 Default.json（所有校验都在这里）
  split.py        YAML → 集控静态文件树 + Version 管理
  yaml_edit.py    按段改写 YAML（保留注释与紧凑写法）
tools/
  webui.py        网页服务端（含首次使用向导接口）
  webui.html      网页前端（单文件，无构建）
  preflight.py    发布前安全检查
  prune_ids.py    清理已删班级在线上的残留目录
  import_live.py  把线上既有配置反向导入成 YAML
  build_release.py        打包干净的独立发布压缩包
  build_windows_bundle.py 打包「开箱即入控」的 Windows 客户端整合包
docs/
  SCHEMA.md       实测得出的数据格式说明
reference/
  default-subjects.json   ClassIsland 自带的 21 个官方科目
```

## 设计原则

几条被实践反复验证过的原则，改代码时请遵守：

**校验只写一处。** 所有校验都在 `build.py`，`split.py` 和 Web 界面都调用它，绝不各自实现一遍 —— 两条路径迟早会行为不一致。

**宁可报错，不要静默出错。** 科目名打错一个字，早期版本会静默造一个新科目下发全校；现在会直接报错并提示最接近的正确名字。同理，班级没排课又没标 `reserved` 会报错，而不是生成空课表把大屏刷成空白。

**GUID 必须确定性且可钉住。** 平时用 `uuid5` 从名称派生，保证输出稳定。但线上已有的历史 GUID 派生不出来，所以 YAML 支持 `guid:` 显式钉住 —— 改了它，所有引用该作息的班级课表会全部悬空。

**只写不删。** `split.py` 从不删除线上文件。线上可能有别人手工加的目录，生成器擅自删除等于替用户做决定。删除是独立工具 `prune_ids.py`，默认 dry-run。

**先备份再覆盖。** 每次保存 YAML、每次覆盖线上文件前都自动备份到 `backup_dir`。

## 自己打包发布物

发布给别人用的压缩包只含程序与示例，绝不夹带本机的 `config.json`、真实
`schedule.yaml`、`dist*/`、`live-repo/`、`backups/` 等：

```bash
python3 tools/build_release.py                 # 产物到 release/classisland-server-<版本>.zip
python3 tools/build_release.py --format tar.gz # tar.gz 格式
```

打包前会做脱敏检查：通用的本机绝对路径模式直接拦；个人/私有词表（姓名、私有仓库名等）通过环境变量 `CISRV_PRIVATE_TERMS`（逗号分隔）提供，命中即拒绝出包。刻意不把任何人的私有词硬编码进开源仓库。

另有面向**客户端装机**的 Windows 整合包脚本（把官方客户端与入控预设打在一起）：
`tools/build_windows_bundle.py`，见其文件头说明。

## 数据格式

`docs/SCHEMA.md` 记录了实测得出的数据契约（含 `StartTime` 与已废弃的 `StartSecond` 的关系、分割线的零长度语义、`WeekCountDiv` 轮换语义等）。这些是通过读取程序集元数据 + 与客户端自己写出的配置文件逐字段比对得出的，不是猜的。

## 兼容性

- 针对 ClassIsland **2.1.x**（`CoreVersion 2.0.0.0`）开发验证
- 已在真实客户端上验证：加载生成的配置后文件字节未被改写，界面渲染与预期逐字一致

## 许可

[GPL-3.0](LICENSE)

`reference/default-subjects.json` 中的官方科目定义提取自 ClassIsland（GPL-3.0），因此本项目同样采用 GPL-3.0。

## 致谢

感谢 [ClassIsland](https://github.com/ClassIsland/ClassIsland) 及其作者。本项目是第三方配套工具，与上游官方无隶属关系。
