# 活动额度机器人调用接口

## 1. 接口说明

活动额度接口用于：

- 查询当前登录用户的活动额度
- 给指定用户增加活动额度
- 给指定用户减少活动额度

活动额度每天北京时间 00:00 自动清零。活动模型会优先消耗活动额度，余额不足时再消耗普通额度。

基础地址示例：

```text
https://catiecli.sukaka.top
```

## 2. 配置专用密钥

在项目根目录 `.env` 文件中配置：

```env
ACTIVITY_QUOTA_API_KEY=请替换为随机生成的长密钥
```

可以使用以下命令生成密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

修改 `.env` 后重启后端服务使配置生效。

注意：

- 专用密钥只用于发放活动额度。
- 不要把专用密钥发送给普通用户。
- 不要把专用密钥提交到 Git 或写入机器人公开日志。

## 3. 给用户增加额度

### 请求

```http
POST /api/activity-quota/grant
Content-Type: application/json
X-Activity-Quota-Key: 你的活动额度专用密钥
```

用户可以通过 `user_id` 或 `username` 指定，但不能同时提供两个字段。

### 按用户名发放

```bash
curl -X POST "https://你的域名/api/activity-quota/grant" \
  -H "Content-Type: application/json" \
  -H "X-Activity-Quota-Key: 你的活动额度专用密钥" \
  -d '{"username":"testuser","amount":100}'
```

### 按用户 ID 发放

```bash
curl -X POST "https://你的域名/api/activity-quota/grant" \
  -H "Content-Type: application/json" \
  -H "X-Activity-Quota-Key: 你的活动额度专用密钥" \
  -d '{"user_id":123,"amount":100}'
```

`amount` 必须是大于 0 的整数。

### 成功响应

HTTP 状态码为 `200` 时表示发放成功：

```json
{
  "success": true,
  "message": "活动额度增加成功",
  "user_id": 123,
  "username": "testuser",
  "added": 100,
  "current_activity_quota": 250,
  "activity_quota": 250
}
```

机器人建议使用 `success` 判断是否成功，使用 `message` 作为提示文本，并播报：

```text
活动额度增加成功，用户 testuser 增加 100 点，当前额度 250 点。
```

其中 `activity_quota` 是兼容旧调用方保留的字段，新的机器人优先使用 `current_activity_quota`。

## 4. 给用户减少额度

### 请求

```http
POST /api/activity-quota/deduct
Content-Type: application/json
X-Activity-Quota-Key: 你的活动额度专用密钥
```

请求体格式与增加额度相同，可以使用 `user_id` 或 `username`：

```json
{
  "username": "testuser",
  "amount": 50
}
```

当用户当前额度不足时，接口会将额度扣减到 `0`，不会出现负数；接口仍返回成功，并在 `current_activity_quota` 中返回扣减后的余额。

### 成功响应

```json
{
  "success": true,
  "message": "活动额度减少成功",
  "user_id": 123,
  "username": "testuser",
  "deducted": 50,
  "current_activity_quota": 200,
  "activity_quota": 200
}
```

机器人可以播报：

```text
活动额度减少成功，用户 testuser 减少 50 点，当前额度 200 点。
```

## 5. 查询当前用户额度

该接口使用网页登录 JWT，不使用发放专用密钥。

```http
GET /api/activity-quota
Authorization: Bearer 用户登录 Token
```

成功响应：

```json
{
  "user_id": 123,
  "username": "testuser",
  "activity_quota": 250
}
```

## 6. 使用专用密钥查询指定用户额度

该接口使用专用密钥鉴权，可以查询任意用户的活动额度。

### 请求

```http
GET /api/activity-quota/query
Content-Type: application/json
X-Activity-Quota-Key: 你的活动额度专用密钥
```

请求体使用 `user_id` 或 `username` 指定目标用户：

```json
{
  "username": "testuser"
}
```

或：

```json
{
  "user_id": 123
}
```

### 成功响应

```json
{
  "success": true,
  "user_id": 123,
  "username": "testuser",
  "activity_quota": 250
}
```

机器人可以播报：

```text
用户 testuser 当前活动额度为 250 点。
```

## 7. 错误响应

### 专用密钥错误

HTTP `401`：

```json
{
  "detail": "无效的活动额度 API Key"
}
```

### 未配置专用密钥

HTTP `503`：

```json
{
  "detail": "活动额度发放接口未配置专用 API Key"
}
```

### 用户不存在

HTTP `404`：

```json
{
  "detail": "用户不存在"
}
```

### 请求参数错误

HTTP `422`。常见原因：

- `user_id` 和 `username` 都没有提供
- 同时提供了 `user_id` 和 `username`
- `amount` 不是正整数

## 8. Python 机器人示例

```python
import requests

BASE_URL = "https://你的域名"
ACTIVITY_QUOTA_KEY = "你的活动额度专用密钥"


def query_activity_quota(username: str) -> str:
    response = requests.get(
        f"{BASE_URL}/api/activity-quota/query",
        headers={
            "Content-Type": "application/json",
            "X-Activity-Quota-Key": ACTIVITY_QUOTA_KEY,
        },
        json={"username": username},
        timeout=15,
    )
    data = response.json()

    if response.ok and data.get("success") is True:
        return f"用户 {data['username']} 当前活动额度为 {data['activity_quota']} 点。"

    return f"查询失败：{data.get('detail', '未知错误')}"


def grant_activity_quota(username: str, amount: int) -> str:
    response = requests.post(
        f"{BASE_URL}/api/activity-quota/grant",
        headers={
            "Content-Type": "application/json",
            "X-Activity-Quota-Key": ACTIVITY_QUOTA_KEY,
        },
        json={"username": username, "amount": amount},
        timeout=15,
    )
    data = response.json()

    if response.ok and data.get("success") is True:
        return (
            f"{data['message']}，用户 {data['username']} "
            f"增加 {data['added']} 点，"
            f"当前额度 {data['current_activity_quota']} 点。"
        )

    return f"发放失败：{data.get('detail', '未知错误')}"


def deduct_activity_quota(username: str, amount: int) -> str:
    response = requests.post(
        f"{BASE_URL}/api/activity-quota/deduct",
        headers={
            "Content-Type": "application/json",
            "X-Activity-Quota-Key": ACTIVITY_QUOTA_KEY,
        },
        json={"username": username, "amount": amount},
        timeout=15,
    )
    data = response.json()

    if response.ok and data.get("success") is True:
        return (
            f"{data['message']}，用户 {data['username']} "
            f"减少 {data['deducted']} 点，"
            f"当前额度 {data['current_activity_quota']} 点。"
        )

    return f"扣减失败：{data.get('detail', '未知错误')}"
```

机器人处理建议：

- 先检查 HTTP 状态码和响应中的 `success` 是否为 `true`。
- 成功时播报 `message`、目标用户、操作数量和 `current_activity_quota`。
- 扣减数量大于当前余额时，仍视为成功，当前额度返回 `0`。
- 失败时播报响应中的 `detail`，不要把专用密钥或完整请求头输出到聊天记录。
