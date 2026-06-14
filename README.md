# 融媒体数据看板 · 西北民族大学

> 基于飞书开放API的跨平台宣传数据聚合与实时可视化大屏

## 项目简介

为西北民族大学党委宣传部打造的融媒体数据看板，通过飞书开放API自动聚合抖音、快手、B站、微信视频号四平台宣传数据，将分散在多维表格中的任务管理与内容数据整合为实时可视化大屏，解决宣传数据碎片化、统计效率低的核心痛点。

## 功能特性

- **数据总览** — KPI指标卡、播放趋势、平台占比、热力图
- **趋势分析** — 四平台月度堆叠柱状图、互动率走势、平台间对比
- **平台对比** — 五维雷达图、播放/互动/贡献度多维度对比
- **爆款排行** — TOP20排行，支持按播放/互动/评论排序
- **平台深度** — 四平台独立分析（含完播率、涨粉、投币、弹幕等专属指标）
- **任务管理** — 153条选题任务展示、筛选搜索、作者智能匹配
- **热点选题** — 实时百度/微博热搜聚合 + AI选题建议
- **驾驶舱大屏** — ECharts GL 3D中国地图 + 全屏可视化仪表盘

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | HTML5 · CSS3 · JavaScript · Chart.js 4.4 · ECharts 5.4 + GL 2.0 |
| 后端 | Python 3.13 (http.server) |
| 数据 | 飞书开放API (tenant_access_token) |
| 开发 | 腾讯 WorkBuddy AI 辅助开发 |

## 🚀 新手部署指南（零基础也能看懂）

这是一份给**完全没有编程经验**的朋友准备的图文教程，5 分钟跑起来。

---

### 第一步：确认你的电脑有 Python

**Windows 用户（绝大多数情况）：**

1. 按下键盘 `Win + R`，输入 `cmd`，回车
2. 在弹出来的黑色窗口里输入 `python --version`，回车
3. 如果显示 `Python 3.x.x`，说明已安装，跳到第二步
4. 如果提示"不是内部命令"，需要先装 Python：

> 打开 https://www.python.org/downloads/
> 点击黄色大按钮下载 → 安装时**勾选 "Add Python to PATH"**（这一步很重要！）
> → 一路下一步装完 → 重新打开 cmd 再试 `python --version`

**Mac 用户：**
> 打开"终端"，输入 `python3 --version`，如果没装，去 python.org 下载 macOS 版。

---

### 第二步：下载项目文件

**方法A（推荐，最简单）：**

1. 打开 https://github.com/Jiangpan-star/media-dashboard
2. 点击绿色的 **「<> Code」** 按钮
3. 点击 **「Download ZIP」**
4. 解压到你喜欢的文件夹（比如桌面）

**方法B（如果你装了 Git）：**
```bash
git clone https://github.com/Jiangpan-star/media-dashboard.git
```

---

### 第三步：配置飞书密钥（只有这一步需要动一下脑子）

这个看板的数据是从飞书拉取的，所以需要一个"钥匙"。

1. 在项目文件夹里找到 `config_example.py`
2. **复制一份**，重命名为 `config.py`
3. 用记事本打开 `config.py`，你会看到：

```python
APP_ID     = 'cli_aa87b5969f391bdb'     # 你的飞书应用 ID
APP_SECRET = 'your_app_secret_here'      # 你的飞书应用密钥
APP_TOKEN  = 'your_app_token_here'       # 你的飞书多维表格 Token
```

4. 把 `your_app_secret_here` 和 `your_app_token_here` 替换成真实的密钥
5. 保存，关闭

> ⚠️ 如果你没有飞书 API 密钥，也可以用内置的示例数据先跑起来看效果——
> 项目里的 `data.json` 和 `tasks.json` 包含了约 50 条视频的示例数据。

---

### 第四步：启动！

**Windows 用户（最简单）：**
> 双击 `启动看板服务.bat`

**手动启动（任何系统）：**
> 在项目文件夹里打开命令行（cmd / 终端），输入：
```bash
python app.py
```

看到 `Serving HTTP on 0.0.0.0 port 8765` 就成功了！

---

### 第五步：打开浏览器

在浏览器地址栏输入：

```
http://localhost:8765
```

回车，应该就能看到数据大屏了！🎉

---

### 常见问题

| 问题 | 解决方法 |
|------|----------|
| `python` 不是内部命令 | Python 没装好，或者安装时没勾选 "Add to PATH"，重装一次 |
| 打开浏览器是空白页 | 检查 cmd 窗口有没有报错，常见原因：`config.py` 里的密钥没填对 |
| 端口被占用 | 把 `app.py` 最后一行的 `8765` 改成 `8766` 或其他数字 |
| 想后台运行 | Windows 双击 `后台启动服务.vbs`（静默运行，不弹黑窗口） |
| Mac/Linux 怎么跑 | 终端里 `python3 app.py` 就行，完全一样 |

---

### 项目启动后的截图

打开 `http://localhost:8765` 后，你会看到 8 个 Tab 标签页：

- **数据总览** — KPI 大数字 + 播放趋势图 + 热力图
- **趋势分析** — 四平台对比图
- **平台对比** — 雷达图
- **爆款排行** — TOP20 视频榜单
- **平台深度** — 每个平台独立分析
- **任务管理** — 153 条选题任务
- **热点选题** — 实时热搜 + AI 选题建议
- **驾驶舱** — 3D 地图大屏

## 项目结构

```
media-dashboard/
├── app.py                    # 后端服务（API + 飞书数据拉取）
├── index.html                # 主页面（8个Tab）
├── cockpit.html              # 驾驶舱大屏独立页面
├── config_example.py         # 配置文件模板
├── .gitignore
├── 启动看板服务.bat           # 前台启动脚本
├── autostart_hidden.bat      # 隐藏启动脚本
├── 后台启动服务.vbs           # 开机自启动脚本
├── logo_emblem.png           # 西北民大校徽
└── logo_media_center.png     # 融媒体中心logo
```

## 开发团队

- **小组**：融媒数析组（24级新闻学2班）
- **组长**：贾潘
- **成员**：张娜婧、谌梦婷、陈诗琪
- **课程**：Web前端技术课程实践作业

## License

仅供学习交流使用。
