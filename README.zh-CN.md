# TradingView Webhook 警报接收服务

[English](README.md) · [验证记录](docs/VERIFICATION.md) · [TradingView 配置说明](docs/TRADINGVIEW.md)

**把图表信号变成经过校验、可以审计且不会重复执行的模拟动作。**

这是一个独立作品集项目：使用 FastAPI 接收 TradingView JSON 警报，校验令牌、字段和时效，
将有效警报转交给模拟执行器，并把警报与模拟动作一起写入 SQLite。项目不连接券商、不下真实订单。

## 核心能力

- **输入校验：**流式读取限制 16 KiB，拒绝畸形 JSON、重复字段、额外字段、无效价格和过期信号。
- **安全认证：**启动时必须配置随机专用令牌；令牌通过 JSON 的 `token` 字段传递，
  不写入数据库，不出现在应用日志或错误响应中。
- **持久化去重：**相同 `event_id` 和规范化内容返回 `200 duplicate`；相同 ID、不同内容返回 `409`。
  并发请求和服务重启仍然只记录一次模拟动作。
- **原子处理：**警报与模拟动作在同一事务提交。模拟执行失败会回滚；数据库锁竞争返回 `503`。
- **可验证：**离线演示、真实本地 HTTP 示例、异常与并发测试、英文/中文文档，以及多版本 CI。

技术栈：Python 3.11+、FastAPI、Pydantic、SQLite、Uvicorn；pytest 和 HTTPX 用于验证。

## 五分钟运行

```powershell
git clone https://github.com/harper698/tradingview-webhook-service.git
cd tradingview-webhook-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python examples/demo.py
```

macOS/Linux 使用 `source .venv/bin/activate` 激活环境。离线演示会生成临时令牌和临时数据库，
依次验证首次接收、重复请求、ID 冲突、错误认证和重启后去重；最后确认数据库只有一条警报和一条模拟动作。
演示不需要 TradingView 账号，也不访问网络。

若 PowerShell 阻止激活脚本，可直接用 `.\.venv\Scripts\python.exe` 替代 `python`，
用 `.\.venv\Scripts\tv-webhook.exe` 替代 `tv-webhook`，无需修改执行策略。

仅运行服务时，可安装 `python -m pip install -r requirements.txt`；离线演示和测试需要上面的开发依赖。
若需使用本次验证的精确运行时依赖版本，可改用 `python -m pip install -r requirements-lock.txt`。

## 启动本地服务

```powershell
$env:WEBHOOK_SECRET = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:WEBHOOK_ALLOWED_SYMBOLS = 'BINANCE:BTCUSDT,NYSE:IBM'
tv-webhook
```

默认监听 `http://127.0.0.1:8000`。在第二个终端设置与服务器**完全相同的** `WEBHOOK_SECRET`，
再运行 `python examples/send_alert.py` 或 `.\examples\send_alert.ps1`。
示例自动生成当前时间戳和唯一 ID，并将同一请求发送两次，得到 `accepted` 和 `duplicate`。
不要为客户端重新生成另一个令牌。

| 接口 | 用途 |
| --- | --- |
| `GET /healthz` | 数据库就绪检查，返回模拟模式 |
| `POST /webhook` | 接收警报并记录模拟动作 |
| `GET /docs` | 交互式接口文档 |

## 请求与响应

```json
{
  "token": "REPLACE_WITH_YOUR_SCOPED_WEBHOOK_TOKEN",
  "event_id": "ma01.BTCUSDT.buy.20261004T120000Z",
  "symbol": "BINANCE:BTCUSDT",
  "action": "buy",
  "price": 60000.25,
  "timestamp": "2026-10-04T12:00:00Z"
}
```

这是格式示例，发送前需替换令牌与当前时间戳。`price` 必须是 JSON 数字，大于零、不超过 `1e12`，
最多 24 位数字和 12 位小数。拒绝布尔值、数字字符串、`NaN` 和无穷大。`action` 只能是 `buy` 或 `sell`。
ID 最长 128 字符，可用 ASCII 字母、数字、点、下划线、冒号和连字符；交易品种最长 64 字符，
使用大写字母、数字和 `._:/-`，首字符须为字母或数字。禁止额外字段。

时间戳必须包含时区。默认允许过去 300 秒至未来 30 秒之间的信号；**重复请求同样需要通过认证与时间校验**。
重试需保留原有 ID、时间戳和内容，并在时效窗口内完成。

| 状态码 | 含义 |
| --- | --- |
| `202` | 新警报和模拟动作已提交 |
| `200` | 相同内容已处理，不新增动作 |
| `400` | JSON 无效、重复键或请求不完整 |
| `401` | 令牌缺失或错误 |
| `409` | 相同 ID 对应不同内容 |
| `413` | 请求超过 16 KiB |
| `415` | 类型不支持或请求体被压缩 |
| `422` | 字段、时间或品种校验失败 |
| `500` | 数据库存储失败 |
| `503` | 数据库暂忙，保持原请求重试；返回 `Retry-After: 1` |

## 设计亮点

`HTTP → 流式限量 → 认证与字段校验 → 时间/品种校验 → 线程池 → SQLite 事务 → 模拟动作审计`

[`store.py`](src/tv_webhook/store.py) 使用 `BEGIN IMMEDIATE` 在检查 ID 前获取写锁，
然后在同一事务内写入 `alerts` 和 `simulated_actions`。两张表都以 `event_id` 为主键。
没有会随进程退出丢失的内存去重缓存。价格以 Decimal 规范化，时间统一为 UTC，
因此 `100` 与 `100.0`、同一时刻的不同 UTC 偏移表示都可正确识别为重复内容。

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEBHOOK_SECRET` | 必填 | 32–128 个 URL 安全 ASCII 字符，使用随机生成的专用令牌 |
| `WEBHOOK_DB_PATH` | `data/alerts.sqlite3` | SQLite 文件路径，应使用持久化本地磁盘 |
| `WEBHOOK_MAX_AGE_SECONDS` | `300` | 最大延迟，范围 1–86400 |
| `WEBHOOK_FUTURE_SKEW_SECONDS` | `30` | 未来时间容差，范围 0–300 |
| `WEBHOOK_ALLOWED_SYMBOLS` | 空 | 逗号分隔的精确品种名单；空值允许所有格式有效的品种 |

`.env.example` 仅作为参考，应用不会自动加载 `.env`。
可通过 `tv-webhook --host 127.0.0.1 --port 8000` 指定监听地址。

## TradingView 与交付边界

详细步骤、官方来源与 Pine v6 示例见 [TradingView 配置说明](docs/TRADINGVIEW.md)。
模板使用 `{{timenow}}` 作为触发时间，而不是可能过期的 K 线时间 `{{time}}`。
`price` 使用不带引号的 `{{close}}`。每个警报规则需要自己的 ID 前缀；模板按每根 K 线收盘最多触发一次设计，
同秒多次触发需要更强的上游唯一 ID。

本项目已验证离线流程和真实本地 HTTP 请求，**未部署公网服务，也未验证实际 TradingView 警报投递**。
公网使用需配置 HTTPS 443、反向代理请求超时与大小限制、入口限流、持久化磁盘和监控。
使用独立 Webhook 令牌，不能填入券商密钥、交易账号密码或 TradingView 登录信息；代理和监控同样应关闭请求体记录。

“一次动作”保证仅限于保留中的 SQLite 记录。接入真实外部执行器需要事务发件箱、后台工作进程、
目标系统幂等与对账，不能简单在事务里加入下单请求。此项目没有生产托管、自动清理、数据库迁移或分布式队列。
时效校验用于限制重放窗口；共享令牌一旦泄露，无法证明发送方就是 TradingView。

## 验证

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

实际验证结果和限制记录在 [VERIFICATION.md](docs/VERIFICATION.md)。

关联作品：[金融数据管道](https://github.com/harper698/market-data-pipeline)。
本项目采用 [MIT 许可证](LICENSE)，与 TradingView 无隶属关系。
