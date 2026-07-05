from llm_analyzer import analyze_operation_reason

result = analyze_operation_reason(
    reason="最近市场下跌，想补一点仓，但担心这次操作是不是有点冲动。",
    operation_type="临时加仓",
    is_planned="否",
    emotion_reason="因为下跌想补仓",
    cash_flow="不会",
    can_accept_loss="能接受",
)

print(result)