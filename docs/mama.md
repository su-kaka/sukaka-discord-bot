# mama.py — 找妈妈（家庭组共享登记）

频道 `1455038454772531311`（与轮播共用）的家庭组互助登记功能：想加入别人「谷歌家庭组」共享 pro 的人（找妈妈）与乐于分享位置的人（妈妈）互相匹配。组家庭本身的操作教程见 [family-group-guide.md](family-group-guide.md)。

## 使用方式

| 输入 | 效果 |
| --- | --- |
| `登记妈妈` | Bot 发一条 @你 的提示消息 + 两个按钮（60 秒自动删）。点【登记/更新我的登记】弹出 Modal 表单（区域必填、备注选填，已有登记时预填旧值），提交保存；点【删除我的登记】删除记录 |
| `找妈妈` | Bot 发按区域分组的登记列表（每人一行 mention + 备注）+ 下拉菜单（120 秒自动删）。在菜单选中某人 → 仅你可见地显示对方登记，点击对方头像即可私信联系 |
| `组家庭教程` | Bot 发一段 Gemini Pro 家庭组共享的图文教程（120 秒自动删）。教程总结存在 [family-group-guide.md](family-group-guide.md)，与 `mama.py` 顶部的 `GUIDE_TEXT` 常量对应，改教程两边同步 |

- 一人一条记录：重复登记即覆盖更新（保留首次登记时间，保证排序稳定）。
- 联系方式不需要填：列表里 mention 点击头像即可私信。
- 「只有输入者能看到」的实现：Discord 机器人无法主动发仅指定人可见的频道消息，所以公开的只有 @你 + 按钮的提示消息；Modal 表单与全部确认/查询结果都走 ephemeral（仅本人可见）。
- 列表发消息时带 `allowed_mentions=none`（mention 照常渲染为 @名字，但**不会通知**任何登记者）；提示消息保留对发起者的 @（提醒回来点按钮）。

## 消息流（架构）

本模块**不注册自己的事件监听**（discord.py 的 `@bot.event` 是覆盖语义，唯一的 `on_message` 在 `bot.py`，详见 [bot.md](bot.md) 与 [extending-guide.md](extending-guide.md)）。`start_mama(bot)` 里调用 `bot.register_message_handler(MAMA_CHANNEL_ID, handle_mama_message)`，由 bot.py 的分发入口按频道调用：

```
bot.on_message（bot.py，全项目唯一）
  ├── author.bot 过滤（挡住 carousel 轮播与 bot 自己的按钮消息，防回环）
  └── 按频道 ID 查注册表 → mama.handle_mama_message(message)
       （游戏频道则进入 roulette 的处理器，两个频道完全隔离——
         妈妈频道的消息不触发额度掉落、不触发下线解除）
```

## 存储

- SQLite `mama.db`（env `MAMA_DB` 可覆盖），import 时建表。
- 表 `mama_registrations`：`discord_id INTEGER PRIMARY KEY`（一人一条）、`display_name`（Select 菜单 label 用；mention 渲染永远显示当前名，改名后重新登记即可刷新）、`region`、`note`、`created_at`（首次登记时间，upsert 不覆盖）、`updated_at`。

## 模块内部结构

- **DB 层**：`get_registration` / `upsert_registration` / `delete_registration` / `get_all_registrations`，`MamaRegistration`（NamedTuple，带 `mention` 属性即时构造 `<@id>`，渲染列表不需要查成员对象）。
- **UI 层**：
  - `RegisterPromptView`：两按钮，`timeout=60` 与消息 `delete_after` 对齐；**不覆写 on_timeout、不 edit 消息**（消息由 Discord 侧自动删除，这是与 beg.py/marry.py 惯例的关键差异）。
  - `RegisterModal`：`__init__` 里动态构造 TextInput 并 `default` 预填（不用类属性——共享原型有串值风险）；`timeout=None` 弹窗不本地过期；`on_submit` 服务端复检（`required=True` 挡不住纯空格）并用 `" ".join(value.split())` 折叠空白（备注换行会破坏「每人一行」列表格式）。
  - `MamaSelect`/`MamaSelectView`：下拉菜单，回调时**重新查库**（列表发出后对方可能已删除登记）。
- **渲染**：`_render_list_chunks` 按区域分组（dict 保插入序）+ 贪心 1800 字符分块（Discord 2000 上限留余量）；`_build_select_options` 截断前 25 条（Discord Select 硬上限），页脚注明。

## 启动接线（bot.py）

三行惯例：`from mama import start_mama` + `__init__` 加 `self._mama_started = False` + `on_ready` 幂等块调 `start_mama(self)`。`start_mama` 负责注册消息入口（`bot.register_message_handler`）并打印启动日志。

## 已知行为边界

- 重启后已发出的提示/列表消息会残留且按钮失效（`delete_after` 由运行中进程计时；与 carousel 行为一致，接受）。
- 「美东」与「美 东」是两个区域组；空白折叠只处理字面空白，不做同义词归一。
- 无新 Intent / 无新权限要求。
