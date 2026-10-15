"""Explicit bounded tool-call loop. Never trusts generated prices or prose as facts."""

import asyncio
from threading import BoundedSemaphore
from time import monotonic

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langsmith import tracing_context
from pydantic import ValidationError

from app.agent.provider import SYSTEM_PROMPT
from app.agent.tools import bounded_json

MAX_TOOLS = 4
MAX_CONTEXT_BYTES = 32000
MODEL_TIMEOUT = 1.0
TOOL_TIMEOUT = 1.5
RUN_TIMEOUT = 6.0
WORKERS = BoundedSemaphore(4)


async def invoke_tool(tool, parsed):
    # A timeout cannot kill a Python worker: retain its slot until it actually exits.
    if not WORKERS.acquire(blocking=False):
        raise HTTPException(429, "目录工具繁忙，请稍后重试", headers={"Retry-After": "2"})
    task = asyncio.create_task(
        asyncio.to_thread(
            tool.invoke,
            parsed.model_dump(),
            config={"callbacks": []},
        )
    )

    def complete(future):
        WORKERS.release()
        if not future.cancelled():
            future.exception()  # Consume exceptions even after the HTTP caller timed out.

    task.add_done_callback(complete)
    return await asyncio.wait_for(asyncio.shield(task), TOOL_TIMEOUT)


async def run(provider, tools, message):
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=message)]
    trace, quotes, candidates = [], [], []
    used, ids = 0, set()
    schemas = [convert_to_openai_tool(tool) for tool in tools.values()]
    try:
        # Explicitly disable external tracing even if the host has tracing environment variables.
        with tracing_context(enabled=False):
            async with asyncio.timeout(RUN_TIMEOUT):
                for _ in range(MAX_TOOLS + 2):
                    if (
                        sum(len(str(item.content).encode()) for item in messages)
                        > MAX_CONTEXT_BYTES
                    ):
                        raise HTTPException(422, "上下文超过本阶段上限")
                    reply = await asyncio.wait_for(
                        provider.ainvoke(messages, schemas), MODEL_TIMEOUT
                    )
                    if len(str(reply.content).encode()) > 2000 or reply.invalid_tool_calls:
                        raise HTTPException(502, "模拟模型返回格式无效")
                    calls = reply.tool_calls
                    if not calls:
                        return finish(trace, quotes, candidates)
                    if used + len(calls) > MAX_TOOLS:
                        raise HTTPException(422, "工具调用次数超过上限")
                    messages.append(reply)
                    for call in calls:
                        used += 1
                        if call["id"] in ids or not call["id"] or len(call["id"]) > 100:
                            raise HTTPException(502, "工具调用标识无效")
                        ids.add(call["id"])
                        tool = tools.get(call["name"])
                        if tool is None:
                            raise HTTPException(422, "模型请求了未授权工具")
                        try:
                            parsed = tool.args_schema.model_validate(call["args"])
                        except ValidationError as exc:
                            raise HTTPException(422, "模型工具参数不合法") from exc
                        started = monotonic()
                        # A new DB session is created inside the worker. No shared ORM session.
                        try:
                            result = await invoke_tool(tool, parsed)
                            encoded = bounded_json(result)
                        except HTTPException as exc:
                            if exc.status_code != 404:
                                raise
                            result = {"error": "PART_NOT_FOUND", "message": "商品不存在或尚未审核"}
                            encoded = bounded_json(result)
                        trace.append(
                            {
                                "name": call["name"],
                                "arguments": parsed.model_dump(mode="json"),
                                "status": "error" if result.get("error") else "ok",
                                "duration_ms": round((monotonic() - started) * 1000),
                                "result": result,
                            }
                        )
                        if call["name"] == "get_reference_price" and not result.get("error"):
                            quotes.append(result)
                        if call["name"] == "search_parts":
                            candidates = result.get("items", [])
                        messages.append(
                            ToolMessage(content=encoded, tool_call_id=call["id"], name=call["name"])
                        )
                raise HTTPException(422, "工具循环超过上限")
    except TimeoutError as exc:
        raise HTTPException(504, "模拟模型或目录工具超时，请稍后重试") from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Never expose model/tool exception details, prompts, credentials or database URLs.
        raise HTTPException(502, "模拟模型或工具执行失败；未生成报价") from exc


def finish(trace, quotes, candidates):
    if any(quote.get("price_usd") is not None for quote in quotes):
        status = "GROUNDED"
        answer = "已从目录读取历史报价，请以来源卡片为准；不是实时市场价格。"
    elif quotes:
        status = "UNQUOTABLE"
        answer = "目录没有已审核选定的报价，不能提供金额。"
    elif candidates:
        status = "NEEDS_SELECTION"
        answer = "找到候选但未取得具体报价，请补充准确型号后查询；不会合并或擅自选价。"
    else:
        status = "NO_PRICE_EVIDENCE"
        answer = "未取得可用报价证据。可能没有匹配商品，或问题超出模拟模式范围；请提供具体型号。"
    result = dict(
        mode="mock",
        status=status,
        answer=answer,
        quotes=quotes,
        candidates=candidates,
        tool_calls=trace,
        historical_notice="USD 历史参考价；数据集日期不是逐商品当天采价。",
        persistence="本阶段不保存聊天；每次查询独立",
    )
    bounded_json(result, 64000)
    return result
