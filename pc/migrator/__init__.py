"""换机工作台（店内版）PC 端核心库。零第三方依赖。"""

__version__ = "0.1.0"


class Cancelled(Exception):
    """用户取消迁移。"""
