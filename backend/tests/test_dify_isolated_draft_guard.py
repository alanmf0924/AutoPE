"""Offline regression checks for the isolated Dify draft's two code nodes."""

import json
from pathlib import Path


GRAPH_PATH = Path(__file__).resolve().parents[1] / "backups" / "dify-candidate-draft-after-case4-case10-20261014.json"
GRAPH = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
NODES = {node["id"]: node["data"] for node in GRAPH["nodes"]}
PREFIX = "DIFY_SELECTION_V1\n"


def run_code(node_id, **kwargs):
    namespace = {}
    exec(compile(NODES[node_id]["code"], node_id, "exec"), namespace)
    return namespace["main"](**kwargs)


def envelope(facts, status="ok"):
    return PREFIX + json.dumps({"facts": facts, "status": status}, ensure_ascii=False)


def test_generic_skincare_shopping_requests_clarification():
    plan = envelope({"intent": "out_of_scope", "early_result": {"answer": "抱歉，我只能协助护肤选购。"}})
    result = run_code("1791959851729", arg1=plan, arg2="想买一套护肤品，给我推荐。")
    assert "肤质" in result["result"]
    assert "预算" in result["result"]
    assert "抱歉" not in result["result"]


def test_other_intent_is_not_overridden():
    plan = envelope({"intent": "out_of_scope", "early_result": {"answer": "请联系订单客服。"}})
    result = run_code("1791959851729", arg1=plan, arg2="想买护肤品但订单退款怎么办？")
    assert result["result"] == "请联系订单客服。"


def guard(query, items, products, evidence, available=None, evidence_status="ok"):
    return run_code(
        "1791976746555",
        query=query,
        bundle_text=envelope({"bundle": {"items": items}}),
        availability_text=envelope({"available_sku_ids": available if available is not None else [p["sku_id"] for p in products], "products": products}),
        evidence_text=envelope({"evidence": evidence}, status=evidence_status),
    )["result"]


ITEMS = [{"sku_id": "A"}, {"sku_id": "B"}]
PRODUCTS = [
    {"sku_id": "A", "approved": True, "on_sale": True, "stock": 2},
    {"sku_id": "B", "approved": True, "on_sale": True, "stock": 3},
]
EVIDENCE = [
    {"sku_id": "A", "source_chunk_id": "a#1", "quote": "保湿商品审核资料"},
    {"sku_id": "B", "source_chunk_id": "b#1", "quote": "屏障修护商品审核资料"},
]


def test_complete_audited_bundle_is_allowed():
    assert guard("干皮，预算300元，给我一个保湿修护搭配。", ITEMS, PRODUCTS, EVIDENCE) == "ALLOW"


def test_single_item_cannot_pass_as_bundle():
    assert guard("干皮，预算300元，给我一个保湿修护搭配。", ITEMS[:1], PRODUCTS, EVIDENCE) == "BLOCK"


def test_missing_repair_quote_is_blocked():
    assert guard("保湿修护搭配", ITEMS, PRODUCTS, EVIDENCE[:1]) == "BLOCK"


def test_missing_stock_or_bad_evidence_status_is_blocked():
    assert guard("修护产品", ITEMS[:1], PRODUCTS, EVIDENCE, available=["B"]) == "BLOCK"
    assert guard("修护产品", ITEMS[:1], PRODUCTS, EVIDENCE, evidence_status="error") == "BLOCK"
