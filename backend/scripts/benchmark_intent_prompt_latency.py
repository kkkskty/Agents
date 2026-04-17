"""
对比不同意图分析 prompt 的大模型耗时（仅测 LLM 调用，不含业务校验）。
用法（在 backend 目录下）:
  python scripts/benchmark_intent_prompt_latency.py
"""
from __future__ import annotations

import statistics
import time

from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings


USER_TEXT = "查询我的订单 帮我把降价的产品重新下单一份"
RUNS = 3


def build_prompt_long(text: str) -> str:
    """与 app.agents.intent_router.IntentRouterAgent.analyze 中 prompt 一致。"""
    return (
        "你是客服系统任务分析器。\n"
        "请输出 JSON，不要输出其他内容。JSON 格式为：\n"
        '{"route":"query|rule|order|complex|unknown","confidence":0-1,"reason":"简短原因","tasks":[{"text":"子任务","intent":"query|rule|order|complex|unknown","depends_on":["task_1"]}]}\n'
        "要求：一次同时完成\"主路由判定 + 子任务拆分\"。\n"
        "输出约束：tasks 必须是非空数组，且每个 task 必须包含 text、intent；字段名必须严格使用 text/intent/depends_on。\n"
        "关键判定规则：如果用户意图是\"先查数据，再基于查到的数据执行动作\"（如先查订单降价商品，再下单），route 必须是 complex，且至少拆成 2 个任务，后任务 depends_on 前任务。\n"
        "禁止输出 unknown 的场景：只要能明确为查询、规则、订单、或其组合，就不能输出 unknown。\n"
        "分类准则：\n"
        "query：信息查询、状态查询、列表查询（包括\"我的订单有哪些/订单状态如何\"）。\n"
        "rule：规则、政策、条款、规范解释，在用户不知道如何处理的时候归为此类，触发关键词可以是\"怎么办？\"\"怎么处理\"类似的。\n"
        "order：需要执行订单动作（下单、退单、退款申请、修改订单信息等）。\n"
        "complex：组合任务，存在\"先查后做\"等依赖或数据传递。\n"
        "unknown：仅在语义完全不可理解时使用。\n"
        "拆分规则：尽量拆成可独立执行子任务；如有\"先查再做\"等依赖，使用 depends_on 指向前序 task_id，更新问题的文本，要说明数据源是什么。\n"
        "示例：\n"
        "\"我的订单有哪些\" → {\"route\":\"query\",\"confidence\":0.95,\"reason\":\"用户查询订单列表\",\"tasks\":[{\"text\":\"查询我的订单列表\",\"intent\":\"query\",\"depends_on\":[]}]}\n"
        "\"订单退单规则是什么\" → {\"route\":\"rule\",\"confidence\":0.96,\"reason\":\"用户询问退单规则\",\"tasks\":[{\"text\":\"查询退单规则\",\"intent\":\"rule\",\"depends_on\":[]}]}\n"
        "\"充值失败怎么办？\" → {\"route\":\"rule\",\"confidence\":0.94,\"reason\":\"用户询问充值失败的处理方式\",\"tasks\":[{\"text\":\"查询充值失败处理规则\",\"intent\":\"rule\",\"depends_on\":[]}]}\n"
        "\"怎么转人工？\" → {\"route\":\"rule\",\"confidence\":0.95,\"reason\":\"用户询问转人工流程\",\"tasks\":[{\"text\":\"查询转人工规则\",\"intent\":\"rule\",\"depends_on\":[]}]}\n"
        "\"查询我的订单，看下那些降价了，帮我重新下单一份？\" → {\"route\":\"complex\",\"confidence\":0.98,\"reason\":\"先查降价商品再下单，存在数据依赖\",\"tasks\":[{\"text\":\"查询我的订单中哪些商品已降价\",\"intent\":\"query\",\"depends_on\":[]},{\"text\":\"基于查询到的降价商品重新下单\",\"intent\":\"order\",\"depends_on\":[\"task_1\"]}]}\n"
        "\"帮我把 ORD-123 退单\" → {\"route\":\"order\",\"confidence\":0.97,\"reason\":\"用户明确指定订单号执行退单\",\"tasks\":[{\"text\":\"将订单 ORD-123 退单\",\"intent\":\"order\",\"depends_on\":[]}]}\n"
        "\"怎么退单\" → {\"route\":\"rule\",\"confidence\":0.95,\"reason\":\"用户询问退单流程规则\",\"tasks\":[{\"text\":\"查询退单规则\",\"intent\":\"rule\",\"depends_on\":[]}]}\n"
        f"用户输入：{text}"
    )


def build_prompt_short(text: str) -> str:
    """极简：同 schema，无长示例与多段规则。"""
    return (
        "你是客服任务分析器，只输出 JSON，不要其他内容。\n"
        "格式："
        '{"route":"query|rule|order|complex|unknown","confidence":0-1,"reason":"简短原因",'
        '"tasks":[{"text":"子任务原文","intent":"query|rule|order|complex|unknown","depends_on":[]}]}。\n'
        "若需多步且后一步依赖前一步，depends_on 填 task_1 等。\n"
        f"用户输入：{text}"
    )


def char_count(s: str) -> int:
    return len(s)


def bench(name: str, llm: LLMRouter, prompt: str) -> list[float]:
    times: list[float] = []
    for i in range(RUNS):
        t0 = time.perf_counter()
        try:
            llm.invoke_json(prompt)
        except Exception as e:
            print(f"  [{name}] run {i + 1} 失败: {e}")
            continue
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        print(f"  [{name}] run {i + 1}: {elapsed:.3f}s")
    return times


def main() -> None:
    settings = load_settings()
    llm = LLMRouter(settings.intent_agent_llm)
    if llm.get_llm() is None:
        raise SystemExit("LLM 未配置（检查 INTENT_AGENT_* 与 .env）")

    p_short = build_prompt_short(USER_TEXT)
    p_long = build_prompt_long(USER_TEXT)

    print("用户句:", USER_TEXT)
    print()
    print(f"短 prompt 字符数: {char_count(p_short)}")
    print(f"长 prompt 字符数: {char_count(p_long)}")
    print(f"RUNS={RUNS}，模型超时: {llm._invoke_timeout_s}s")
    print()

    print("--- 短 prompt ---")
    ts_short = bench("short", llm, p_short)
    print("--- 长 prompt（当前 intent_router） ---")
    ts_long = bench("long", llm, p_long)

    def summarize(label: str, arr: list[float]) -> None:
        if not arr:
            print(f"{label}: 无成功次数")
            return
        print(
            f"{label}: min={min(arr):.3f}s  max={max(arr):.3f}s  "
            f"avg={statistics.mean(arr):.3f}s  median={statistics.median(arr):.3f}s"
        )

    print()
    summarize("短 prompt", ts_short)
    summarize("长 prompt", ts_long)
    if ts_short and ts_long:
        ratio = statistics.mean(ts_long) / statistics.mean(ts_short)
        print(f"平均耗时比（长/短）: {ratio:.2f}x")


if __name__ == "__main__":
    main()
