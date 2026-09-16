# vendor/ — 随包内置的第三方依赖

本目录存放发布包**内置**的第三方库，使最终用户无需联网、无需 `pip install`
即可运行（开箱即用）。

## yaml/

- 项目：[PyYAML](https://pyyaml.org/) 6.0.2
- 许可证：MIT（见 `PyYAML-LICENSE`，与本项目 GPL-3.0 兼容）
- 形态：**纯 Python** 版本。已剔除平台相关的 C 扩展
  （`_yaml*.so` / `_yaml*.pyd`），因此同一份代码在
  Windows / macOS / Linux 上均可运行。
- C 扩展（libyaml）只是可选加速；本项目不使用任何 `CSafeLoader`/`CDumper`
  等 C 接口，纯 Python 版功能完全一致。

加载方式见 `src/vendor_bootstrap.py`：若运行环境已自行安装 PyYAML
（可能带 C 加速），优先使用系统版；否则回退到本目录的内置版本。

## 更新方法（维护者）

```bash
# 下载官方 wheel（任意平台 wheel 内的 yaml/*.py 都相同）
python -m pip download --no-deps --only-binary=:all: \
    --platform manylinux2014_x86_64 --python-version 311 \
    --implementation cp "PyYAML==<版本>" -d /tmp/yv
# 解压其中的 yaml/（仅 *.py，删除所有 .so/.pyd 与 __pycache__）覆盖到此处，
# 并同步更新 dist-info 内的 LICENSE 与本文件标注的版本号。
```
