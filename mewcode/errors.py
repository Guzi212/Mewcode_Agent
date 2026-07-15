"""MewCode 统一异常类型。"""


class MewCodeError(Exception):
    """所有 MewCode 自定义异常的基类。"""


class ConfigError(MewCodeError):
    """配置文件缺失、格式错误或字段非法。"""


class ProviderError(MewCodeError):
    """Provider 装配或协议相关错误。"""


class SandboxError(MewCodeError):
    """系统级沙箱不可用或拒绝执行。"""
