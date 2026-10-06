# carousel.py — 频道轮播

独立小模块（约 80 行，无其他依赖）：**每隔固定分钟数**，把 `docs/carousel-content.md` 的全部内容作为一条消息发到指定频道，并在下一个周期到来时自动删除旧消息（`delete_after`），实现「频道里始终只有一条轮播公告」的效果。

## 工作方式

```
start_carousel(bot)  →  asyncio.create_task(carousel_loop(bot), name="carousel-message-loop")
```

`carousel_loop` 是无限循环，每轮：

1. 计算距下一个对齐时刻的秒数并 sleep（**对齐到墙钟时间**，例如间隔 10 分钟则固定在 10:00、10:10、10:20 发送，而不是「上一次发送后 10 分钟」）；
2. 获取 `CAROUSEL_CHANNEL_ID` 频道；**首轮**先回查频道最近 25 条历史，删除本机器人遗留的旧轮播消息（应对 `delete_after` 随重启丢失）；
3. 读取 `docs/carousel-content.md`（UTF-8）全文，为空则跳过本轮发送；
4. 发送到目标频道，`delete_after=interval_minutes * 60`；
5. 任何异常（文件不存在、频道找不到、Discord 报错）只 `print` 不中断循环。

## 可配置项

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CAROUSEL_CHANNEL_ID`（常量） | `1455038454772531311` | 目标频道，**改代码** |
| `CAROUSEL_FILE`（env） | `docs/carousel-content.md` | 轮播内容文件路径 |
| `CAROUSEL_INTERVAL_MINUTES`（env） | `1` | 间隔分钟数，可为小数；非正数/非法值自动回退默认并打印警告 |

```env
# .env 示例
CAROUSEL_FILE=docs/carousel-content.md
CAROUSEL_INTERVAL_MINUTES=1
```

内容更新流程：直接编辑 `docs/carousel-content.md` 保存即可，**无需重启**——每轮发送前重新读文件。

## 关键实现细节

- **墙钟对齐**（`seconds_until_next_slot`）：`remaining = interval - (now % interval)`，若恰好落在对齐点上则本轮立即发送。好处是重启机器人不会打乱发布节奏。
- **频道获取兜底**：先 `bot.get_channel`（缓存）， miss 则 `fetch_channel`（API 请求）；只接受 `TextChannel` / `Thread`，否则视为配置错误跳过本轮。
- **自动删旧**：靠 `delete_after` 实现，机器人不维护任何消息引用状态。`delete_after` 的删除任务只存在于进程内，重启即丢失；因此循环首轮会先扫描频道最近 `STALE_SCAN_LIMIT`（25）条消息，把本机器人遗留的旧轮播消息删掉再发送，保证重启后频道里也只有一条轮播公告。
- **无状态**：模块不落盘任何数据，重启恢复靠频道历史回查（`_carousel_started` 标志见 bot.md）。

## 与其他模块的关系

- 由 `bot.py` 的 `on_ready` 启动，任务句柄存在 `bot._carousel_task`（仅用于持有引用，没有别的模块读它）；
- 不依赖活动额度 API、不依赖 SQLite、不注册任何事件监听——是四 大模块中最独立的一个，可作为「新定时任务类功能」的参考模板。

## 常见改动

- **换频道**：改文件顶部 `CAROUSEL_CHANNEL_ID`。
- **改间隔**：改 `.env` 的 `CAROUSEL_INTERVAL_MINUTES`，重启生效。
- **多条轮播内容轮换**：把 `docs/carousel-content.md` 改成多段（如用 `---` 分隔），在 `carousel_loop` 里维护索引逐轮取下一段；或直接复制本模块做一个新的（见 [extending-guide.md](extending-guide.md)）。
