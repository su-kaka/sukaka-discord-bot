"""赌大小及活动额度小游戏包。

入口为 start_roulette(bot)，向 bot 注册游戏区频道的消息处理器：
赌大小、乞讨、决斗、红包、抢劫、结婚、诅咒、梭哈、排行榜、
机器人大红包定时发送，以及发言掉落。
"""

from roulette.handlers import start_roulette

__all__ = ["start_roulette"]
