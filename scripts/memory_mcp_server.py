#!/usr/bin/env python3
"""
memory_mcp_server.py
myScope MCP Server — 让 Claude Code 在对话中自动查询个人记忆
暴露两个 tool：
  - search_memory : Meilisearch 关键词搜索（第一层 + 第三层）
  - recall_memory : Anda Hippocampus 图谱召回（深度上下文）
"""

import os
import sys
import json
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

MEMORY_API_BASE = os.getenv("MEMORY_API_BASE", "https://memory.arjo.us.ci")
MEMORY_API_TOKEN = os.getenv("MEMORY_API_TOKEN", "")

HEADERS = {"Authorization": f"Bearer {MEMORY_API_TOKEN}"}

server = Server("myScope-memory")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memory",
            description=(
                "搜索用户的个人记忆库。"
                "index=memory_chunks 搜索第一层（flomo/微信/Obsidian 切片的个人事实记忆）；"
                "index=wiki_entries 搜索第二层（LLM 归纳的结构化知识条目）；"
                "index=hubble_radius 搜索第三层（RSS + 公众号，即「哈勃半径」）。"
                "当用户询问自己最近做了什么、某个话题自己了解多少、或想知道关注源里有无相关内容时使用。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索词，支持中英文自然语言"
                    },
                    "index": {
                        "type": "string",
                        "enum": ["memory_chunks", "wiki_entries", "hubble_radius"],
                        "description": "memory_chunks=个人事实记忆，wiki_entries=结构化知识，hubble_radius=信息宇宙",
                        "default": "memory_chunks"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 10",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="recall_memory",
            description=(
                "从 Anda Hippocampus 知识图谱召回用户的结构化记忆。"
                "适合需要理解用户项目背景、长期偏好、决策历史等深度上下文的场景。"
                "响应较慢（约 30-60 秒），仅在需要深度背景时调用。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询语句，自然语言描述想了解的内容"
                    }
                },
                "required": ["query"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "search_memory":
        query = arguments["query"]
        index = arguments.get("index", "memory_chunks")
        limit = arguments.get("limit", 10)

        try:
            resp = requests.get(
                f"{MEMORY_API_BASE}/search",
                params={"q": query, "index": index, "limit": limit},
                headers=HEADERS,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError:
            return [types.TextContent(type="text", text="[memory-api 暂不可用，请检查 Mac mini 上的服务是否运行]")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"[search_memory 错误] {e}")]

        total = data.get("total", 0)
        results = data.get("results", [])

        if not results:
            return [types.TextContent(type="text", text=f"在 {index} 中未找到与「{query}」相关的内容。")]

        lines = [f"**{index} 搜索结果**：共 {total} 条，显示前 {len(results)} 条\n"]
        for i, r in enumerate(results, 1):
            text = r.get("text", "").strip()[:300]
            source = r.get("source", "")
            date = r.get("date", "")
            meta = " | ".join(filter(None, [source, date]))
            lines.append(f"{i}. {text}")
            if meta:
                lines.append(f"   _来源：{meta}_")
            lines.append("")

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "recall_memory":
        query = arguments["query"]

        try:
            resp = requests.get(
                f"{MEMORY_API_BASE}/recall",
                params={"q": query},
                headers=HEADERS,
                timeout=90
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError:
            return [types.TextContent(type="text", text="[memory-api 暂不可用，请检查 Mac mini 上的服务是否运行]")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"[recall_memory 错误] {e}")]

        content = data.get("result", {}).get("content", "") or data.get("content", "")
        if not content:
            return [types.TextContent(type="text", text="（Anda 暂无相关记忆）")]

        return [types.TextContent(type="text", text=f"## 知识图谱记忆召回\n\n{content}")]

    else:
        return [types.TextContent(type="text", text=f"未知 tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
