# Windows 原生 Chrome 采集

Windows 版由 Python 后端、蓝色 Web 界面和 Chrome 扩展组成。扩展使用当前 Chrome profile 的真实登录状态，打开最小化的普通浏览器窗口采集搜索、商品详情和卖家页面。后端继续执行筛选、去重、价格历史、AI 分析、通知和定时任务。

## 首次启动

需要安装 Python 3.11+、Chrome，以及构建前端时使用的 Node.js。运行：

```powershell
.\start-windows.ps1 -Setup
```

若 PowerShell 阻止执行，可仅为当前进程放开脚本执行策略：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -Setup
```

脚本在 `.venv` 安装 `requirements-native.txt`，Python 依赖使用 TUNA。首次构建前端时会先测试 npm 国内镜像连通性和延迟；路由不符合项目规则时停止安装并提示检查。Windows 运行依赖不包含 Playwright，也不下载 Chromium。

已有环境时运行 `.\start-windows.ps1`。前端代码更新后运行 `.\start-windows.ps1 -Build`。可用 `-Port 8001` 更换端口、`-NoBrowser` 只启动后端。服务监听 `127.0.0.1`；保持终端运行，Ctrl+C 停止服务。

## 数据持久化

任务、采集结果和价格历史保存在 `data/app.sqlite3`。Windows 启动入口读取 `.env` 中的 `APP_DATABASE_FILE`，将路径固定为绝对路径并传给后端；默认使用项目根目录下的 `data/app.sqlite3`，重启或更新源码继续使用该数据库。

从 DSM 迁移时，先通过 SQLite online backup 生成包含已提交 WAL 数据的一致快照，验证完整性后替换本机数据库，并复制任务引用的 `prompts` 文件。替换前保留本机旧库，迁移快照及清单保存在 `data/backups`。迁移后的运行状态重置为停止；Cron、账号绑定和筛选条件保持原值。

## 安装并配对扩展

1. Chrome 打开 `chrome://extensions`，开启开发者模式，点击“加载已解压的扩展程序”，选择项目的 `chrome-extension` 文件夹。已有旧扩展时点击重新加载。
2. 登录本机蓝色界面，打开“系统设置 → Chrome 采集”，输入管理员密码，获取配对令牌。
3. 打开扩展的“采集连接设置”，填写 `http://127.0.0.1:8000`（或实际端口）及令牌，启用后台采集连接。
4. 在同一个 Chrome profile 中打开闲鱼并登录。管理界面显示扩展在线后，可以使用原来的启动、停止和 Cron 定时任务。

扩展后台通过 Chrome alarms 定期唤醒。命令、标签页和待上报结果保存在扩展本地存储中；网络恢复后重传，上报按 command ID 去重。后端重启会将任务重置为停止，需要重新启动任务。

遇到登录、验证码、页面异常或断线，任务会等待人工处理，采集窗口会恢复显示。完成处理后，点击扩展弹窗中的“完成登录 / 验证后继续”。停止任务只关闭扩展为任务创建的闲鱼标签页，不关闭其他标签页。

## 账号与网络

- 自动账号模式使用在线 Chrome profile；固定账号模式要求扩展设置中的“绑定账号文件”与任务所选账号路径一致（例如 `state/my-account.json`）。绑定名称用于选 profile，不会向 Chrome 导入 cookies。
- 轮换模式在已连接的 Chrome profiles 之间选择账号。需要分别打开不同的 Chrome profile、登录对应账号并配置扩展。同一个 profile 的采集任务按顺序执行，AI 分析仍可并发。
- 浏览器网络使用 Chrome 自身的系统代理或 profile 配置；原爬虫的代理池不会替扩展切换 Chrome 代理。AI 请求继续使用后端配置。

## 采集范围与验证

扩展观察页面正常发出的 fetch、XHR 和 JSONP 请求，读取搜索、详情、用户资料、商品和评价数据；DOM 作为搜索和详情的补充。筛选和分页通过页面控件操作；后端再次执行价格、区域、包邮、关键词及个人卖家条件检查。无法确认个人卖家身份时不会放行。页面变化时可能需要调整扩展中的解析器和选择器。

卖家商品和评价随页面滚动加载，单个列表最多收集 500 条；保存的数据注明采集范围。普通 Chrome 在最小化或系统休眠时仍可能被节流；长时间无响应的任务会等待你检查并继续。

测试命令（现有 conda 风格环境也可使用 `.venv\python.exe`）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_native_browser_service.py tests/integration/test_api_browser.py
cd chrome-extension
npm test
```

Linux / DSM 的默认运行方式保持为原有采集器；可通过 `SCRAPER_BACKEND=playwright` 显式选择。Windows 启动脚本固定使用 `native_chrome`。
