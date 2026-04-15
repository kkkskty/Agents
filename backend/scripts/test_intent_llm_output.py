"""一次性脚本：打印意图拆分大模型原始返回与 decompose 最终结果。"""
import json
import sys

from app.agents.intent_router import IntentRouterAgent
from app.core.settings import load_settings


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "查询我的订单"
    r = IntentRouterAgent()
    settings = load_settings()
    prompt = (
        "你是客服任务拆分器。请输出 JSON："
        '{"tasks":[{"text":"子问题","intent":"query|rule|order|unknown"}]}。'
        "将用户输入按语义拆成多个可独立执行的问题；如果只有一个问题就返回一个。"
        f"用户输入：{text}"
    )
    print("INTENT_AGENT use_local=", settings.intent_agent_llm.use_local)
    print("--- 大模型原始文本（拆分任务）---")
    try:
        raw = r.llm.invoke_text(prompt)
        print(repr(raw))
        print("--- 解析后 JSON ---")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print("调用失败:", type(e).__name__, e)

    print("--- decompose_sub_tasks 最终结果（含本地修正）---")
    tasks = r.decompose_sub_tasks(text)
    for t in tasks:
        print(t.id, t.intent, repr(t.text))


if __name__ == "__main__":
    main()
