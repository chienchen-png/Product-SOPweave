"""Shared rejection policy for approved shell execution."""

COMMAND_BLACKLIST: list[str] = [
    "format", "shutdown", "del /f /s", "rm -rf /",
    "rd /s /q C:", "diskpart", "reg delete", "taskkill /f /im",
]


def _check_blacklist(command: str) -> str:
    cmd_lower = command.lower()
    for pattern in COMMAND_BLACKLIST:
        if pattern.lower() in cmd_lower:
            return f"命令被安全策略阻止（匹配黑名单: {pattern})"
    return ""
