# carousel.py — 频道轮播

独立小模块（约 80 行，无其他依赖）：**每隔固定分钟数**，把 `docs/carousel-content.md` 的全部内容作为一条消息发到指定频道，并在下一个周期到来时自动删除旧消息（`delete_after`），实现「频道里始终只有一条轮播公告」的效果。

## 工作方式

```
start_carousel(bot)  →  asyncio.create_task(carousel_loop(bot), name="carousel-message-loop")
```

`carousel_loop` 是无限循环，每轮：

1. 计算距下一个对齐时刻的秒数并 sleep（**对齐到墙钟时间**，例如间隔 10 分钟则固定在 10:00、10:10、10:20 发送，而不是「上一次发送后 10 分钟」）；
2. 读取 `docs/carousel-content.md`（UTF-8）全文，为空则跳过本轮；
3. 发送到 `CAROUSEL_CHANNEL_ID`，`delete_after=interval_minutes * 60`；
4. 任何异常（文件不存在、频道找不到、Discord 报错）只 `print` 不中断循环。

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
- **自动删旧**：靠 `delete_after` 实现，机器人不维护任何消息引用状态。代价：机器人重启后**正在显示的那条不会有人去删**，会多留一个周期（下一条发出时旧条已到期自然消失；若改过间隔则可能短暂出现两条）。
- **无状态**：模块不落盘任何数据，重启无副作用（`_carousel_started` 标志见 bot.md）。

## 与其他模块的关系

- 由 `bot.py` 的 `on_ready` 启动，任务句柄存在 `bot._carousel_task`（仅用于持有引用，没有别的模块读它）；
- 不依赖活动额度 API、不依赖 SQLite、不注册任何事件监听——是四 大模块中最独立的一个，可作为「新定时任务类功能」的参考模板。

## 常见改动

- **换频道**：改文件顶部 `CAROUSEL_CHANNEL_ID`。
- **改间隔**：改 `.env` 的 `CAROUSEL_INTERVAL_MINUTES`，重启生效。
- **多条轮播内容轮换**：把 `docs/carousel-content.md` 改成多段（如用 `---` 分隔），在 `carousel_loop` 里维护索引逐轮取下一段；或直接复制本模块做一个新的（见 [extending-guide.md](extending-guide.md)）。
