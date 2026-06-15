# 贡献指南 · Contributing

欢迎给 DuckType（码字鸭）🦆 提 issue 和 PR！

## 开发环境

```bat
git clone <your-fork-url>
cd ducktype
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt pytest
python -m ducktype            :: 运行（Windows）
```

非 Windows 平台也能开发 / 跑测试——捕获引擎（钩子）只在 Windows 生效，但分析层、
存储层、仪表盘 API 都是平台无关的。

## 运行测试

```bash
pytest -q
```

捕获相关（`capture/`、`native/`）的改动请在 **Windows 实机**上验证一次：确认托盘出现、
打字后仪表盘「序列」里出现汉字。

## 代码风格

- 跟随现有风格：类型注解、模块顶部 docstring、相对导入。
- 原生钩子（`native/ducktype_hook.cpp`）保持极简、零副作用——它会被注入到其它进程，
  任何阻塞或崩溃都会影响用户的其它程序。
- 窗口类名 / 注册消息名在 C 与 Python 两侧必须一致
  （当前为 `DuckTypeHostWindowV2` / `DuckType_CommittedChar_V2`）。

## 提交

- 小而聚焦的提交；信息能说明“为什么”。
- 用户可见的改动请在 `CHANGELOG.md` 的 `[Unreleased]` 记一笔。
