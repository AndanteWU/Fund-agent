"""Core checking and review logic for the Fund DCA Decision Support Agent."""


EMOTION_AFFECTED_OPTIONS = {"轻微影响", "明显影响", "因为上涨怕错过", "因为亏损感到焦虑"}
CONVERSATION_KEYWORDS = ["加仓", "补仓", "跌", "亏", "涨", "怕错过"]
REVIEW_EMOTION_OPTIONS = {"轻微影响", "明显影响", "因为上涨怕错过", "因为亏损感到焦虑", "因为下跌想补仓"}
VAGUE_REASON_WORDS = ["感觉", "随便", "看情况"]
CLEAR_REASON_WORDS = ["计划", "现金流", "长期", "预算", "目标"]
OPERATION_TYPES = ["计划内定投", "临时加仓", "暂停定投", "减少定投金额", "其他"]


def generate_operation_check(
    operation_type,
    is_in_plan,
    market_emotion,
    cash_flow_effect,
    accept_drawdown,
):
    """Generate restrained pre-operation reminders without buy/sell advice."""
    reminders = []

    if operation_type in {"计划内定投", "定投"} and is_in_plan == "是":
        reminders.append("这次操作属于原计划内的纪律性操作，可以重点关注是否按既定节奏执行。")
    else:
        reminders.append("这次操作不完全属于原计划内，建议先确认它是否仍然符合你的长期投资目标。")

    if market_emotion == "轻微影响":
        reminders.append("本次操作存在轻微短期波动影响，建议把操作理由写清楚，确认它仍符合原计划。")
    elif market_emotion in EMOTION_AFFECTED_OPTIONS:
        reminders.append("本次操作可能受到较明显的短期情绪影响，建议把操作理由写清楚，避免只被当下涨跌牵动。")

    if market_emotion == "因为下跌想补仓":
        reminders.append("如果是因为下跌想补仓，建议确认补仓金额是否在自己可承受的范围内。")

    if cash_flow_effect in {"可能会", "会"}:
        reminders.append("本次操作可能影响日常现金流，建议优先保证生活资金和应急资金安全。")

    if accept_drawdown == "不能接受":
        reminders.append("如果无法接受操作后继续下跌 10%，说明当前操作金额可能偏高，需要更谨慎地评估承受能力。")
    elif accept_drawdown == "不太确定":
        reminders.append("如果对继续下跌 10% 的承受能力不确定，建议先降低操作冲动，重新检查资金安排。")

    reminders.append("以上内容只用于辅助决策、纪律检查、风险提醒和行为复盘，不预测市场涨跌，也不构成直接操作结论。")
    return reminders


def generate_conversation_check(user_text):
    """Check a natural-language operation idea with simple keyword matching."""
    text = (user_text or "").strip()
    matched_keywords = [keyword for keyword in CONVERSATION_KEYWORDS if keyword in text]
    reminders = []

    if not text:
        return {
            "matched_keywords": [],
            "reminders": ["请先输入一段操作想法，Agent 会基于关键词做纪律检查。"],
        }

    if "加仓" in text or "补仓" in text:
        reminders.append("你提到了加仓或补仓，建议先确认这笔金额是否在原计划和可承受范围内。")

    if "跌" in text:
        reminders.append("你提到了下跌，建议区分这是计划内执行，还是被短期波动触发的临时决定。")

    if "亏" in text:
        reminders.append("你提到了亏损，建议留意焦虑感是否正在影响操作节奏。")

    if "涨" in text:
        reminders.append("你提到了上涨，建议检查这次想法是否带有追涨或临时改变计划的倾向。")

    if "怕错过" in text:
        reminders.append("你提到了怕错过，这通常带有较强情绪信号，建议先回到长期目标和原定计划。")

    if not reminders:
        reminders.append("这段想法没有触发明显关键词。你仍可以检查：是否符合原计划、是否影响现金流、是否能承受继续波动。")

    reminders.append("本检查仅基于关键词做纪律检查和风险提醒，不预测市场涨跌，也不输出直接操作结论。")

    return {
        "matched_keywords": matched_keywords,
        "reminders": reminders,
    }


def calculate_amount_ratio(total_amount, monthly_amount):
    """Calculate actual amount divided by planned monthly amount safely."""
    if not monthly_amount or monthly_amount <= 0:
        return None
    return total_amount / monthly_amount


def analyze_plan_execution(total_amount, monthly_amount):
    """Analyze whether actual amount is close to the plan amount."""
    ratio = calculate_amount_ratio(total_amount, monthly_amount)

    if ratio is None:
        summary = "当前基金计划中的每月定投金额为 0 或未填写，暂时无法计算执行比例。"
        level = "info"
        band = "信息不足"
    elif 0.8 <= ratio <= 1.2:
        summary = "本期实际操作金额与每月定投金额较接近，整体基本符合原定计划。"
        level = "success"
        band = "贴合计划"
    elif ratio > 2:
        summary = "本期实际投入明显高于原计划，建议复盘计划外操作和资金来源是否充分。"
        level = "warning"
        band = "显著高于计划"
    elif ratio < 0.5:
        summary = "本期实际操作金额明显低于每月定投金额，定投执行可能不足。"
        level = "warning"
        band = "低于计划"
    else:
        summary = "本期实际操作金额与原计划存在一定偏差，建议结合操作理由复盘执行一致性。"
        level = "info"
        band = "轻度偏离"

    return {
        "monthly_amount": monthly_amount,
        "total_amount": total_amount,
        "ratio": ratio,
        "summary": summary,
        "level": level,
        "band": band,
    }


def analyze_out_plan(total_count, out_plan_count):
    """Analyze plan-outside operation frequency."""
    ratio = out_plan_count / total_count if total_count else 0

    if out_plan_count == 0:
        summary = "本期没有计划外操作，纪律执行情况较好。"
        level = "success"
    elif out_plan_count >= 2 or ratio >= 0.4:
        summary = "本期计划外操作占比较高，操作节奏较依赖临时决策，建议重点复盘原因。"
        level = "warning"
    else:
        summary = "本期存在少量计划外操作，建议确认这些操作是否仍服务于长期目标。"
        level = "info"

    return {
        "count": out_plan_count,
        "ratio": ratio,
        "summary": summary,
        "level": level,
    }


def analyze_emotions(transactions):
    """Analyze short-term emotion signals in operation records."""
    emotion_counts = {
        "因为上涨怕错过": 0,
        "因为亏损感到焦虑": 0,
        "因为下跌想补仓": 0,
    }

    for item in transactions:
        market_emotion = item.get("market_emotion")
        if market_emotion in emotion_counts:
            emotion_counts[market_emotion] += 1

    total_emotion_count = sum(emotion_counts.values())

    if total_emotion_count == 0:
        summary = "本期没有明显情绪影响记录，操作相对理性。"
        level = "success"
    elif emotion_counts["因为下跌想补仓"] > 0 and total_emotion_count == emotion_counts["因为下跌想补仓"]:
        summary = "本期出现下跌后补仓相关操作。它不一定代表错误，但需要区分计划内安排和短期波动反应。"
        level = "info"
    else:
        summary = "本期存在可能受短期情绪影响的操作，建议关注上涨怕错过、亏损焦虑等信号。"
        level = "warning"

    return {
        "total_count": total_emotion_count,
        "counts": emotion_counts,
        "summary": summary,
        "level": level,
    }


def analyze_reason_quality(transactions):
    """Analyze whether operation reasons are specific enough for review."""
    empty_or_short_count = 0
    vague_count = 0
    clear_count = 0

    for item in transactions:
        reason = (item.get("reason") or "").strip()

        if len(reason) < 8:
            empty_or_short_count += 1

        if any(word in reason for word in VAGUE_REASON_WORDS):
            vague_count += 1

        if any(word in reason for word in CLEAR_REASON_WORDS):
            clear_count += 1

    total_count = len(transactions)

    if total_count == 0:
        summary = "暂无操作理由记录，暂时无法判断记录质量。"
        level = "info"
    elif empty_or_short_count >= max(1, total_count / 2):
        summary = "本期较多操作理由为空或过短，记录质量不足，后续复盘依据会偏弱。"
        level = "warning"
    elif vague_count > 0 and clear_count == 0:
        summary = "本期部分理由包含较模糊表达，建议记录更具体的计划、预算或现金流依据。"
        level = "warning"
    elif clear_count > 0 and empty_or_short_count == 0:
        summary = "本期操作理由中能看到计划、现金流、长期目标或预算等线索，记录相对清晰。"
        level = "success"
    else:
        summary = "本期操作理由有一定信息量，但仍可继续提高具体程度。"
        level = "info"

    return {
        "empty_or_short_count": empty_or_short_count,
        "vague_count": vague_count,
        "clear_count": clear_count,
        "summary": summary,
        "level": level,
    }


def count_by_operation_type(transactions):
    """Count operation records by operation type."""
    counts = {operation_type: 0 for operation_type in OPERATION_TYPES}
    for item in transactions:
        operation_type = item.get("operation_type") or "其他"
        if operation_type not in counts:
            counts[operation_type] = 0
        counts[operation_type] += 1
    return counts


def amount_by_operation_type(transactions):
    """Sum operation amount by operation type."""
    amounts = {operation_type: 0.0 for operation_type in OPERATION_TYPES}
    for item in transactions:
        operation_type = item.get("operation_type") or "其他"
        if operation_type not in amounts:
            amounts[operation_type] = 0.0
        amounts[operation_type] += float(item.get("amount", 0) or 0)
    return amounts


def build_timeline(transactions):
    """Build cumulative amount timeline sorted by operation date."""
    sorted_items = sorted(transactions, key=lambda item: item.get("operation_date", ""))
    labels = []
    amounts = []
    cumulative_amounts = []
    cumulative = 0.0

    for index, item in enumerate(sorted_items, start=1):
        amount = float(item.get("amount", 0) or 0)
        cumulative += amount
        labels.append(item.get("operation_date") or f"第 {index} 次")
        amounts.append(amount)
        cumulative_amounts.append(cumulative)

    return {
        "labels": labels,
        "amounts": amounts,
        "cumulative_amounts": cumulative_amounts,
    }


def analyze_risk_checks(transactions):
    """Analyze cash-flow and drawdown answers from pre-operation checks."""
    cash_flow_warning_count = sum(
        1 for item in transactions if item.get("cash_flow_effect") in {"可能会", "会"}
    )
    drawdown_warning_count = sum(
        1 for item in transactions if item.get("accept_drawdown") in {"不太确定", "不能接受"}
    )

    if cash_flow_warning_count == 0 and drawdown_warning_count == 0:
        summary = "本期记录中没有明显现金流或回撤承受压力信号。"
        level = "success"
    elif cash_flow_warning_count > 0 and drawdown_warning_count > 0:
        summary = "本期同时出现现金流和回撤承受压力信号，建议优先复盘资金安全边界。"
        level = "warning"
    else:
        summary = "本期出现局部资金承受能力提示，建议在操作前继续确认现金流和回撤边界。"
        level = "info"

    return {
        "cash_flow_warning_count": cash_flow_warning_count,
        "drawdown_warning_count": drawdown_warning_count,
        "summary": summary,
        "level": level,
    }


def calculate_behavior_score(total_count, out_plan_analysis, emotion_analysis, reason_quality, risk_checks):
    """Create a simple behavior score for review display, not for investment advice."""
    if total_count == 0:
        return {
            "score": None,
            "summary": "暂无操作记录，暂不生成纪律评分。",
            "level": "info",
        }

    score = 100
    score -= int(out_plan_analysis["ratio"] * 30)
    score -= emotion_analysis["total_count"] * 10
    score -= reason_quality["empty_or_short_count"] * 8
    score -= reason_quality["vague_count"] * 5
    score -= risk_checks["cash_flow_warning_count"] * 8
    score -= risk_checks["drawdown_warning_count"] * 6
    score = max(0, min(100, score))

    if score >= 80:
        summary = "本期纪律执行较稳，主要风险来自个别操作的记录质量或临时判断。"
        level = "success"
    elif score >= 60:
        summary = "本期纪律表现中等，建议重点关注计划外操作、理由记录和资金承受边界。"
        level = "info"
    else:
        summary = "本期纪律信号偏弱，建议先提升记录完整度并减少临时性操作。"
        level = "warning"

    return {
        "score": score,
        "summary": summary,
        "level": level,
    }


def build_next_suggestions(plan_execution, out_plan_analysis, emotion_analysis, reason_quality, risk_checks):
    """Build 3 to 5 restrained behavior suggestions for the next period."""
    suggestions = []

    if plan_execution["ratio"] is None:
        suggestions.append("补充或校准每月定投金额，让后续复盘有清晰参照。")
    elif plan_execution["ratio"] < 0.8 or plan_execution["ratio"] > 1.2:
        suggestions.append("下期先对照原定定投计划，再决定是否需要调整执行节奏。")
    else:
        suggestions.append("继续保持围绕原定定投计划执行的习惯。")

    if out_plan_analysis["count"] > 0:
        suggestions.append("对计划外操作先记录原因和资金来源，避免临时决策过多。")
    else:
        suggestions.append("继续保持较少计划外操作的纪律。")

    if emotion_analysis["total_count"] > 0:
        suggestions.append("操作前先识别上涨怕错过、亏损焦虑、下跌想补仓等短期情绪信号。")
    else:
        suggestions.append("继续在操作前区分长期计划和短期波动。")

    if reason_quality["level"] == "warning":
        suggestions.append("每次操作至少写清楚目标、预算、现金流或计划依据。")
    else:
        suggestions.append("继续保留操作理由，方便后续复盘投资纪律。")

    if risk_checks["cash_flow_warning_count"] > 0 or risk_checks["drawdown_warning_count"] > 0:
        suggestions.append("下期操作前优先确认日常现金流、应急资金和回撤承受能力。")
    else:
        suggestions.append("继续把现金流安全和回撤承受能力作为操作前检查项。")

    return suggestions[:5]


def generate_review_report(transactions, plan=None):
    """Generate a structured behavior review report for one fund plan."""
    plan = plan or {}
    fund_name = plan.get("fund_name") or "当前基金"
    monthly_amount = float(plan.get("monthly_amount", 0) or 0)

    total_count = len(transactions)
    total_amount = sum(float(item.get("amount", 0) or 0) for item in transactions)
    in_plan_count = sum(1 for item in transactions if item.get("operation_type") == "计划内定投")
    out_plan_count = total_count - in_plan_count
    emotion_count = sum(
        1
        for item in transactions
        if item.get("market_emotion") in REVIEW_EMOTION_OPTIONS
    )

    plan_execution = analyze_plan_execution(total_amount, monthly_amount)
    out_plan_analysis = analyze_out_plan(total_count, out_plan_count)
    emotion_analysis = analyze_emotions(transactions)
    reason_quality = analyze_reason_quality(transactions)
    risk_checks = analyze_risk_checks(transactions)
    behavior_score = calculate_behavior_score(
        total_count,
        out_plan_analysis,
        emotion_analysis,
        reason_quality,
        risk_checks,
    )
    suggestions = build_next_suggestions(
        plan_execution,
        out_plan_analysis,
        emotion_analysis,
        reason_quality,
        risk_checks,
    )

    return {
        "overview": {
            "fund_name": fund_name,
            "total_count": total_count,
            "total_amount": total_amount,
            "in_plan_count": in_plan_count,
            "out_plan_count": out_plan_count,
            "emotion_count": emotion_count,
        },
        "plan_execution": plan_execution,
        "out_plan_analysis": out_plan_analysis,
        "emotion_analysis": emotion_analysis,
        "reason_quality": reason_quality,
        "risk_checks": risk_checks,
        "behavior_score": behavior_score,
        "operation_type_counts": count_by_operation_type(transactions),
        "operation_type_amounts": amount_by_operation_type(transactions),
        "timeline": build_timeline(transactions),
        "next_suggestions": suggestions,
    }


