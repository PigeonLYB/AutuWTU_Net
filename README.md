# AutoWTU_Net 校园网自动重连助手

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/Python-3.8+-blue)

AutoWTU_Net 是一个面向校园网场景的轻量自动登录/重连工具。支持电信、联通、移动及校园网网关，支持托盘常驻与断网自动重连。

⚠️ **首次使用必读**
**第一次使用时，需要先在你的设备浏览器上登录校园网（完成运营商账号绑定）**，绑定成功后再使用本工具，才能正常自动登录！

## Release 下载
- 发布页：https://github.com/PigeonLYB/AutoWTU_Net/releases
- 可执行文件：在 Release Assets 中下载 `AutoWTU.exe`

## 版本更新
- `v1.0.0` (2026-04-09)
- 新增 Windows 开机自启配置（设置页复选框 + 托盘开关）
- 启动时自动同步配置与注册表状态

## 功能特点
- 自动检测网络状态，离线时自动触发登录。
- 适配 ePortal 的常见跳转场景（包括 JS 强转页面）。
- 系统托盘常驻，支持手动测试登录与设置面板。
- 防多开机制，避免重复启动冲突。

## 项目结构
- `AutoWTU_Net.py`：主程序脚本。
- `icon.ico`：托盘图标资源。
- `title.png`：配置窗口顶部图片资源。
- `requirements.txt`：源码运行依赖。
- `LICENSE`：开源许可证（MIT）。

## 本地运行
1. 安装 Python 3.8 及以上版本。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 运行脚本：

```bash
python AutoWTU_Net.py
```

## 打包 EXE（可选）
如需打包，请先安装 pyinstaller，然后执行：

```bash
pyinstaller --noconsole --onefile --add-data "icon.ico;." --add-data "title.png;." --icon="icon.ico" AutoWTU_Net.py
```

## 上传到 GitHub
仓库已包含脚本与图片资源，推荐使用以下流程：

```bash
git add AutoWTU_Net.py icon.ico title.png README.md requirements.txt LICENSE .gitignore
git commit -m "chore: prepare project for github"
git push origin main
```

## 免责声明
本工具仅供学习交流使用，请勿用于违反校园网管理规定的行为。
