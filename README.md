# SeedPanel 分类做种状态面板

一个基于 PyQt6 的 qBittorrent 图形化管理工具，可以按分类查看做种状态，并通过种子注释自动加载对应的详情页面。

## ✨ 主要功能

- 📊 **分类管理**：按 qBittorrent 分类显示做种列表，支持快速筛选
- 🔗 **自动跳转**：根据种子注释中的 URL 或 ID，自动加载对应站点的详情页
- 🍪 **Cookie 支持**：为不同站点配置 Cookie，实现自动登录
- 🎨 **网页自适应**：支持根据窗口宽度自动缩放网页，隐藏横向滚动条
- ⌨️ **快捷键**：支持自定义快捷键快速导航和复制路径
- 💾 **持久化登录**：网页 Cookie 自动保存，下次启动无需重新登录

## 📋 系统要求

- Python 3.8+
- qBittorrent（需启用 WebUI）
- Windows / Linux / macOS

## 🚀 快速开始

### 安装依赖

```bash
pip install qbittorrent-api PyQt6 PyQt6-WebEngine
```

### 配置 qBittorrent

1. 打开 qBittorrent
2. 进入 **工具** → **选项** → **Web UI**
3. 启用 **Web 用户界面**
4. 设置端口（默认 8080）和用户名/密码
5. 保存设置

### 运行程序

**方式一：使用批处理文件（Windows）**
```bash
双击 run_qbLook.bat
```

**方式二：命令行运行**
```bash
python main.py
```

首次运行会自动创建 `config.json` 配置文件。

## ⚙️ 配置说明

### 基本配置

编辑 `config.json` 文件，配置 qBittorrent 连接信息：

```json
{
  "qbittorrent": {
    "host": "http://127.0.0.1",
    "port": 8080,
    "username": "admin",
    "password": "your_password",
    "verify_ssl": false
  }
}
```

### 网页模式配置

程序支持通过正则表达式匹配种子注释，自动生成详情页 URL。详细配置说明请参考 [WEB_MODES.md](WEB_MODES.md)。

**示例配置：**

```json
{
  "web_modes": [
    {
      "name": "KamePT",
      "pattern": "https?://kamept\\.com/details\\.php\\?id=\\d+",
      "template": "{value}",
      "description": "注释里直接放完整的KamePT详情链接",
      "cookie": "uid=xxx; passkey=yyy"
    },
    {
      "name": "M-Team",
      "pattern": "(?P<tid>\\d{3,})",
      "template": "https://kp.m-team.cc/detail/{tid}",
      "description": "注释里只填数字ID，自动拼接M-Team详情页",
      "cookie": ""
    }
  ]
}
```

### 界面设置

在程序内通过 **设置** → **界面** 可以配置：

- **未选择分类时不加载种子列表**：启用后需要先选择分类才会加载数据，提高启动速度
- **根据窗口宽度自动缩放网页**：自动调整网页缩放比例，隐藏横向滚动条
- **快捷键设置**：
  - `W` / `S`：树列表向上/向下导航
  - `D`：复制当前种子的保存文件路径

## 🎯 使用指南

### 基本操作

1. **连接 qBittorrent**：程序启动后会自动连接，状态栏显示连接状态
2. **选择分类**：在工具栏的"分类"下拉框中选择要查看的分类
3. **查看种子**：左侧树列表显示该分类下的所有做种任务
4. **查看详情**：点击种子后，中间面板会加载对应的详情页面
5. **查看信息**：右侧面板显示种子的分类、状态、保存路径等信息

### 高级功能

#### 添加新的网页模式

1. 打开 **设置** → **网页模式**
2. 点击 **新增**
3. 填写：
   - **名称**：模式名称（如站点名称）
   - **正则模式**：用于匹配注释的正则表达式
   - **URL模板**：生成的 URL 模板（可使用 `{value}` 或命名组）
   - **描述**：使用说明
   - **Cookie**：该站点的登录 Cookie（可选）

详细的正则表达式和模板语法请参考 [WEB_MODES.md](WEB_MODES.md)。

#### 配置 Cookie

1. 在浏览器中登录目标站点
2. 打开开发者工具（F12）
3. 进入 **Network** 标签
4. 刷新页面，找到任意请求
5. 在 **Request Headers** 中找到 `Cookie:` 行
6. 复制 Cookie 值（如：`uid=xxx; passkey=yyy`）
7. 粘贴到对应网页模式的 Cookie 字段

配置 Cookie 后，程序会自动保存登录状态，下次启动无需重新登录。

## 📁 项目结构

```
qbLook/
├── main.py              # 主程序文件
├── config.json          # 配置文件（自动生成）
├── config.example.json  # 配置示例文件
├── WEB_MODES.md        # 网页模式配置说明
├── README.md           # 本文件
├── run_qbLook.bat      # Windows 快速启动脚本
└── web_profile/        # 网页缓存和 Cookie 存储目录
```

## 🔧 常见问题

### Q: 无法连接到 qBittorrent？

**A:** 请检查：
1. qBittorrent 是否已启用 Web UI
2. 配置文件中的 host、port、用户名、密码是否正确
3. 防火墙是否阻止了连接

### Q: 网页模式无法匹配？

**A:** 请检查：
1. 种子注释中是否包含匹配的内容
2. 正则表达式是否正确（可在设置中测试）
3. URL 模板中的占位符是否正确

### Q: Cookie 配置后仍然需要登录？

**A:** 请确保：
1. Cookie 格式正确（`key=value; key2=value2`）
2. Cookie 未过期
3. 程序有写入 `web_profile` 目录的权限

### Q: 网页显示不正常？

**A:** 可以尝试：
1. 启用"根据窗口宽度自动缩放网页"选项
2. 清除 `web_profile` 目录重新加载
3. 检查站点是否需要特定的 User-Agent 或其他请求头

## 🛠️ 开发说明

### 依赖库

- `PyQt6`：GUI 框架
- `PyQt6-WebEngine`：网页渲染引擎
- `qbittorrent-api`：qBittorrent Web API 客户端

### 代码结构

- `QbClient`：qBittorrent API 客户端封装
- `WebMode`：网页模式匹配逻辑
- `MainWindow`：主窗口和 UI 逻辑
- `SettingsDialog`：设置对话框
- `InfoPanel`：右侧信息面板

## 📝 更新日志

### v1.0.0
- 初始版本
- 支持分类查看做种状态
- 支持网页模式自动匹配
- 支持 Cookie 自动登录
- 支持网页自适应缩放
- 支持快捷键自定义

## 🙏 致谢

- [qBittorrent](https://www.qbittorrent.org/) - 优秀的 BT 客户端
- [qbittorrent-api](https://github.com/rmartin16/qbittorrent-api) - Python API 客户端
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/) - 跨平台 GUI 框架

---

如有问题或建议，欢迎提交 Issue 或 Pull Request！

