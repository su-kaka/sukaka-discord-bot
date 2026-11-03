# mama.py — 找妈妈（家庭组共享登记）

频道 `1455038454772531311`（与轮播共用）的家庭组互助登记功能：想加入别人「谷歌家庭组」共享 pro 的人（找妈妈）与乐于分享位置的人（妈妈）互相匹配。组家庭本身的操作教程见 [family-group-guide.md](family-group-guide.md)。

> 文件顶部 `MAMA_ENABLED = False` 可彻底关闭本模块（`register_commands` 直接跳过，六个斜杠命令全部不注册）。

## 使用方式

六个斜杠命令，其中三个常规命令**仅限找妈妈频道**（`MAMA_CHANNEL_ID = 1455038454772531311`）内可用，其他频道里会收到 ephemeral 提示「仅限找妈妈频道」；三个拉黑管理命令不限频道，但仅限 `BLOCK_COMMAND_USER_IDS` 列表里的用户使用：

| 命令 | 效果 |
| --- | --- |
| `/登记妈妈` | 仅你可见的提示消息 + 两个按钮（5 分钟内有效）。点【登记/更新我的登记】弹出 Modal 表单（区域必填、备注选填，已有登记时预填旧值），提交保存；点【删除我的登记】删除记录。登记有效期 7 天，到期自动删除，重新提交即重新计时 |
| `/找妈妈` | 仅你可见的分页登记列表（Components V2 布局，10 分钟内有效）：每页 10 人按区域分组、每人一行 mention + 备注，下方是与行对应的名字按钮（点开仅你可见地显示对方登记，点击对方头像即可私信联系），多页时有上一页/下一页按钮 |
| `/家庭组教程` | 仅你可见的 Gemini Pro 家庭组共享教程。教程源文件是 [family-group-guide.md](family-group-guide.md)，发送时现场把 Markdown 渲染成 Discord 文本（`#`→emoji 标题、`##`→加粗、`- `→•）——**改教程只改这一个文件，无需重启** |
| `/拉黑 <用户>` | 仅管理员（`BLOCK_COMMAND_USER_IDS`，当前只有 `1077843520917872670`，后续拓展往列表加 ID 即可）。把指定用户加入拉黑名单，禁止其使用 /找妈妈；重复拉黑幂等提示 |
| `/解除拉黑 <用户>` | 仅管理员。把用户移出拉黑名单，恢复其 /找妈妈 权限；参数是 `User` 类型，已退服用户也可直接填 ID |
| `/拉黑列表` | 仅管理员。查看当前拉黑名单（mention + 拉黑时间 UTC），不 ping 被拉黑者，超长自动分块 |

- 一人一条记录：重复登记即覆盖更新（保留首次登记时间，保证排序稳定）。
- **登记 7 天自动过期**：过期判定基于 `updated_at`，重新提交即重新计时；过期记录在读取时惰性清理（无需后台任务）。想继续当妈妈重新 /登记妈妈 即可。
- 联系方式不需要填：列表里 mention 点击头像即可私信。
- **全部响应都是 ephemeral**（仅发起者本人可见）：频道里不会出现任何公开消息，也不会 @ 任何登记者。ephemeral 消息上的按钮只能由发起者本人使用，因此不需要 owner 校验。
- **频道白名单**：/登记妈妈 /找妈妈 /家庭组教程 三个命令的入口都先过 `_deny_if_wrong_channel`（比对 `interaction.channel_id`），不在找妈妈频道时回复仅本人可见的提示并短路。拉黑命令不限频道（管理员可能在任何地方处理），但入口先过 `_deny_if_not_block_admin`（比对 `interaction.user.id` 是否在 `BLOCK_COMMAND_USER_IDS`）。
- 超长内容（教程/拉黑列表）按 1800 字符贪心分块，第一块走 `interaction.response`，其余走 `interaction.followup`。

## 消息流（架构）

本模块是**斜杠命令模块**，不监听普通消息、不注册任何事件监听（与 `channel_admin.py` 同一形态）。`register_commands(bot)` 由 `bot.py` 的 `setup_hook()` 调用（`on_ready` 里 `tree.sync()` 同步生效）：

```
bot.setup_hook（bot.py）
  └── mama.register_commands(bot)   # 注册 /登记妈妈 /找妈妈 /家庭组教程 /拉黑 /解除拉黑 /拉黑列表
bot.on_ready
  └── await tree.sync()             # 同步到 Discord（全局命令最多 1 小时生效）
```

## 存储

- SQLite `data/mama.db`（env `MAMA_DB` 可覆盖），import 时建表。
- 表 `mama_registrations`：`discord_id INTEGER PRIMARY KEY`（一人一条）、`display_name`（详情按钮 label 用；mention 渲染永远显示当前名，改名后重新登记即可刷新）、`region`、`note`、`created_at`（首次登记时间，upsert 不覆盖）、`updated_at`（过期判定基准，`REGISTRATION_TTL_SECONDS = 7 天`，读取时惰性清理超期记录）。
- 表 `mama_blocks`：`discord_id INTEGER PRIMARY KEY`（一人一条，`INSERT OR IGNORE` 幂等）、`blocked_at`（拉黑时间，列表展示用）。

## 拉黑机制

- **管理员列表**：`BLOCK_COMMAND_USER_IDS = (1077843520917872670,)`——服务端硬编码的元组，后续要给更多人授权直接往里加 ID 即可（改完需重启）。
- **拦截优先级**：/找妈妈 的 `_deny_if_missing_role` 里依次检查：①用户级拉黑（查 `mama_blocks` 表）②身份组黑名单（`BLOCKED_ROLE_IDS`）③身份组白名单（`REQUIRED_ROLE_IDS`）。被拉黑用户即使持有白名单身份组也会被拒。
- **不自动删登记**：拉黑只挡 /找妈妈，不删除对方的登记记录；对方仍可 /登记妈妈（只挡看别人，不影响自己登记）。需要时可用 /解除拉黑 恢复。
- 拉黑/解除拉黑的响应都是 ephemeral，仅在频道里不产生公开消息；/拉黑列表 用 mention 渲染但不 ping（`AllowedMentions.none()`）。

## 模块内部结构

- **DB 层**：登记 `get_registration` / `upsert_registration` / `delete_registration` / `get_all_registrations`，拉黑 `is_blocked` / `add_block` / `remove_block` / `get_all_blocks`；`_purge_expired_registrations` 在两个读路径（`get_registration` / `get_all_registrations`）开头惰性清理过期登记（同一事务内 DELETE，不引入后台循环任务）。`MamaRegistration`（NamedTuple，带 `mention` 属性即时构造 `<@id>`，渲染列表不需要查成员对象）。
- **UI 层**：
  - `RegisterPromptView`：两按钮，`timeout=300`（5 分钟）——ephemeral 消息不会自动删除，View 超时后按钮失效，重发命令即可。
  - `RegisterModal`：`__init__` 里动态构造 TextInput 并 `default` 预填（不用类属性——共享原型有串值风险）；`timeout=None` 弹窗不本地过期；`on_submit` 服务端复检（`required=True` 挡不住纯空格）并用 `" ".join(value.split())` 折叠空白（备注换行会破坏「每人一行」列表格式）。
  - `MamaListView`：`discord.ui.LayoutView`（Discord **Components V2** 布局，discord.py 2.6+）承载 /找妈妈 列表，`timeout=600`（10 分钟）。结构：标题 TextDisplay → 区域标题 + 每人一行 TextDisplay → 名字按钮排（每 5 个一排 `ActionRow`，与行顺序对应）→ 分隔线 + 页码页脚 → 翻页排（多页才有）。**翻页/详情回调都重新查库**（列表发出后数据可能变化）：翻页 `_refresh` 查库后 `clear_items` 重建组件并 `edit_message`；数据变少页码自动回落，全部清空则整条消息替换成空态提示并 `stop()`。详情按钮回调重查单人登记，已删除则提示刷新。组件数最坏约 35 个（Discord 单消息 40 组件上限内）。
- **渲染**：`_split_text_chunks` 贪心 1800 字符分块（教程/拉黑列表用，Discord 2000 上限留余量）；/找妈妈 列表本身是 Components V2 分页消息，不再走文本分块。
- **教程文案**：`_load_guide_text` 每次触发时现场读 `GUIDE_FILE`（`docs/family-group-guide.md`，env `MAMA_GUIDE_FILE` 可覆盖），做轻量 Markdown → Discord 文本转换（`# `→emoji 标题、`## `→加粗、`- `→•、HTML 注释整段丢弃、连续空行折叠）。**不缓存**：改教程文件即时生效，无需重启。

## 启动接线（bot.py）

斜杠命令模块惯例（与 channel_admin 相同）：`from mama import register_commands as register_mama_commands` + `setup_hook()` 里调用 `register_mama_commands(self)`（在 `on_ready` 的 `tree.sync()` 之前）。不再有 `start_mama` / `_mama_started` 幂等标志（`setup_hook` 每次连接只调用一次，无需幂等保护）。

## 已知行为边界

- ephemeral 消息由 Discord 侧持久关联到发起者，机器人重启后按钮仍可用（回调走新进程同样处理；与旧版 `delete_after` 消息残留问题相比反而是改善）。
- 翻页按钮的有效期是 10 分钟（`LIST_VIEW_TIMEOUT`）：超时后按钮失效，重发 /找妈妈 即可获得新列表。翻页不重置该计时（与旧下拉菜单行为一致）。
- 全局斜杠命令同步最多 1 小时生效一次，上线新命令后可稍等或用 `/命令` 刷新验证。
- 「美东」与「美 东」是两个区域组；空白折叠只处理字面空白，不做同义词归一。列表分页按登记时间升序切页，区域分组只合并相邻行，跨页不合并。
- **登记过期**：`updated_at` 距今超过 7 天的记录在读取时被清理——过期后对方点详情/翻页会得到「已不存在」或列表变小属正常现象；**过期只影响登记，不影响拉黑记录**（`mama_blocks` 不过期）。
- 无新 Intent / 无新权限要求；不再需要 `message_content` Intent（这是本模块的触发方式）。
