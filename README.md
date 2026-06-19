# 🦆 DuckType · 码字鸭

[![CI](https://github.com/x1324x5/Duck-Type/actions/workflows/build.yml/badge.svg)](https://github.com/x1324x5/Duck-Type/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/x1324x5/Duck-Type?include_prereleases)](https://github.com/x1324x5/Duck-Type/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows-blue)

一只安静蹲在后台的小鸭子：统计你通过输入法**上屏**的汉字，记录字频、词频、
输入序列，并提供一个本地桌面仪表盘查看各种统计。**只记录汉字**，拼音/英文/数字不入库。

> Windows only（依赖 Win32 输入法消息钩子）。后台运行，不打断正常使用。

## ✨ 功能

- **字频 / 词频**：高频字、jieba 分词后的高频词。
- **输入序列浏览与导出**：按时间线查看上屏汉字（即"打过的词语序列"），可按应用筛选、跳转日期、复制单段内容，并一键导出 TXT / CSV / JSON。
- **词性统计**：基于 jieba 词性标注的名/动/形/… 分布，可下钻查看某类词性下的高频词。
- **主题关键词**：用 TF-IDF 关键词提取概括一段时间内输入的主题。
- **输入效率**：平均速度、60 秒峰值速度（字/分）、活跃时长、会话数。
- **删改频率**：退格 / Delete 次数与"修改率"（估计真实删除汉字数 ÷ 上屏字数）。
- **分应用统计**：哪个程序里打字最多。
- **活跃热力图**：星期 × 小时 的输入热力分布；每日趋势。
- **自定义时间范围**：今天 / 近 7 天 / 近 30 天 / 全部，或任意起止日期（周报、月报都是范围特例）。
- **趋势环比**：关键指标与上一等长周期对比，卡片上直接显示升/降幅度。
- **目标 · 连续打卡 · 成就**：每日目标进度环、连续码字 streak、字数 / 生僻字 / 单字重复 / 趣味彩蛋等成就徽章墙。
- **报告行为洞察**：今日 / 周 / 月 / 年报告会总结高产时段、主力应用、修改习惯、产出变化、表达覆盖面与月/年节奏。
- **趣味榜单**：最爱词、成语/长词、生僻字、只打过一次的字。
- **设置页**：在仪表盘里按分组直接改黑名单、目标、阈值、自启、窗口启动方式等，免手动改 JSON。
- **数据管理与隐私**：一键清空、按日期区间删除、保留期限自动清理。
- **托盘常驻**：暂停/继续、开机自启、打开仪表盘、打开数据文件夹、退出。关闭仪表盘窗口会收起到托盘，不会停止后台统计。
- **隐私保护**：自动跳过 Win32 密码框；可配置程序黑名单（默认含常见密码管理器）。

## 🧠 工作原理（重点）

普通的全局键盘钩子只能拿到你按下的**拼音字母**，拿不到输入法最终**上屏的汉字**。
要拿到上屏汉字，需要把一段代码注入到每个程序里去观察文本提交——本项目通过一个被系统
注入到其它进程的 `WH_GETMESSAGE` 钩子 DLL 来做到，并用**两条通路**覆盖新老程序：

- **TSF 通路（现代程序）**：搜狗、微软拼音等在 Win10/11 的现代程序（微信、VS Code、
  Win11 记事本、浏览器、Office…）里通过 **TSF 文本服务框架**提交中文，**不发 `WM_CHAR`**。
  DLL 在每个进程里挂上 `ITfThreadMgrEventSink` / `ITfTextEditSink`，监听 TSF 的文本提交。
- **WM_CHAR 通路（经典程序）**：传统 Win32 输入框（如 Win+R 运行框）仍走 `WM_CHAR /
  WM_IME_CHAR`。DLL 在 TSF 未接管该线程时回退到这条通路，且两路互斥，**不会重复计数**。

因此本项目由两部分组成：

1. `native/ducktype_hook.cpp` —— 注入式钩子 DLL（C++/COM），把每个上屏字符通过一个系统
   范围的注册消息 `PostMessage` 回主程序（只传一个标量，不做跨进程指针传递）。
2. Python 主程序 —— 创建隐藏窗口接收字符、用纯 ctypes 的低级键盘钩子统计退格/删除、
   写入 SQLite、跑分词与统计、通过 pywebview/WebView2 打开本地桌面仪表盘、托盘常驻。

打包版会先把 bundled 钩子 DLL 复制到数据根目录下的 `native\ducktype_hook_<hash>.dll`
再注入，避免 PyInstaller one-file 的随机 `_MEI...` 临时目录被长驻程序里的 pinned DLL 占住，
也避免反复重启后在同一目标程序中累积多个钩子副本。

> **位数限制**：64 位的钩子只能捕获 64 位程序里的输入（绝大多数现代程序都是 64 位）。
> 要覆盖 32 位老程序需要再跑一个 32 位宿主，属于进阶用法。

## 🚀 快速开始

### 方式 A：下载发行版（推荐）

在 GitHub 的 Releases 下载 `DuckType.exe`（由 CI 自动编译 DLL + 生成图标并打包）。双击运行，
托盘出现🦆小鸭图标即在统计。右键托盘可打开仪表盘 / 数据文件夹 / 开机自启 / 暂停 / 退出。
打包版首次启动会要求选择一个数据文件夹。

### 方式 B：从源码运行

```bat
pip install -r requirements.txt
native\build_dll.bat        :: 需要 MinGW-w64 (gcc) 或 MSVC (cl.exe)
python -m ducktype
```

没有编译器也能运行——只是在装好 DLL 之前不会记录上屏汉字（退格/速度等仍可用）。
源码运行默认使用系统数据目录；如需指定数据根目录，可设置 `DUCKTYPE_DATA_DIR`。

### 方式 C：自己打包 exe

```bat
build.bat                   :: 编译 DLL + 生成图标 + 安装依赖 + PyInstaller 打包
:: 产物：dist\DuckType.exe
```

## 💻 命令行

```bat
python -m ducktype                 :: 后台运行（托盘 + 仪表盘）
python -m ducktype --report        :: 打印文本摘要
python -m ducktype --export out\   :: 导出字频/词频/序列
python -m ducktype --clear         :: 清空全部已记录数据
python -m ducktype --report --range 7d
```

默认运行方式是托盘 + pywebview 原生窗口，不启动 HTTP 服务，也不占用端口。
如需用普通浏览器调试前端，可运行 `_preview_server.py`，它会启动开发用 Flask shim。

## 🗂️ 数据与配置

DuckType 使用一个“数据根目录”保存运行期文件。打包版首次启动会让你选择目录；
源码/CLI 默认回退到 `%APPDATA%\DuckType\`，也可用 `DUCKTYPE_DATA_DIR` 覆盖。
固定留在 `%APPDATA%\DuckType\location.json` 的只有数据根目录指针。

- `ducktype.db` —— SQLite 数据库（字符序列、按键事件、词频缓存）。
- `config.json` —— 配置（也可在仪表盘「设置」页里改）。
- `ducktype.log` —— 运行日志。

数据全部留在本机。打包版仪表盘通过进程内 pywebview bridge 与 Python 后端通信，不上传任何内容。

## ⚙️ 配置项（config.json）

| 键 | 说明 | 默认 |
|---|---|---|
| `paused` | 是否暂停统计 | false |
| `exclude_password_fields` | 跳过 Win32 密码输入框 | true |
| `blacklist_apps` | 不统计的程序（进程名，小写） | 常见密码管理器 |
| `run_gap_seconds` | 超过该间隔或切换程序即视为新的一段输入（影响分词） | 3.0 |
| `session_gap_seconds` | 超过该空闲秒数视为新的输入会话（影响效率统计） | 60.0 |
| `retention_days` | 自动删除早于该天数的数据；0 = 永久保留 | 0 |
| `daily_goal` | 每日字数目标（用于目标环 / streak） | 500 |
| `dashboard_host` / `dashboard_port` | 旧浏览器预览服务配置；打包版原生窗口不使用端口 | 127.0.0.1 / 8765 |
| `open_dashboard_on_start` | 启动即显示原生仪表盘窗口；关闭窗口会收起到托盘 | true |
| `autostart` | 开机自启（写 HKCU Run） | false |

## 🎨 图标

图标优先使用 `assets/duck.png`，`tools/make_icon.py` 会据此生成仪表盘/托盘用的
`src/ducktype/assets/duck.png` 和打包用的多尺寸 `duck.ico`。
想换成自己的鸭子图：替换 `assets/duck.png`，再跑一次 `python tools/make_icon.py` 即可。

## 🧪 测试

```bash
pip install pytest
pytest -q
```

分析层、存储层、配置层的测试与平台无关，CI 在 Ubuntu + Windows 上都会跑。

## ⚠️ 已知限制

- 仅 Windows；目标进程需与钩子位数一致（默认 64 位）。
- 浏览器 / Electron 应用内的密码框不是 Win32 控件，无法被密码框检测识别——请用程序黑名单。
- 极个别既不走 TSF、又用完全自绘文本框的程序，可能仍会漏记。
- 安全软件可能拦截全局钩子注入；若仪表盘横幅提示"未捕获"，看健康横幅与日志排查。

## 📄 许可证

MIT，见 [LICENSE](LICENSE)。这是一个本地统计工具，请仅用于统计**你自己**的输入。
