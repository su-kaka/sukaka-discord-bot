# mama.py — 找妈妈（家庭组共享登记）

频道 `1455038454772531311`（与轮播共用）的家庭组互助登记功能：想加入别人「谷歌家庭组」共享 pro 的人（找妈妈）与乐于分享位置的人（妈妈）互相匹配。组家庭本身的操作教程见 [family-group-guide.md](family-group-guide.md)。

## 使用方式

六个斜杠命令，其中三个常规命令**仅限找妈妈频道**（`MAMA_CHANNEL_ID = 1455038454772531311`）内可用，其他频道里会收到 ephemeral 提示「仅限找妈妈频道」；三个拉黑管理命令不限频道，但仅限 `BLOCK_COMMAND_USER_IDS` 列表里的用户使用：

| 命令 | 效果 |
| --- | --- |
| `/登记妈妈` | 仅你可见的提示消息 + 两个按钮（5 分钟内有效）。点【登记/更新我的登记】弹出 Modal 表单（区域必填、备注选填，已有登记时预填旧值），提交保存；点【删除我的登记】删除记录 |
| `/找妈妈` | 仅你可见的按区域分组登记列表（每人一行 mention + 备注）+ 下拉菜单（10 分钟内有效）。在菜单选中某人 → 再次仅你可见地显示对方登记，点击对方头像即可私信联系 |
| `/家庭组教程` | 仅你可见的 Gemini Pro 家庭组共享教程。教程源文件是 [family-group-guide.md](family-group-guide.md)，发送时现场把 Markdown 渲染成 Discord 文本（`#`→emoji 标题、`##`→加粗、`- `→•）——**改教程只改这一个文件，无需重启** |
| `/拉黑 <用户>` | 仅管理员（`BLOCK_COMMAND_USER_IDS`，当前只有 `1077843520917872670`，后续拓展往列表加 ID 即可）。把指定用户加入拉黑名单，禁止其使用 /找妈妈；重复拉黑幂等提示 |
| `/解除拉黑 <用户>` | 仅管理员。把用户移出拉黑名单，恢复其 /找妈妈 权限；参数是 `User` 类型，已退服用户也可直接填 ID |
| `/拉黑列表` | 仅管理员。查看当前拉黑名单（mention + 拉黑时间 UTC），不 ping 被拉黑者，超长自动分块 |

- 一人一条记录：重复登记即覆盖更新（保留首次登记时间，保证排序稳定）。
- 联系方式不需要填：列表里 mention 点击头像即可私信。
- **全部响应都是 ephemeral**（仅发起者本人可见）：频道里不会出现任何公开消息，也不会 @ 任何登记者。ephemeral 消息上的按钮/菜单只能由发起者本人使用，因此不需要 owner 校验。
- **频道白名单**：/登记妈妈 /找妈妈 /家庭组教程 三个命令的入口都先过 `_deny_if_wrong_channel`（比对 `interaction.channel_id`），不在找妈妈频道时回复仅本人可见的提示并短路。拉黑命令不限频道（管理员可能在任何地方处理），但入口先过 `_deny_if_not_block_admin`（比对 `interaction.user.id` 是否在 `BLOCK_COMMAND_USER_IDS`）。
- 超长内容（列表/教程）按 1800 字符贪心分块，第一块走 `interaction.response`，其余走 `interaction.followup`，下拉菜单挂在最后一块上。

## 消息流（架构）

本模块是**斜杠命令模块**，不监听普通消息、不注册任何事件监听（与 `channel_admin.py` 同一形态）。`register_commands(bot)` 由 `bot.py` 的 `setup_hook()` 调用（`on_ready` 里 `tree.sync()` 同步生效）：

```
bot.setup_hook（bot.py）
  └── mama.register_commands(bot)   # 注册 /登记妈妈 /找妈妈 /家庭组教程 /拉黑 /解除拉黑 /拉黑列表
bot.on_ready
  └── await tree.sync()             # 同步到 Discord（全局命令最多 1 小时生效）
```

## 存储

- SQLite `mama.db`（env `MAMA_DB` 可覆盖），import 时建表。
- 表 `mama_registrations`：`discord_id INTEGER PRIMARY KEY`（一人一条）、`display_name`（Select 菜单 label 用；mention 渲染永远显示当前名，改名后重新登记即可刷新）、`region`、`note`、`created_at`（首次登记时间，upsert 不覆盖）、`updated_at`。
- 表 `mama_blocks`：`discord_id INTEGER PRIMARY KEY`（一人一条，`INSERT OR IGNORE` 幂等）、`blocked_at`（拉黑时间，列表展示用）。

## 拉黑机制

- **管理员列表**：`BLOCK_COMMAND_USER_IDS = (1077843520917872670,)`——服务端硬编码的元组，后续要给更多人授权直接往里加 ID 即可（改完需重启）。
- **拦截优先级**：/找妈妈 的 `_deny_if_missing_role` 里依次检查：①用户级拉黑（查 `mama_blocks` 表）②身份组黑名单（`BLOCKED_ROLE_IDS`）③身份组白名单（`REQUIRED_ROLE_IDS`）。被拉黑用户即使持有白名单身份组也会被拒。
- **不自动删登记**：拉黑只挡 /找妈妈，不删除对方的登记记录；对方仍可 /登记妈妈（只挡看别人，不影响自己登记）。需要时可用 /解除拉黑 恢复。
- 拉黑/解除拉黑的响应都是 ephemeral，仅在频道里不产生公开消息；/拉黑列表 用 mention 渲染但不 ping（`AllowedMentions.none()`）。

## 模块内部结构

- **DB 层**：登记 `get_registration` / `upsert_registration` / `delete_registration` / `get_all_registrations`，拉黑 `is_blocked` / `add_block` / `remove_block` / `get_all_blocks`。`MamaRegistration`（NamedTuple，带 `mention` 属性即时构造 `<@id>`，渲染列表不需要查成员对象）。
- **UI 层**：
  - `RegisterPromptView`：两按钮，`timeout=300`（5 分钟）——ephemeral 消息不会自动删除，View 超时后按钮失效，重发命令即可。
  - `RegisterModal`：`__init__` 里动态构造 TextInput 并 `default` 预填（不用类属性——共享原型有串值风险）；`timeout=None` 弹窗不本地过期；`on_submit` 服务端复检（`required=True` 挡不住纯空格）并用 `" ".join(value.split())` 折叠空白（备注换行会破坏「每人一行」列表格式）。
  - `MamaSelect`/`MamaSelectView`：下拉菜单（`timeout=600`，10 分钟），回调时**重新查库**（列表发出后对方可能已删除登记）。ephemeral 消息的组件只有发起者能用，无需 owner 校验。
- **渲染**：`_render_list_text` 按区域分组（dict 保插入序），`_split_text_chunks` 贪心 1800 字符分块（Discord 2000 上限留余量）；`_build_select_options` 截断前 25 条（Discord Select 硬上限），页脚注明。
- **教程文案**：`_load_guide_text` 每次触发时现场读 `GUIDE_FILE`（`docs/family-group-guide.md`，env `MAMA_GUIDE_FILE` 可覆盖），做轻量 Markdown → Discord 文本转换（`# `→emoji 标题、`## `→加粗、`- `→•、HTML 注释整段丢弃、连续空行折叠）。**不缓存**：改教程文件即时生效，无需重启。

## 启动接线（bot.py）

斜杠命令模块惯例（与 channel_admin 相同）：`from mama import register_commands as register_mama_commands` + `setup_hook()` 里调用 `register_mama_commands(self)`（在 `on_ready` 的 `tree.sync()` 之前）。不再有 `start_mama` / `_mama_started` 幂等标志（`setup_hook` 每次连接只调用一次，无需幂等保护）。

## 已知行为边界

- ephemeral 消息由 Discord 侧持久关联到发起者，机器人重启后按钮/菜单仍可用（回调走新进程同样处理；与旧版 `delete_after` 消息残留问题相比反而是改善）。
- 全局斜杠命令同步最多 1 小时生效一次，上线新命令后可稍等或用 `/命令` 刷新验证。
- 「美东」与「美 东」是两个区域组；空白折叠只处理字面空白，不做同义词归一。
- 无新 Intent / 无新权限要求；不再需要 `message_content` Intent（这是本模块的触发方式）。
