# def mock_search_knowledge(query: str) -> list[dict]:
#     kb = [
#         {
#             "source": "faq-search",
#             "chunk_id": 1,
#             "snippet": "文具类订单支持 7 天无理由退单，需保持外包装完整。",
#         },
#         {
#             "source": "faq-search",
#             "chunk_id": 2,
#             "snippet": "订单修改支持地址、联系人手机号，发货后不可修改。",
#         },
#     ]
#     if any(k in query for k in ["文具", "订单", "退单", "修改"]):
#         return kb
#     return []
