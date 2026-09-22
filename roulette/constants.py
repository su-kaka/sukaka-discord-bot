"""roulette 包共享常量。"""

QUOTA_CHANNEL_ID = 1545664527410929745
DEFAULT_API_BASE = "https://catiecli.sukaka.top"

PLAYER_COUNT = 5
BET_AMOUNT = 5
FEE_AMOUNT = 1  # 赌大小手续费销毁 1 点
JOIN_TIMEOUT_SECONDS = 120
API_TIMEOUT_SECONDS = 15
COOLDOWN_SECONDS = 10

TRIGGER_KEYWORD = "赌大小"
BANKER_KEYWORD = "当庄家"
BANKER_RUN_KEYWORD = "跑路"
BANKER_MIN_QUOTA = 50  # 当庄家最低额度
BEG_KEYWORD = "乞讨"
BEG_RECEIVE = 10
BEG_COST = 12
BEG_TIMEOUT_SECONDS = 60
BEG_COOLDOWN_SECONDS = 60

DUEL_KEYWORD = "决斗"
DUEL_MIN_QUOTA = 10  # 双方均需 ≥ 10 点才有决斗资格
DUEL_FEE_PERCENT = 20  # 赢家奖金手续费比例（%）
DUEL_TIMEOUT_SECONDS = 60
DUEL_COOLDOWN_SECONDS = 30

RED_PACKET_KEYWORD = "红包"
RED_PACKET_MIN_COST = 10  # 红包最低成本
RED_PACKET_COST_PERCENT = 10  # 红包成本为发送者额度的百分比
RED_PACKET_FEE_PERCENT = 20  # 红包销毁比例（%）
RED_PACKET_MAX_GRABBERS = 8
PACKET_TIMEOUT_SECONDS = 60  # 所有红包统一超时（普通/大/自爆）
RED_PACKET_COOLDOWN_SECONDS = 30

ROB_KEYWORD = "抢劫"
ROB_MIN_QUOTA = 10  # 低于此额度无抢劫能力
ROB_FEE_MIN_PERCENT = 1  # 抢劫成功随机销毁比例下限（%）
ROB_FEE_MAX_PERCENT = 50  # 抢劫成功随机销毁比例上限（%）
ROB_COOLDOWN_SECONDS = 60
ROB_MIN_PERCENT = 10  # 百分比伤害下限（%）
ROB_MAX_PERCENT = 30  # 百分比伤害上限（%）

MARRY_KEYWORD = "结婚"
MARRY_MIN_FEE = 10  # 手续费最低 10 点
MARRY_FEE_PERCENT = 10  # 手续费为两人总额度的百分比
MARRY_TIMEOUT_SECONDS = 60
MARRY_COOLDOWN_SECONDS = 300

CURSE_KEYWORD = "诅咒"
CURSE_COST = 10  # 诅咒费用，全销毁
CURSE_COOLDOWN_SECONDS = 300

ALLIN_KEYWORD = "梭哈"
ALLIN_MIN_QUOTA = 10  # 额度 ≥ 10 点才能梭哈
ALLIN_FEE_PERCENT = 20  # 成功后从奖金扣除的手续费比例（%）
ALLIN_COOLDOWN_SECONDS = 60

LEADERBOARD_KEYWORD = "排行榜"
LEADERBOARD_TOP_N = 10

GACHA_KEYWORD = "抽卡"
GACHA_MIN_COST = 10  # 抽卡最低消耗
GACHA_COST_PERCENT = 10  # 抽卡消耗为额度的百分比
GACHA_COOLDOWN_SECONDS = 10
GACHA_BLANK_CHANCE = 0.4  # 空白卡概率 40%
GACHA_DB = "gacha.db"
GACHA_SEDUCE_SUCCESS_CHANCE = 0.5  # 诱惑成功概率
GACHA_SELFDESTRUCT_MIN_PERCENT = 25  # 自爆销毁比例下限（%）
GACHA_SELFDESTRUCT_MAX_PERCENT = 50  # 自爆销毁比例上限（%）
GACHA_ERROR_MIN = 1  # 错误卡重置额度下限
GACHA_ERROR_MAX = 1000  # 错误卡重置额度上限
GACHA_SELLOUT_PRICE = 200  # 变卖家产：每张道具卡牌卖价
INFLATION_MIN_BALANCE = 5000  # 通货膨胀：触发所需的最低存款门槛（存在存款 > 此值的用户才生效）
CURSE_EYE_DESTROY_CHANCE = 0.4444  # 诅咒之眼：每次诅咒后 44.44% 概率销毁
CURSE_EYE_QUOTA_MIN = 0  # 诅咒之眼：目标额度随机变化下限
CURSE_EYE_QUOTA_MAX = 1000  # 诅咒之眼：目标额度随机变化上限
BLESS_KEYWORD = "祝福"
DIVINITY_EXHAUST_CHANCE = 0.5  # 神性：每次祝福后 50% 概率神力耗尽
BLESS_ALLIN_SUCCESS_CHANCE = 0.75  # 祝福：梭哈成功率提升目标
GACHA_WISHING_TIMEOUT_SECONDS = 60  # 许愿池选择时限（秒），超时视为放弃
GACHA_NOTYET_RECOVER = 50  # 时候未到！恢复额度
METEOR_DISSIPATE_CHANCE = 0.2222  # 流星雨：每次掉落后星光消散的概率
SEDUCE_KEYWORD = "诱惑"
YOURNAME_KEYWORD = "你的名字"
YOURNAME_SWAP_SECONDS = 300  # 交换身体持续时间（秒）
MY_CARDS_KEYWORD = "我的卡牌"
D6_KEYWORD = "D6"
D6_RECHARGE_COST = 66  # D6：每次使用的充能额度
OFFLINE_KEYWORD = "下线"
OFFLINE_MIN_QUOTA = 2000  # 使用下线卡牌的最低额度要求
OFFLINE_RESET_QUOTA = 400  # 下线后额度重置值

BLACK_MARKET_KEYWORD = "黑市"
BLACK_MARKET_DB = "black_market.db"
BLACK_MARKET_SLOTS = 5  # 黑市货架商品种数
BLACK_MARKET_PRICE_MIN = 100  # 单件价格下限
BLACK_MARKET_PRICE_MAX = 500  # 单件价格上限
BLACK_MARKET_STOCK_MIN = 1  # 单种商品数量下限
BLACK_MARKET_STOCK_MAX = 5  # 单种商品数量上限
BLACK_MARKET_TIMEOUT_SECONDS = 60  # 黑市界面超时（秒）
BLACK_MARKET_EXCLUDED_CARDS = {"inflation", "depositking", "weak", "blank", "wishingpool", "selfdestruct"}  # 黑市不出售的卡牌（通货膨胀/存为王/虚弱/空白/许愿池/自爆）
BLACK_MARKET_EXCLUDED_KINDS = frozenset({"unique"})  # 黑市不出售的物品种类（unique=唯一道具）

BANK_KEYWORD = "存钱"
BANK_WITHDRAW_KEYWORD = "取钱"
BANK_BALANCE_KEYWORD = "我的钱"
BANK_MIN_DEPOSIT = 10  # 最低起存金额
BANK_DEPOSIT_PERCENT = 50  # 存入额度比例（%）
BANK_WITHDRAW_MIN_PERCENT = 10  # 取钱扣除比例下限（%）
BANK_WITHDRAW_MAX_PERCENT = 50  # 取钱扣除比例上限（%）
BANK_DB = "bank.db"
BANK_SECURITY_THRESHOLD = 1000  # 存款超过此值解锁普通安保：无法被抢劫
BANK_ROYAL_SECURITY_THRESHOLD = 2000  # 存款超过此值解锁皇家安保：无法被抢劫、诱惑、劫富济贫

BANK_HEIST_KEYWORD = "抢银行"
BANK_HEIST_TEAM_SIZE = 3  # 组队人数
BANK_HEIST_JOIN_TIMEOUT_SECONDS = 60  # 组队 timeout
BANK_HEIST_COOLDOWN_SECONDS = 60  # 个人冷却
BANK_HEIST_TARGET_COOLDOWN_SECONDS = 300  # 目标被抢冷却
BANK_HEIST_MIN_BALANCE = 500  # 目标最低存款
BANK_HEIST_MIN_TARGETS = 1  # 最少目标数
BANK_HEIST_MAX_TARGETS = 10  # 最多目标数
BANK_HEIST_BASE_SUCCESS = 5  # 基础成功率（%）
BANK_HEIST_PROFIT_SHARE = 70  # 队员分配比例（%）
BANK_HEIST_GEAR_NAMES = {"knife": "跑刀", "gun": "起枪", "armor": "全甲"}
BANK_HEIST_GEAR_COST = {"knife": 10, "gun": 50, "armor": 100}  # 装备固定投入（点），同时作为最低额度门槛
BANK_HEIST_GEAR_COEFFICIENTS = {"knife": 1, "gun": 5, "armor": 10}  # 收益系数
BANK_HEIST_GEAR_SUCCESS_BONUS = {"knife": 5, "gun": 15, "armor": 30}  # 成功率加成（%）
BANK_HEIST_AUTO_INTERVAL_SECONDS = 300  # 自动发送抢银行邀请的间隔（秒）

BANK_LOAN_KEYWORD = "贷款"
BANK_LOAN_AMOUNT = 50  # 贷款额度
BANK_LOAN_REPAY = 60  # 需还款额度
BANK_LOAN_LENDER_GAIN = 55  # 借款账号实收
BANK_LOAN_FEE = 5  # 手续费（销毁）
BANK_LOAN_MIN_LENDER_BALANCE = 100  # 借款账号最低存款

LOTTERY_KEYWORD = "买彩票"
LOTTERY_COST = 10  # 彩票单价
LOTTERY_WIN_CHANCE = 0.05  # 中奖概率 5%
LOTTERY_POOL_CONTRIBUTE = 8  # 未中奖进入奖池的额度（剩余 2 点销毁）
LOTTERY_BASE_POOL = 50  # 奖池基础额度
LOTTERY_DB = "lottery.db"

RULES_KEYWORD = "规则"

BIG_RED_PACKET_INTERVAL_SECONDS = 300  # 机器人每 5 分钟发一次大红包
BIG_RED_PACKET_POOL = 500  # 奖池 500 点
BIG_RED_PACKET_OPTIONS_COUNT = 3  # 人机验证选项数（仅 1 个正确）

# 掉落
QUOTA_DROP_KEYWORD = "来财"  # 触发掉落的关键词
QUOTA_DROP_DB = "quota_drops.db"  # 掉落冷却数据库
QUOTA_DROP_MAX = 100  # 单次掉落点数上限
QUOTA_DROP_ZERO_CHANCE = 0.3  # 掉落 0 点的概率（0-1），剩余概率掉 1-100 均匀随机
QUOTA_DROP_DEDUCT_CHANCE = 0.1  # 触发扣减事件的概率（0-1）
QUOTA_DROP_DEDUCT_MIN = 1  # 单次扣减点数下限
QUOTA_DROP_DEDUCT_MAX = 100  # 单次扣减点数上限
QUOTA_DROP_COOLDOWN_MIN_SECONDS = 30  # 单用户掉落冷却下限（秒）
QUOTA_DROP_COOLDOWN_MAX_SECONDS = 180  # 单用户掉落冷却上限（秒）
QUOTA_DROP_NOTIFY_DELETE_AFTER = 10  # 掉落通知自动删除时间（秒）
QUOTA_DROP_MIN_SEND_INTERVAL = 0.5  # 批量通知最小发送间隔（秒）
QUOTA_DROP_MAX_BATCH_CHARS = 1800  # 批量通知单条消息字符上限（Discord 限 2000，留余量）
