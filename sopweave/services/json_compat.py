from __future__ import annotations

import ast
import json
import re


def extract_top_block(text: str) -> str:
    starts = [index for index, char in enumerate(text) if char in "{["]
    if not starts:
        return text
    start = starts[0]
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]


def python_literal_to_json(text: str) -> str:
    block = extract_top_block(text)
    try:
        json.loads(block, strict=False)
        return text
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        parsed = ast.literal_eval(block)
        return text.replace(block, json.dumps(parsed, ensure_ascii=False))
    except (SyntaxError, ValueError):
        converted = re.sub(r"'([^']{1,64})'(\s*:)", r'"\1"\2', text)
        return re.sub(r":\s*'([^'\"{}:,]{1,128})'", r': "\1"', converted)
