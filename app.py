"""Streamlit web app for the Fund Investor Emotion Management Agent."""

from datetime import date, datetime, timedelta
import calendar
import json
from html import escape

import streamlit as st
import streamlit.components.v1 as components

from auth import get_authenticated_user, logout, render_login_page
from agent_logic import generate_conversation_check, generate_operation_check
from data_manager import (
    add_transaction,
    create_fund_plan,
    delete_fund_plan_with_records,
    ensure_data_files,
    get_selected_plan as load_selected_plan,
    list_fund_plans,
    list_operation_checks,
    load_review_reports,
    set_selected_plan,
    save_review_reports,
    update_fund_plan,
    load_emotion_records,
    upsert_emotion_record,
    delete_emotion_record,
    get_emotion_record_by_date,
    get_last_emotion_records_error,
)
from llm_analyzer import analyze_operation_reason, analyze_daily_emotion


st.set_page_config(page_title="基金投资情绪管理 Agent", page_icon="🧠", layout="wide")

FINAL_DISCLAIMER = "本分析不构成投资建议，不预测市场涨跌，也不提供买入、卖出、加仓、减仓建议。"
QUESTION_FLOW = [
    {"key": "is_planned", "title": "是否符合定投计划？", "options": ["是", "否"]},
    {"key": "emotion_reason", "title": "是否受短期波动影响？", "options": ["无影响", "轻微影响", "明显影响"]},
    {"key": "cash_flow", "title": "是否影响现金流？", "options": ["不会", "可能会", "会"]},
    {"key": "can_accept_loss", "title": "是否接受继续下跌 10%？", "options": ["能接受", "不能接受"]},
]


def get_current_user_id(user=None):
    user = user or get_authenticated_user()
    if not user or not user.get("user_id"):
        return ""
    st.session_state.user_id = user["user_id"]
    st.session_state.email = user.get("email", "")
    return user["user_id"]


def format_money(amount):
    return f"{float(amount or 0):,.2f} 元"


def contains_any(text, keywords):
    text = (text or "").lower()
    return any(keyword.lower() in text for keyword in keywords)


def clamp_score(value):
    return max(0, min(100, round(value)))


def parse_diagnosis_payload(payload):
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload.strip())
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"structure_error": True, "error_message": "AI 返回结构异常，无法展示原始内容。"}
    return {}


def extract_rationality_score(record):
    diagnosis = parse_diagnosis_payload(record.get("behavior_diagnosis"))
    value = diagnosis.get("rationality_score") or diagnosis.get("score")
    if isinstance(value, dict):
        value = value.get("score")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_bias_level(record, bias_name):
    diagnosis = parse_diagnosis_payload(record.get("behavior_diagnosis"))
    for item in diagnosis.get("bias_dimensions", []):
        if item.get("name") == bias_name:
            return item.get("level", "低")
    return "低"


def build_investment_personality(records):
    total = len(records)
    if total == 0:
        return {}
    fomo_keywords = ["怕错过", "追涨", "涨", "感觉会涨", "朋友推荐", "别人都", "大家都", "热门", "赚钱"]
    panic_keywords = ["跌了", "大跌", "下跌", "亏", "亏损", "焦虑", "害怕", "恐慌", "扛不住"]
    long_term_keywords = ["长期", "计划", "定投", "预算", "目标", "现金流", "三年", "五年"]
    vague_keywords = ["感觉", "随便", "看情况", "先试试"]
    out_plan_count = sum(1 for item in records if item.get("is_in_plan") != "是")
    in_plan_count = total - out_plan_count
    adjust_count = sum(1 for item in records if item.get("operation_type") in {"加仓", "减仓", "其他", "临时加仓", "暂停定投", "减少定投金额"})
    emotion_count = sum(1 for item in records if item.get("market_emotion") not in {"无影响", "没有明显影响", "", None})
    fomo_count = sum(1 for item in records if contains_any(item.get("reason", ""), fomo_keywords))
    panic_count = sum(1 for item in records if contains_any(item.get("reason", ""), panic_keywords))
    friend_count = sum(1 for item in records if contains_any(item.get("reason", ""), ["朋友", "同事", "群里", "网上", "大家", "别人"]))
    vague_count = sum(1 for item in records if contains_any(item.get("reason", ""), vague_keywords) or len((item.get("reason") or "").strip()) < 8)
    clear_reason_count = sum(1 for item in records if contains_any(item.get("reason", ""), long_term_keywords))
    cannot_accept_count = sum(1 for item in records if item.get("accept_drawdown") == "不能接受")
    cash_warning_count = sum(1 for item in records if item.get("cash_flow_effect") in {"可能会", "会"})
    high_fomo_bias = sum(1 for item in records if get_bias_level(item, "羊群效应") == "高" or get_bias_level(item, "自豪与悔恨") == "高")
    high_loss_bias = sum(1 for item in records if get_bias_level(item, "损失厌恶") == "高")
    high_confidence_bias = sum(1 for item in records if get_bias_level(item, "过度自信") == "高")
    emotion_ratio = emotion_count / total
    out_plan_ratio = out_plan_count / total
    adjust_ratio = adjust_count / total
    fomo_ratio = min(1, (fomo_count + friend_count + high_fomo_bias) / total)
    panic_ratio = min(1, (panic_count + high_loss_bias) / total)
    vague_ratio = vague_count / total
    clear_ratio = clear_reason_count / total
    risk_warning_ratio = min(1, (cannot_accept_count + cash_warning_count) / total)
    scores = {
        "Emotional Sensitivity": clamp_score(15 + emotion_ratio * 45 + panic_ratio * 30 + high_loss_bias / total * 10),
        "Discipline": clamp_score(95 - out_plan_ratio * 42 - adjust_ratio * 22 - vague_ratio * 18 + clear_ratio * 10),
        "FOMO Tendency": clamp_score(10 + fomo_ratio * 65 + high_confidence_bias / total * 10 + (1 if adjust_count else 0) * 8),
        "Risk Tolerance": clamp_score(88 - risk_warning_ratio * 50 - cannot_accept_count / total * 22 + (1 - panic_ratio) * 8),
        "Long-term Orientation": clamp_score(55 + in_plan_count / total * 28 + clear_ratio * 22 - adjust_ratio * 18 - emotion_ratio * 12),
    }
    return {"scores": scores, "personality_type": classify_personality(scores, out_plan_ratio, adjust_ratio), "risk_level": classify_risk_level(scores), "evidence": build_personality_evidence(total, out_plan_count, adjust_count, emotion_count, fomo_count, panic_count, friend_count, vague_count, clear_reason_count, cannot_accept_count, cash_warning_count), "pattern_summary": build_behavior_pattern_summary(scores), "suggestions": build_personality_suggestions(scores), "stats": {"total": total, "out_plan_count": out_plan_count, "adjust_count": adjust_count, "emotion_count": emotion_count, "fomo_count": fomo_count}}


def classify_personality(scores, out_plan_ratio, adjust_ratio):
    if scores["Discipline"] >= 78 and scores["Long-term Orientation"] >= 72 and scores["Emotional Sensitivity"] <= 45:
        return "理性定投型（Rational DCA Investor）"
    if scores["Discipline"] >= 72 and scores["Long-term Orientation"] >= 78:
        return "纪律型长期投资者（Disciplined Investor）"
    if scores["Emotional Sensitivity"] >= 68 and scores["FOMO Tendency"] >= 55:
        return "情绪驱动型交易者（Emotion-driven Trader）"
    if scores["FOMO Tendency"] >= 65 and scores["Risk Tolerance"] >= 55:
        return "机会捕捉型投资者（Opportunistic Investor）"
    if adjust_ratio >= 0.45 or out_plan_ratio >= 0.5:
        return "高频调整型（Reactive Adjuster）"
    return "平衡观察型投资者（Balanced Observer）"


def classify_risk_level(scores):
    if scores["Emotional Sensitivity"] >= 70 or scores["Risk Tolerance"] <= 42:
        return "高敏感风险人格"
    if scores["FOMO Tendency"] >= 65 and scores["Discipline"] <= 60:
        return "机会冲动风险人格"
    if scores["Discipline"] >= 75 and scores["Risk Tolerance"] >= 65:
        return "稳健纪律风险人格"
    return "中性波动风险人格"


def build_personality_evidence(total, out_plan_count, adjust_count, emotion_count, fomo_count, panic_count, friend_count, vague_count, clear_reason_count, cannot_accept_count, cash_warning_count):
    evidence = [f"历史样本共 {total} 次操作，画像会随着记录增加而更稳定。"]
    evidence.append(f"计划外或未确认计划内操作 {out_plan_count} 次，操作类型调整 {adjust_count} 次。")
    evidence.append(f"短期波动影响操作 {emotion_count} 次，理由中出现下跌、亏损或焦虑信号 {panic_count} 次。")
    evidence.append(f"理由中出现怕错过、朋友推荐、感觉会涨等 FOMO/羊群信号 {fomo_count + friend_count} 次。")
    evidence.append(f"理由较短或偏模糊 {vague_count} 次，包含长期、计划、预算、目标等清晰依据 {clear_reason_count} 次。")
    if cannot_accept_count or cash_warning_count:
        evidence.append(f"现金流或回撤承受压力信号 {cannot_accept_count + cash_warning_count} 次。")
    return evidence[:5]


def build_behavior_pattern_summary(scores):
    return (
        "这个人格画像不是收益预测，而是把多个基金计划下的长期操作记录转化为心理结构。"
        f"纪律性为 {scores['Discipline']}/100，长期主义为 {scores['Long-term Orientation']}/100；"
        f"情绪敏感度为 {scores['Emotional Sensitivity']}/100，FOMO 倾向为 {scores['FOMO Tendency']}/100。"
    )


def build_personality_suggestions(scores):
    suggestions = []
    if scores["Emotional Sensitivity"] >= 60:
        suggestions.append("把看到波动后的第一反应和最终操作理由分开记录。")
    if scores["Discipline"] < 70:
        suggestions.append("为计划外操作写固定模板：触发条件、金额上限、资金来源、复盘日期。")
    if scores["FOMO Tendency"] >= 55:
        suggestions.append("遇到朋友推荐、热门讨论或怕错过时，先记录信息来源，再检查它是否改变了原定目标。")
    if scores["Risk Tolerance"] < 65:
        suggestions.append("每次操作前保留现金流和继续下跌情景检查。")
    if scores["Long-term Orientation"] < 70:
        suggestions.append("把每次操作绑定到长期目标或预算规则。")
    if not suggestions:
        suggestions.append("继续保持操作前问答和理由记录，让长期人格画像更稳定。")
    return suggestions[:5]



def build_monthly_review(records, plan):
    """Build a current-month behavior review for the selected plan only."""
    current_month = datetime.now().strftime("%Y-%m")
    monthly_records = [record for record in records if str(record.get("operation_date", "")).startswith(current_month)]
    total = len(monthly_records)
    planned_amount = float(plan.get("monthly_amount", 0) or 0)
    actual_amount = sum(float(record.get("amount", 0) or 0) for record in monthly_records)
    in_plan_count = sum(1 for record in monthly_records if record.get("is_in_plan") == "是")
    out_plan_count = total - in_plan_count
    reduce_or_pause_count = sum(1 for record in monthly_records if record.get("operation_type") in {"减仓", "暂停定投", "减少定投金额", "赎回"})
    discipline_completion_rate = round(in_plan_count / total * 100, 1) if total else 0

    trigger_keywords = {
        "FOMO": ["怕错过", "追涨", "热门", "别人都", "大家都", "赚钱"],
        "亏损焦虑": ["亏", "亏损", "焦虑", "害怕", "恐慌"],
        "下跌补仓冲动": ["跌", "下跌", "补仓", "摊平", "回本"],
        "羊群影响": ["朋友", "同事", "群里", "网上", "大家", "别人"],
        "新闻刺激": ["新闻", "政策", "消息", "热搜", "媒体"],
        "现金流压力": ["现金流", "生活费", "还款", "工资", "应急"],
    }
    emotion_triggers = {name: 0 for name in trigger_keywords}
    for record in monthly_records:
        reason = record.get("reason", "") or ""
        market_emotion = record.get("market_emotion", "") or ""
        cash_flow = record.get("cash_flow_effect", "") or ""
        for name, keywords in trigger_keywords.items():
            if any(keyword in reason for keyword in keywords):
                emotion_triggers[name] += 1
        if market_emotion == "明显影响":
            emotion_triggers["FOMO"] += 1
        if cash_flow in {"可能会", "会"}:
            emotion_triggers["现金流压力"] += 1

    bias_counts = {}
    evidence = []
    for record in monthly_records:
        report = parse_diagnosis_payload(record.get("behavior_diagnosis"))
        for bias in report.get("main_biases", []):
            bias_counts[bias] = bias_counts.get(bias, 0) + 1
        if record.get("is_in_plan") != "是" or report.get("main_biases"):
            evidence.append(
                f"{record.get('operation_date', '未填写日期')} {record.get('operation_type', '未填写类型')}："
                f"计划内={record.get('is_in_plan', '未填写')}，理由={record.get('reason') or '未填写'}"
            )
    top_biases = [name for name, _ in sorted(bias_counts.items(), key=lambda item: item[1], reverse=True)[:2]]

    if total == 0:
        one_sentence = "本月当前基金暂无操作记录，暂时无法形成纪律复盘。"
    elif out_plan_count == 0 and sum(emotion_triggers.values()) == 0:
        one_sentence = "本月执行较贴近原定计划，短期情绪干扰较少。"
    elif out_plan_count > in_plan_count:
        one_sentence = "本月最需要关注的是计划外操作占比偏高。"
    elif sum(emotion_triggers.values()) > 0:
        one_sentence = "本月主要问题是短期情绪信号已经进入操作决策。"
    else:
        one_sentence = "本月整体执行平稳，但仍需提升操作理由记录质量。"

    suggestions = []
    if emotion_triggers.get("FOMO") or emotion_triggers.get("亏损焦虑"):
        suggestions.append("为明显情绪触发的操作设置24小时冷静期。")
    if out_plan_count:
        suggestions.append("提前写清楚计划外操作的触发条件、金额上限和资金来源。")
    if actual_amount > planned_amount * 1.5 and planned_amount > 0:
        suggestions.append("限制本月计划外操作金额，避免实际投入长期偏离原计划。")
    if reduce_or_pause_count:
        suggestions.append("明确暂停或减少定投的条件，避免只因短期波动临时改变节奏。")
    if not suggestions:
        suggestions.append("继续保持操作前问答和操作后复盘问题记录。")

    return {
        "month": current_month,
        "plan_id": plan.get("plan_id") or plan.get("id"),
        "fund_name": plan.get("fund_name", ""),
        "records": monthly_records,
        "planned_amount": planned_amount,
        "actual_amount": actual_amount,
        "in_plan_count": in_plan_count,
        "out_plan_count": out_plan_count,
        "reduce_or_pause_count": reduce_or_pause_count,
        "discipline_completion_rate": discipline_completion_rate,
        "emotion_triggers": emotion_triggers,
        "top_biases": top_biases,
        "evidence": evidence[:5],
        "suggestions": suggestions[:5],
        "one_sentence": one_sentence,
    }
def save_monthly_review_report(review, user_id):
    """Upsert the current monthly review into the user's review report file."""
    if not review or not review.get("plan_id") or not review.get("month"):
        return
    reports = load_review_reports(user_id)
    report_record = {
        **review,
        "report_type": "monthly_review",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    reports = [
        item for item in reports
        if not (
            item.get("report_type") == "monthly_review"
            and item.get("plan_id") == review.get("plan_id")
            and item.get("month") == review.get("month")
        )
    ]
    reports.append(report_record)
    save_review_reports(reports, user_id)
def get_selected_plan(plans):
    selected = load_selected_plan(st.session_state.user_id)
    if selected:
        st.session_state.selected_fund_plan = selected
        return selected
    st.session_state.selected_fund_plan = {}
    return {}


def filter_history_for_plan(records, plan):
    if not plan:
        return []
    plan_id = plan.get("plan_id") or plan.get("id")
    fund_name = plan.get("fund_name")
    return [item for item in records if item.get("plan_id") == plan_id or (not item.get("plan_id") and item.get("fund_name") == fund_name)]


def merge_reminders(*groups):
    merged = []
    for group in groups:
        for item in group:
            if item not in merged:
                merged.append(item)
    return merged


def count_answered_questions(answers, questions):
    return len([answers.get(question["key"]) for question in questions if answers.get(question["key"]) is not None])


def sync_question_state():
    st.session_state.behavior_answers = dict(st.session_state.answers)
    st.session_state.qa_step = st.session_state.current_step
    st.session_state.answered_count = count_answered_questions(st.session_state.answers, QUESTION_FLOW)





def identify_behavior_biases(operation, answers):
    """Identify behavior biases from operation type, QA answers and reason text."""
    reason = operation.get("reason", "") or ""
    operation_type = operation.get("operation_type", "") or ""
    emotion = answers.get("emotion_reason", "")
    cash_flow = answers.get("cash_flow", "")
    can_accept_loss = answers.get("can_accept_loss", "")
    biases = []

    if "怕错过" in reason or "追涨" in reason or "别人" in reason or "热门" in reason or emotion == "明显影响":
        biases.append("FOMO")
    if "亏" in reason or "亏损" in reason or "焦虑" in reason or can_accept_loss == "不能接受":
        biases.append("损失厌恶")
    if any(word in reason for word in ["朋友", "同事", "群里", "网上", "大家", "别人"]):
        biases.append("羊群效应")
    if any(word in reason for word in ["肯定", "一定", "马上", "确定"]):
        biases.append("过度自信")
    if operation_type in {"加仓", "减仓", "其他"} and answers.get("is_planned") != "是":
        biases.append("频繁交易倾向")
    if operation_type == "加仓" and any(word in reason for word in ["跌", "下跌", "补仓", "摊平", "回本"]):
        biases.append("急于摊平成本")
    if cash_flow in {"可能会", "会"}:
        biases.append("现金流压力")
    return list(dict.fromkeys(biases)) or ["未识别到明显行为偏差"]


def build_decision_gate_report(operation, answers, ai_report=None):
    """Build a deterministic decision-gate report; AI content is optional enrichment."""
    reason = (operation.get("reason") or "").strip()
    success_reason = (operation.get("future_success_reason") or "").strip()
    failure_reflection = (operation.get("failure_reflection") or "").strip()
    score_parts = {
        "计划一致性": 30 if answers.get("is_planned") == "是" else 10,
        "情绪稳定性": {"无影响": 25, "轻微影响": 15, "明显影响": 5}.get(answers.get("emotion_reason"), 10),
        "现金流安全": {"不会": 20, "可能会": 10, "会": 0}.get(answers.get("cash_flow"), 10),
        "风险承受": 15 if answers.get("can_accept_loss") == "能接受" else 3,
        "理由清晰度": 10 if len(reason) >= 12 and success_reason and failure_reflection else (5 if reason else 0),
    }
    score = sum(score_parts.values())
    biases = identify_behavior_biases(operation, answers)

    if score >= 85:
        conclusion = "纪律通过"
    elif score >= 65:
        conclusion = "谨慎执行"
    elif score >= 45:
        conclusion = "建议冷静24小时"
    else:
        conclusion = "明显偏离计划"

    high_risk = (
        "FOMO" in biases
        or "现金流压力" in biases
        or answers.get("cash_flow") in {"可能会", "会"}
        or answers.get("can_accept_loss") == "不能接受"
        or answers.get("emotion_reason") == "明显影响"
    )
    if high_risk and conclusion == "纪律通过":
        conclusion = "谨慎执行"
    cooling_period = "建议冷静24小时后，再重新检查这次操作是否仍符合计划、现金流和风险承受边界。" if high_risk else "暂未触发强冷静期信号，但仍建议保留操作理由，方便下月复盘。"

    risk_notes = []
    if answers.get("is_planned") != "是":
        risk_notes.append("本次操作与原定定投计划存在偏离，需要确认是否有提前写好的规则依据。")
    if answers.get("emotion_reason") != "无影响":
        risk_notes.append("本次操作受到短期波动或情绪影响，可能放大临时决策倾向。")
    if answers.get("cash_flow") in {"可能会", "会"}:
        risk_notes.append("本次操作可能影响现金流，应优先确认生活资金和应急资金安全。")
    if answers.get("can_accept_loss") == "不能接受":
        risk_notes.append("如果无法接受继续下跌10%，说明当前操作金额或节奏可能超过心理承受边界。")
    if not risk_notes:
        risk_notes.append("本次操作未触发明显纪律风险，但仍需要保留复盘记录。")

    ai_report = parse_diagnosis_payload(ai_report)
    stories = ai_report.get("historical_stories") or []
    if not stories:
        stories = [{
            "title": "2008年金融危机：亏损压力下的补救冲动",
            "context": "2008年金融危机期间，全球风险资产大幅波动，许多投资者同时面对账户亏损、新闻冲击和流动性压力。",
            "simulation": "如果把本次决策放到类似环境中，关键不是判断市场方向，而是确认资金来源、计划边界和继续下跌时的承受力。",
            "data_logic": "当账户回撤30%时，需要上涨约43%才能回本；如果现金流同时受压，心理压力会明显放大。",
            "lesson": "先确认预算来源和最坏情形承受力，再评价操作是否符合纪律。",
        }]

    return {
        "report_type": "decision_gate",
        "discipline_score": score,
        "score_parts": score_parts,
        "discipline_conclusion": conclusion,
        "is_plan_deviation": answers.get("is_planned") != "是",
        "main_biases": biases,
        "risk_notes": risk_notes,
        "cash_flow_check": answers.get("cash_flow", "未填写"),
        "risk_tolerance_check": answers.get("can_accept_loss", "未填写"),
        "cooling_period": cooling_period,
        "historical_stories": stories[:2],
        "final_discipline_suggestion": "本报告只用于纪律检查和行为复盘。请把本次操作放回原定计划、现金流安全和风险承受边界中判断。",
        "next_review_points": [
            success_reason or "一个月后复盘：我希望这次操作被证明正确的原因是什么？",
            failure_reflection or "如果这次操作失败，我愿意承认自己错在哪？",
        ],
        "ai_raw_report": ai_report,
        "final_disclaimer": FINAL_DISCLAIMER,
        "rationality_score": score,
    }
def get_flow_statuses(operation_stage):
    has_operation = bool(st.session_state.get("current_operation"))
    qa_done = count_answered_questions(st.session_state.get("answers", {}), QUESTION_FLOW) >= len(QUESTION_FLOW)
    diagnosis_done = bool(st.session_state.get("diagnosis_report"))
    diagnosis_running = bool(st.session_state.get("diagnosis_in_progress"))
    statuses = []
    statuses.append("已完成" if has_operation else ("进行中" if operation_stage == 0 else "待完成"))
    statuses.append("已完成" if qa_done else ("进行中" if operation_stage == 1 else "待完成"))
    if diagnosis_done:
        statuses.append("已完成")
    elif diagnosis_running:
        statuses.append("进行中")
    elif operation_stage == 2 and qa_done:
        statuses.append("待生成")
    else:
        statuses.append("待完成")
    return statuses



def show_diagnosis_report(diagnosis):
    """Display the decision-gate report first, then supporting explanations."""
    diagnosis = parse_diagnosis_payload(diagnosis)
    if not diagnosis:
        st.info("本次操作未生成行为诊断报告。")
        return

    if diagnosis.get("report_type") == "decision_gate":
        st.subheader("操作检查报告")
        score = int(diagnosis.get("discipline_score", 0) or 0)
        conclusion = diagnosis.get("discipline_conclusion", "谨慎执行")
        col1, col2 = st.columns([1, 2])
        col1.metric("本次操作纪律评分", f"{score}/100")
        col1.progress(max(0, min(score, 100)) / 100)
        col2.metric("本次操作结论", conclusion)
        col2.caption("结论只代表行为纪律检查结果，不代表收益判断或操作建议。")

        st.markdown("**评分拆解**")
        part_cols = st.columns(5)
        for col, (name, value) in zip(part_cols, diagnosis.get("score_parts", {}).items()):
            col.metric(name, value)

        st.markdown("**是否偏离原定定投计划**")
        st.write("是" if diagnosis.get("is_plan_deviation") else "否")

        st.markdown("**主要行为偏差**")
        for item in diagnosis.get("main_biases", []):
            st.write(f"- {item}")

        st.markdown("**现金流与风险承受检查**")
        st.write(f"- 现金流影响：{diagnosis.get('cash_flow_check', '未填写')}")
        st.write(f"- 继续下跌10%承受：{diagnosis.get('risk_tolerance_check', '未填写')}")
        for note in diagnosis.get("risk_notes", []):
            st.warning(note)

        st.markdown("**历史情景类比**")
        for story in diagnosis.get("historical_stories", []):
            with st.expander(story.get("title", "情景推演")):
                st.write(story.get("context", "暂无历史背景。"))
                if story.get("simulation"):
                    st.write(f"如果放到当时：{story.get('simulation')}")
                if story.get("data_logic"):
                    st.write(f"数据与逻辑：{story.get('data_logic')}")
                if story.get("lesson"):
                    st.info(story.get("lesson"))

        st.markdown("**最终纪律建议**")
        st.info(diagnosis.get("final_discipline_suggestion", "请回到原定计划、现金流和风险承受边界中复核。"))
        st.markdown("**冷静期建议**")
        st.warning(diagnosis.get("cooling_period", "暂未触发强冷静期信号。"))
        st.markdown("**下次复盘观察点**")
        for item in diagnosis.get("next_review_points", []):
            st.write(f"- {item}")
        st.caption(diagnosis.get("final_disclaimer") or FINAL_DISCLAIMER)
        return

    st.subheader("行为诊断报告")
    score = diagnosis.get("rationality_score") or diagnosis.get("score") or 0
    if isinstance(score, dict):
        score = score.get("score", 0)
    try:
        score = int(float(score))
    except (TypeError, ValueError):
        score = 0
    st.metric("理性评分", f"{score}/100")
    st.progress(max(0, min(score, 100)) / 100)
    st.info(diagnosis.get("score_explanation") or diagnosis.get("explanation") or "本次报告为行为纪律检查，不构成投资建议。")
    st.warning(diagnosis.get("final_disclaimer") or FINAL_DISCLAIMER)
def sync_operation_history():
    records = list_operation_checks(st.session_state.user_id)
    st.session_state.operation_history = records
    return records


def reset_operation_flow():
    st.session_state.current_operation = {}
    st.session_state.behavior_answers = {}
    st.session_state.answers = {}
    st.session_state.diagnosis_report = {}
    st.session_state.diagnosis_in_progress = False
    st.session_state.qa_step = 0
    st.session_state.current_step = 0
    st.session_state.answered_count = 0
    st.session_state.step_index = 0
    st.session_state.diagnosis_record_key = ""
    for question in QUESTION_FLOW:
        st.session_state.pop(f"qa_radio_{question['key']}", None)


def get_operation_key(operation):
    return "|".join([str(operation.get("plan_id", "")), str(operation.get("operation_date", "")), str(operation.get("operation_type", "")), str(operation.get("amount", "")), str(operation.get("created_at", ""))])


def generate_and_save_diagnosis(show_progress=True):
    operation = st.session_state.get("current_operation", {})
    answers = st.session_state.get("answers", st.session_state.get("behavior_answers", {}))
    operation_key = get_operation_key(operation)
    if st.session_state.get("diagnosis_record_key") == operation_key and st.session_state.get("diagnosis_report"):
        return st.session_state.diagnosis_report
    progress = None
    if show_progress:
        progress = st.progress(0, text="正在生成投资行为诊断报告...")
        progress.progress(30, text="正在整理操作输入和问答结果...")
    with st.spinner("DeepSeek 正在生成行为诊断..."):
        if progress:
            progress.progress(70, text="正在识别心理偏差与行为模式...")
        ai_diagnosis = analyze_operation_reason(
            reason=operation.get("reason", ""),
            operation_type=operation.get("operation_type", ""),
            is_planned=answers.get("is_planned", ""),
            emotion_reason=answers.get("emotion_reason", ""),
            cash_flow=answers.get("cash_flow", ""),
            can_accept_loss=answers.get("can_accept_loss", ""),
        )
    diagnosis = build_decision_gate_report(operation, answers, ai_diagnosis)
    if progress:
        progress.progress(100, text="诊断完成，正在写入历史记录...")
    keyword_result = generate_conversation_check(operation.get("reason", ""))
    rule_result = generate_operation_check(
        operation_type=operation.get("operation_type", ""),
        is_in_plan=answers.get("is_planned", ""),
        market_emotion=answers.get("emotion_reason", ""),
        cash_flow_effect=answers.get("cash_flow", ""),
        accept_drawdown=answers.get("can_accept_loss", ""),
    )
    record = {
        **operation,
        "is_in_plan": answers.get("is_planned"),
        "market_emotion": answers.get("emotion_reason"),
        "cash_flow_effect": answers.get("cash_flow"),
        "accept_drawdown": answers.get("can_accept_loss"),
        "reason": operation.get("reason", ""),
        "qa_answers": dict(answers),
        "matched_keywords": keyword_result.get("matched_keywords", []),
        "check_result": merge_reminders(keyword_result.get("reminders", []), rule_result),
        "behavior_diagnosis": diagnosis,
        "check_date": operation.get("operation_date", ""),
        "emotion_state": answers.get("emotion_reason", ""),
        "fomo_level": "高" if "FOMO" in diagnosis.get("main_biases", []) else "低",
        "anxiety_level": "高" if answers.get("can_accept_loss") == "不能接受" or answers.get("emotion_reason") == "明显影响" else "低",
        "discipline_score": diagnosis.get("discipline_score"),
        "discipline_result": diagnosis.get("discipline_conclusion"),
        "behavior_biases": diagnosis.get("main_biases", []),
        "next_action": diagnosis.get("cooling_period", ""),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    add_transaction(record, st.session_state.user_id)
    st.session_state.diagnosis_report = diagnosis
    st.session_state.diagnosis_record_key = operation_key
    sync_operation_history()
    return diagnosis


def initialize_state():
    st.session_state.setdefault("selected_fund_plan", {})
    st.session_state.setdefault("current_operation", {})
    st.session_state.setdefault("answers", {})
    st.session_state.setdefault("behavior_answers", dict(st.session_state.answers))
    st.session_state.setdefault("diagnosis_report", {})
    st.session_state.setdefault("diagnosis_in_progress", False)
    st.session_state.setdefault("operation_history", list_operation_checks(st.session_state.user_id))
    st.session_state.setdefault("current_step", 0)
    st.session_state.setdefault("answered_count", count_answered_questions(st.session_state.answers, QUESTION_FLOW))
    st.session_state.setdefault("qa_step", st.session_state.current_step)
    st.session_state.setdefault("step_index", 0)
    st.session_state.setdefault("diagnosis_record_key", "")
    st.session_state.setdefault("show_create_plan_form", False)
    st.session_state.setdefault("plan_form_mode", "list")
    st.session_state.setdefault("editing_plan_id", "")
    st.session_state.setdefault("active_tab", "情绪日历")
    st.session_state.setdefault("pending_tab", "")
    st.session_state.setdefault("quick_operation_type", "")
    st.session_state.setdefault("calendar_month", date.today().replace(day=1))
    st.session_state.setdefault("selected_calendar_date", date.today())
    st.session_state.setdefault("delete_confirm_plan_id", "")



def to_date(value):
    """Convert a stored date value into a date object."""
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return date.today()


def get_month_records(records, month_start):
    """Return records in the same month as month_start."""
    month_key = month_start.strftime("%Y-%m")
    return [record for record in records if str(record.get("operation_date") or record.get("check_date") or "").startswith(month_key)]


def get_records_for_day(records, day):
    day_key = day.strftime("%Y-%m-%d")
    return [record for record in records if (record.get("operation_date") or record.get("check_date")) == day_key]


def get_record_score(record):
    diagnosis = parse_diagnosis_payload(record.get("behavior_diagnosis"))
    value = diagnosis.get("discipline_score") or diagnosis.get("rationality_score") or diagnosis.get("score")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def get_record_result(record):
    diagnosis = parse_diagnosis_payload(record.get("behavior_diagnosis"))
    return diagnosis.get("discipline_conclusion") or diagnosis.get("discipline_result") or "未生成结论"


def get_record_biases(record):
    diagnosis = parse_diagnosis_payload(record.get("behavior_diagnosis"))
    biases = diagnosis.get("main_biases") or diagnosis.get("behavior_biases") or []
    if isinstance(biases, list):
        return [str(item) for item in biases if str(item).strip()]
    return [str(biases)] if biases else []


def infer_emotion_state(record):
    explicit = record.get("emotion_state")
    emotion = record.get("market_emotion") or explicit or ""
    if explicit in {"平静", "焦虑", "FOMO", "亏损厌恶", "犹豫", "冲动"}:
        return explicit
    reason = record.get("reason") or ""
    if contains_any(reason, ["怕错过", "追涨", "别人都", "热门"]):
        return "FOMO"
    if contains_any(reason, ["亏", "下跌", "焦虑", "害怕", "恐慌"]):
        return "焦虑"
    if emotion == "明显影响":
        return "冲动"
    if emotion == "轻微影响":
        return "犹豫"
    return "平静"


def render_mini_bar_chart(title, data):
    st.markdown(f"**{title}**")
    if not data or sum(data.values()) == 0:
        st.caption("暂无足够数据，请完成一次操作检查后查看图表。")
        return
    total = max(sum(data.values()), 1)
    for label, value in data.items():
        percent = value / total
        cols = st.columns([1.2, 3, 0.5])
        cols[0].caption(label)
        cols[1].progress(percent)
        cols[2].caption(str(value))


def render_score_trend(records):
    st.markdown("**本月纪律评分趋势**")
    scored = []
    for record in sorted(records, key=lambda item: item.get("operation_date", "")):
        score = get_record_score(record)
        if score is not None:
            scored.append((record.get("operation_date", "未填写日期"), score))
    if not scored:
        st.caption("暂无足够数据，请完成一次操作检查后查看图表。")
        return
    for day_text, score in scored:
        cols = st.columns([1.2, 3, 0.6])
        cols[0].caption(day_text)
        cols[1].progress(max(0, min(score, 100)) / 100)
        cols[2].caption(f"{score}/100")


def build_calendar_day_tags(day, plan, day_records, monthly_reviews):
    tags = []
    dca_day = int(plan.get("dca_day", plan.get("monthly_day", 0)) or 0) if plan else 0
    if dca_day and day.day == dca_day:
        tags.append("📌 定投日")
    if any(record.get("operation_type") == "定投" and record.get("is_in_plan") == "是" for record in day_records):
        tags.append("✅ 已执行")
    if day_records:
        tags.append("🧾 操作记录")
    if any(infer_emotion_state(record) != "平静" or get_record_biases(record) for record in day_records):
        tags.append("🧠 心理记录")
    if any(report.get("month") == day.strftime("%Y-%m") for report in monthly_reviews) and day.day >= 28:
        tags.append("📊 月度复盘")
    return tags


def render_calendar_grid(plan, records, monthly_reviews):
    month_start = to_date(st.session_state.get("calendar_month", date.today().replace(day=1))).replace(day=1)
    selected_day = to_date(st.session_state.get("selected_calendar_date", date.today()))

    nav_cols = st.columns([1, 2, 1])
    with nav_cols[0]:
        if st.button("上个月", use_container_width=True):
            previous_month_last_day = month_start - timedelta(days=1)
            st.session_state.calendar_month = previous_month_last_day.replace(day=1)
            st.session_state.selected_calendar_date = previous_month_last_day.replace(day=1)
            st.rerun()
    with nav_cols[1]:
        st.markdown(f"### {month_start.year} 年 {month_start.month} 月")
    with nav_cols[2]:
        if st.button("下个月", use_container_width=True):
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            st.session_state.calendar_month = next_month
            st.session_state.selected_calendar_date = next_month
            st.rerun()

    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    header_cols = st.columns(7)
    for col, label in zip(header_cols, weekdays):
        col.caption(label)

    month_records = get_month_records(records, month_start)
    month_matrix = calendar.monthcalendar(month_start.year, month_start.month)
    for week in month_matrix:
        cols = st.columns(7)
        for col, day_number in zip(cols, week):
            if day_number == 0:
                col.write("")
                continue
            current_day = date(month_start.year, month_start.month, day_number)
            day_records = get_records_for_day(month_records, current_day)
            tags = build_calendar_day_tags(current_day, plan, day_records, monthly_reviews)
            selected_mark = ""
            label = selected_mark + str(day_number)
            if tags:
                label += "\n" + "\n".join(tags[:3])
            if col.button(label, key=f"calendar_day_{current_day.isoformat()}", use_container_width=True):
                st.session_state.selected_calendar_date = current_day
                st.rerun()


def render_day_detail(plan, records, monthly_reviews):
    selected_day = to_date(st.session_state.get("selected_calendar_date", date.today()))
    day_records = get_records_for_day(records, selected_day)
    dca_day = int(plan.get("dca_day", plan.get("monthly_day", 0)) or 0) if plan else 0
    is_dca_day = bool(dca_day and selected_day.day == dca_day)
    weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][selected_day.weekday()]

    st.subheader("当天详情")
    st.caption(f"{selected_day.strftime('%Y 年 %m 月 %d 日')}｜{weekday}")
    st.metric("日期状态", "定投日" if is_dca_day else "普通日期")

    with st.container(border=True):
        st.markdown("**当日任务**")
        if is_dca_day:
            st.write(f"今天是 {plan.get('fund_name', '当前基金')} 的定投日。")
            st.write(f"计划金额：{format_money(plan.get('monthly_amount', 0))}。")
            st.caption("请确认是否按计划执行本期定投，并在操作前记录当下心理状态。")
        else:
            st.write("今天不是当前基金的计划定投日。")
            st.caption("你可以查看本月执行情况，或在确有需要时记录一条观察/操作检查。")

    with st.container(border=True):
        st.markdown("**市场背景 Demo**")
        st.caption("以下市场背景为 Demo 示例，不构成投资建议，也不代表实时联网数据。")
        st.write("- 上个月海外市场波动仍可能影响部分 QDII 类基金净值表现。")
        st.write("- 美联储利率预期、人民币汇率波动、科技板块回调等因素可作为复盘时的背景变量。")
        st.write("- 当前系统不接金融 API，因此这里只作为行为复盘的示例材料。")

    with st.container(border=True):
        st.markdown("**当日操作记录与心理状态**")
        if not day_records:
            st.caption("当天暂无操作检查记录。")
        for record in day_records:
            score = get_record_score(record)
            biases = get_record_biases(record)
            st.write(f"**{record.get('operation_type', '未填写操作')}｜{format_money(record.get('amount', 0))}**")
            st.write(f"- 是否计划内：{record.get('is_in_plan', '未填写')}")
            st.write(f"- 操作理由：{record.get('reason') or '未填写'}")
            st.write(f"- 情绪状态：{infer_emotion_state(record)}")
            st.write(f"- 主要偏差：{'、'.join(biases) if biases else '未识别到明显偏差'}")
            st.write(f"- 纪律评分：{score if score is not None else '未生成'}/100")
            st.write(f"- 操作结论：{get_record_result(record)}")
            with st.expander("查看本次检查报告"):
                show_diagnosis_report(record.get("behavior_diagnosis"))

    st.markdown("**当天操作中心**")
    action_cols = st.columns(2)
    if is_dca_day and not day_records:
        available_actions = ["按计划记录本期定投", "进入操作检查", "跳过本次定投"]
    elif day_records:
        available_actions = ["查看本次操作检查报告", "进入月度复盘", "查看当前基金历史"]
    else:
        available_actions = ["记录观察", "发起计划外加仓", "发起计划外减仓", "暂停计划", "进入操作检查"]

    for index, action in enumerate(available_actions):
        with action_cols[index % 2]:
            if st.button(action, key=f"calendar_action_{selected_day}_{action}", use_container_width=True):
                st.session_state.selected_calendar_date = selected_day
                if action == "进入操作检查":
                    reset_operation_flow()
                    st.session_state.pending_tab = "操作检查"
                    st.rerun()
                elif action == "进入月度复盘":
                    st.session_state.pending_tab = "月度复盘"
                    st.rerun()
                elif action == "查看当前基金历史":
                    st.session_state.pending_tab = "历史记录"
                    st.rerun()
                elif action == "查看本次操作检查报告":
                    st.session_state.quick_operation_type = ""
                else:
                    st.session_state.quick_operation_type = action
                    st.rerun()

    if st.session_state.get("quick_operation_type"):
        st.divider()
        render_calendar_operation_form(plan, selected_day, st.session_state.quick_operation_type)


def normalize_calendar_operation_type(action):
    mapping = {
        "按计划记录本期定投": "定投",
        "跳过本次定投": "跳过",
        "记录观察": "观察记录",
        "发起计划外加仓": "加仓",
        "发起计划外减仓": "减仓",
        "暂停计划": "暂停",
        "临时买入": "加仓",
        "临时卖出": "减仓",
    }
    return mapping.get(action, action or "观察记录")


def save_calendar_quick_record(plan, selected_day, operation_type, amount, reason, emotion_state, market_emotion, cash_flow, accept_drawdown):
    """Save one calendar-side operation record using the existing history structure."""
    plan_id = plan.get("plan_id") or plan.get("id")
    dca_day = int(plan.get("dca_day", plan.get("monthly_day", 0)) or 0)
    is_planned = "是" if operation_type == "定投" and dca_day == selected_day.day else "否"
    operation = {
        "plan_id": plan_id,
        "fund_name": plan.get("fund_name"),
        "operation_date": selected_day.strftime("%Y-%m-%d"),
        "operation_type": operation_type,
        "amount": amount,
        "reason": reason.strip(),
        "future_success_reason": "来自日历操作中心的快速记录，后续月度复盘时检查是否符合原定计划。",
        "failure_reflection": "如果本次操作偏离计划，需要复盘当时是否受短期情绪或现金流压力影响。",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    answers = {
        "is_planned": is_planned,
        "emotion_reason": market_emotion,
        "cash_flow": cash_flow,
        "can_accept_loss": accept_drawdown,
    }
    diagnosis = build_decision_gate_report(operation, answers, ai_report={})
    keyword_result = generate_conversation_check(operation.get("reason", ""))
    rule_result = generate_operation_check(
        operation_type=operation_type,
        is_in_plan=is_planned,
        market_emotion=market_emotion,
        cash_flow_effect=cash_flow,
        accept_drawdown=accept_drawdown,
    )
    record = {
        **operation,
        "selected_date": operation["operation_date"],
        "check_date": operation["operation_date"],
        "is_in_plan": is_planned,
        "market_emotion": market_emotion,
        "cash_flow_effect": cash_flow,
        "accept_drawdown": accept_drawdown,
        "emotion_state": emotion_state,
        "fomo_level": "高" if emotion_state == "FOMO" or "FOMO" in diagnosis.get("main_biases", []) else "低",
        "anxiety_level": "高" if emotion_state in {"焦虑", "亏损厌恶", "冲动"} or accept_drawdown == "不能接受" else "低",
        "qa_answers": dict(answers),
        "matched_keywords": keyword_result.get("matched_keywords", []),
        "check_result": merge_reminders(keyword_result.get("reminders", []), rule_result),
        "behavior_diagnosis": diagnosis,
        "discipline_score": diagnosis.get("discipline_score"),
        "discipline_result": diagnosis.get("discipline_conclusion"),
        "behavior_biases": diagnosis.get("main_biases", []),
        "next_action": diagnosis.get("cooling_period", ""),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    add_transaction(record, st.session_state.user_id)
    sync_operation_history()
    return record


def render_calendar_operation_form(plan, selected_day, default_type):
    operation_type = normalize_calendar_operation_type(default_type)
    with st.form("calendar_quick_operation_form"):
        st.markdown(f"**记录：{operation_type}**")
        amount_default = float(plan.get("monthly_amount", 0) or 0) if operation_type == "定投" else 0.0
        amount = st.number_input("操作金额", min_value=0.0, step=100.0, value=amount_default, key="calendar_quick_amount")
        reason = st.text_area("操作原因", placeholder="请写下这次操作的触发原因、资金来源和复盘观察点。", key="calendar_quick_reason")
        emotion_state = st.radio("当前情绪状态", ["平静", "焦虑", "FOMO", "冲动", "犹豫", "亏损厌恶"], horizontal=True, key="calendar_quick_emotion")
        market_emotion = st.radio("是否受短期涨跌影响", ["无影响", "轻微影响", "明显影响"], horizontal=True, key="calendar_quick_market")
        cash_flow = st.radio("这笔钱是否影响现金流", ["不会", "可能会", "会"], horizontal=True, key="calendar_quick_cash")
        accept_drawdown = st.radio("如果继续下跌 10%，是否能接受", ["能接受", "不能接受"], horizontal=True, key="calendar_quick_drawdown")
        submitted = st.form_submit_button("保存到当天记录", use_container_width=True)
    if submitted:
        save_calendar_quick_record(plan, selected_day, operation_type, amount, reason, emotion_state, market_emotion, cash_flow, accept_drawdown)
        st.session_state.quick_operation_type = ""
        st.session_state.selected_calendar_date = selected_day
        st.session_state.pending_tab = "日历"
        st.success("已保存到当天记录。")
        st.rerun()

def render_calendar_charts(records, month_start):
    st.subheader("本月执行与心理图表")
    month_records = get_month_records(records, month_start)
    if not month_records:
        st.info("暂无足够数据，请完成一次操作检查后查看图表。")
        return

    type_counts = {}
    emotion_counts = {}
    for record in month_records:
        operation_type = record.get("operation_type") or "未填写"
        if record.get("is_in_plan") == "是" and operation_type == "定投":
            operation_type = "计划内定投"
        elif operation_type == "加仓":
            operation_type = "计划外加仓"
        elif operation_type in {"减仓", "赎回"}:
            operation_type = "赎回/卖出"
        type_counts[operation_type] = type_counts.get(operation_type, 0) + 1
        emotion = infer_emotion_state(record)
        emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1

    chart_cols = st.columns(3)
    with chart_cols[0]:
        render_mini_bar_chart("本月操作类型分布", type_counts)
    with chart_cols[1]:
        render_score_trend(month_records)
    with chart_cols[2]:
        render_mini_bar_chart("本月情绪状态分布", emotion_counts)


def render_calendar_tab(plans, selected_plan):
    st.header("定投日历")
    st.caption("以日历为主线查看定投日、操作记录、心理状态和月度复盘。")
    if not plans:
        st.info("当前还没有定投计划，请先创建基金计划，日历会自动生成每月定投日。")
        if st.button("去创建基金计划", use_container_width=True):
            st.session_state.pending_tab = "定投计划"
            st.session_state.plan_form_mode = "create"
            st.rerun()
        return

    top_cols = st.columns([1.1, 1.4, 1, 1])
    top_cols[0].metric("当前登录邮箱", st.session_state.get("email", "未绑定邮箱"))
    current_plan_id = selected_plan.get("plan_id") or selected_plan.get("id")
    plan_ids = [plan.get("plan_id") or plan.get("id") for plan in plans]
    selected_index = plan_ids.index(current_plan_id) if current_plan_id in plan_ids else 0
    with top_cols[1]:
        chosen_plan_id = st.selectbox(
            "快速切换基金计划",
            plan_ids,
            index=selected_index,
            format_func=lambda plan_id: next((plan.get("fund_name") or "未命名基金" for plan in plans if (plan.get("plan_id") or plan.get("id")) == plan_id), plan_id),
        )
    if chosen_plan_id != current_plan_id:
        st.session_state.selected_fund_plan = set_selected_plan(st.session_state.user_id, chosen_plan_id)
        reset_operation_flow()
        st.rerun()
    month_start = to_date(st.session_state.get("calendar_month", date.today().replace(day=1))).replace(day=1)
    top_cols[2].metric("当前月份", month_start.strftime("%Y-%m"))
    with top_cols[3]:
        if st.button("＋ 新建定投计划", use_container_width=True):
            st.session_state.pending_tab = "定投计划"
            st.session_state.plan_form_mode = "create"
            st.rerun()

    plan_id = selected_plan.get("plan_id") or selected_plan.get("id")
    records = list_operation_checks(st.session_state.user_id, plan_id=plan_id)
    monthly_reviews = load_review_reports(st.session_state.user_id)

    main_cols = st.columns([1.5, 1])
    with main_cols[0]:
        render_calendar_grid(selected_plan, records, monthly_reviews)
    with main_cols[1]:
        render_day_detail(selected_plan, records, monthly_reviews)

    st.divider()
    render_calendar_charts(records, month_start)

def render_plan_tab(plans, selected_plan):
    st.header("定投计划")
    st.caption("一个用户可以管理多个长期基金定投计划；当前选中的基金会用于操作检查、历史记录和月度复盘。")

    mode = st.session_state.get("plan_form_mode", "list")

    if mode == "create":
        st.subheader("新建基金计划")
        if st.button("返回计划列表", use_container_width=True):
            st.session_state.plan_form_mode = "list"
            st.session_state.editing_plan_id = ""
            st.session_state.delete_confirm_plan_id = ""
            st.rerun()

        with st.container(border=True):
            with st.form("create_plan_form"):
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    new_fund_name = st.text_input("基金名称", key="new_plan_fund_name")
                    new_fund_code = st.text_input("\u57fa\u91d1\u4ee3\u7801\uff08\u53ef\u9009\uff09", key="new_plan_fund_code")
                with col2:
                    new_monthly_amount = st.number_input("每月定投金额", min_value=0.0, step=100.0, key="new_plan_monthly_amount")
                with col3:
                    new_monthly_day = st.number_input("定投日期", min_value=1, max_value=28, step=1, value=1, key="new_plan_monthly_day")
                new_freq_col, new_start_col, new_end_col = st.columns(3)
                with new_freq_col:
                    new_dca_frequency = st.selectbox("\u5b9a\u6295\u9891\u7387", ["\u6bcf\u6708", "\u6bcf\u5468", "\u6bcf\u65e5"], index=0, key="new_plan_frequency")
                with new_start_col:
                    new_start_date = st.date_input("\u5f00\u59cb\u65e5\u671f", value=date.today(), key="new_plan_start_date")
                with new_end_col:
                    new_end_date = st.text_input("\u7ed3\u675f\u65e5\u671f\uff08\u53ef\u9009\uff09", placeholder="YYYY-MM-DD", key="new_plan_end_date")
                new_goal = st.text_area("投资目标", key="new_plan_goal")
                new_max_drawdown = st.text_input("最大可接受回撤", placeholder="例如：20%", key="new_plan_max_drawdown")
                new_notes = st.text_area("\u5907\u6ce8", key="new_plan_notes")
                create_button = st.form_submit_button("创建基金计划", use_container_width=True)

            if create_button:
                if not new_fund_name.strip():
                    st.warning("请先填写基金名称。")
                else:
                    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    saved_plan = create_fund_plan(
                        st.session_state.user_id,
                        {
                            "fund_name": new_fund_name.strip(),
                            "fund_code": new_fund_code.strip(),
                            "monthly_amount": new_monthly_amount,
                            "dca_day": new_monthly_day,
                            "dca_frequency": new_dca_frequency,
                            "start_date": new_start_date.strftime("%Y-%m-%d"),
                            "end_date": new_end_date.strip(),
                            "goal": new_goal,
                            "max_drawdown": new_max_drawdown,
                            "notes": new_notes,
                            "created_at": now_text,
                            "updated_at": now_text,
                        },
                    )
                    st.session_state.selected_fund_plan = saved_plan
                    st.session_state.plan_form_mode = "list"
                    st.session_state.editing_plan_id = ""
                    st.session_state.delete_confirm_plan_id = ""
                    reset_operation_flow()
                    st.success("已创建基金计划，并设为当前基金。")
                    st.rerun()
        return

    if mode == "edit":
        editing_plan_id = st.session_state.get("editing_plan_id") or (selected_plan.get("plan_id") or selected_plan.get("id"))
        editing_plan = next((plan for plan in plans if (plan.get("plan_id") or plan.get("id")) == editing_plan_id), selected_plan)
        if not editing_plan:
            st.session_state.plan_form_mode = "list"
            st.session_state.editing_plan_id = ""
            st.info("未找到要编辑的基金计划，已返回计划列表。")
            st.rerun()

        selected_plan_id = editing_plan.get("plan_id") or editing_plan.get("id")
        st.subheader("编辑基金计划")
        if st.button("返回计划列表", use_container_width=True):
            st.session_state.plan_form_mode = "list"
            st.session_state.editing_plan_id = ""
            st.session_state.delete_confirm_plan_id = ""
            st.rerun()

        with st.container(border=True):
            st.caption(f"当前编辑：{editing_plan.get('fund_name', '未填写基金名称')}")
            with st.form(f"edit_plan_form_{selected_plan_id}"):
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    edit_fund_name = st.text_input("基金名称", value=editing_plan.get("fund_name", ""), key=f"edit_name_{selected_plan_id}")
                    edit_fund_code = st.text_input("\u57fa\u91d1\u4ee3\u7801\uff08\u53ef\u9009\uff09", value=editing_plan.get("fund_code", ""), key=f"edit_code_{selected_plan_id}")
                with col2:
                    edit_monthly_amount = st.number_input("每月定投金额", min_value=0.0, step=100.0, value=float(editing_plan.get("monthly_amount", 0) or 0), key=f"edit_amount_{selected_plan_id}")
                with col3:
                    edit_monthly_day = st.number_input("定投日期", min_value=1, max_value=28, step=1, value=int(editing_plan.get("dca_day", editing_plan.get("monthly_day", 1)) or 1), key=f"edit_day_{selected_plan_id}")
                edit_freq_col, edit_start_col, edit_end_col = st.columns(3)
                with edit_freq_col:
                    freq_options = ["\u6bcf\u6708", "\u6bcf\u5468", "\u6bcf\u65e5"]
                    current_freq = editing_plan.get("dca_frequency", "\u6bcf\u6708")
                    edit_dca_frequency = st.selectbox("\u5b9a\u6295\u9891\u7387", freq_options, index=freq_options.index(current_freq) if current_freq in freq_options else 0, key=f"edit_frequency_{selected_plan_id}")
                with edit_start_col:
                    edit_start_date = st.text_input("\u5f00\u59cb\u65e5\u671f", value=editing_plan.get("start_date", ""), placeholder="YYYY-MM-DD", key=f"edit_start_{selected_plan_id}")
                with edit_end_col:
                    edit_end_date = st.text_input("\u7ed3\u675f\u65e5\u671f\uff08\u53ef\u9009\uff09", value=editing_plan.get("end_date", ""), placeholder="YYYY-MM-DD", key=f"edit_end_{selected_plan_id}")
                edit_goal = st.text_area("投资目标", value=editing_plan.get("goal", editing_plan.get("investment_goal", "")), key=f"edit_goal_{selected_plan_id}")
                edit_max_drawdown = st.text_input("最大可接受回撤", value=editing_plan.get("max_drawdown", ""), key=f"edit_drawdown_{selected_plan_id}")
                edit_notes = st.text_area("\u5907\u6ce8", value=editing_plan.get("notes", ""), key=f"edit_notes_{selected_plan_id}")
                save_edit_button = st.form_submit_button("保存修改", use_container_width=True)

            if save_edit_button:
                if not edit_fund_name.strip():
                    st.warning("基金名称不能为空。")
                else:
                    saved_plan = update_fund_plan(
                        st.session_state.user_id,
                        selected_plan_id,
                        {
                            "fund_name": edit_fund_name.strip(),
                            "fund_code": edit_fund_code.strip(),
                            "monthly_amount": edit_monthly_amount,
                            "dca_day": edit_monthly_day,
                            "dca_frequency": edit_dca_frequency,
                            "start_date": edit_start_date.strip(),
                            "end_date": edit_end_date.strip(),
                            "goal": edit_goal,
                            "max_drawdown": edit_max_drawdown,
                            "notes": edit_notes,
                            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                    st.session_state.selected_fund_plan = saved_plan
                    st.session_state.plan_form_mode = "list"
                    st.session_state.editing_plan_id = ""
                    st.session_state.delete_confirm_plan_id = ""
                    reset_operation_flow()
                    st.success("基金计划已更新。")
                    st.rerun()

            st.divider()
            if st.button("删除该基金计划", key=f"delete_plan_{selected_plan_id}", use_container_width=True):
                st.session_state.delete_confirm_plan_id = selected_plan_id
                st.rerun()

            if st.session_state.get("delete_confirm_plan_id") == selected_plan_id:
                st.warning("请再次确认：该操作会删除当前基金计划及其关联历史记录，且不可恢复。")
                confirm_col, cancel_col = st.columns(2)
                with confirm_col:
                    if st.button("确认删除", key=f"confirm_delete_{selected_plan_id}", use_container_width=True):
                        result = delete_fund_plan_with_records(st.session_state.user_id, selected_plan_id)
                        remaining_plans = result.get("plans", [])
                        st.session_state.selected_fund_plan = remaining_plans[0] if remaining_plans else {}
                        st.session_state.plan_form_mode = "list"
                        st.session_state.editing_plan_id = ""
                        st.session_state.delete_confirm_plan_id = ""
                        reset_operation_flow()
                        st.success("已删除该基金计划及关联记录。")
                        st.rerun()
                with cancel_col:
                    if st.button("取消删除", key=f"cancel_delete_{selected_plan_id}", use_container_width=True):
                        st.session_state.delete_confirm_plan_id = ""
                        st.rerun()
        return

    st.subheader("已保存基金计划")
    if not plans:
        st.info("当前还没有定投计划，请先新增一个基金计划。")
    else:
        card_cols = st.columns(2)
        for index, plan in enumerate(plans):
            plan_id = plan.get("plan_id") or plan.get("id")
            is_current = (selected_plan.get("plan_id") or selected_plan.get("id")) == plan_id
            goal = plan.get("goal", plan.get("investment_goal", "")) or "未填写投资目标"
            with card_cols[index % 2].container(border=True):
                st.markdown(f"**{'当前计划｜' if is_current else ''}{plan.get('fund_name') or '未命名基金'}**")
                metric_cols = st.columns(3)
                metric_cols[0].metric("每月定投", format_money(plan.get("monthly_amount", 0)))
                metric_cols[1].metric("定投日期", f"{plan.get('dca_day') or plan.get('monthly_day', 1)} 日")
                metric_cols[2].metric("最大回撤", plan.get("max_drawdown") or "未填写")
                st.caption(f"投资目标：{goal[:72]}{'...' if len(goal) > 72 else ''}")
                if st.button("选择并编辑", key=f"select_plan_card_{plan_id}", use_container_width=True):
                    selected = set_selected_plan(st.session_state.user_id, plan_id)
                    st.session_state.selected_fund_plan = selected
                    st.session_state.plan_form_mode = "edit"
                    st.session_state.editing_plan_id = plan_id
                    st.session_state.delete_confirm_plan_id = ""
                    reset_operation_flow()
                    st.rerun()

    if st.button("＋ 新建基金计划", use_container_width=True):
        st.session_state.plan_form_mode = "create"
        st.session_state.editing_plan_id = ""
        st.session_state.delete_confirm_plan_id = ""
        st.rerun()

def render_operation_tab(selected_plan):
    st.header("操作检查")
    if not selected_plan:
        st.info("当前还没有定投计划，请先新增一个基金计划。")
        return
    st.caption(f"当前基金：{selected_plan.get('fund_name', '未填写基金名称')}")
    step_names = ["Step 1 操作输入", "Step 2 行为问答", "Step 3 行为诊断"]
    operation_stage = max(0, min(st.session_state.get("step_index", 0), len(step_names) - 1))
    step_statuses = get_flow_statuses(operation_stage)
    step_cols = st.columns(len(step_names))
    for index, name in enumerate(step_names):
        step_cols[index].metric(name, step_statuses[index])
    if operation_stage == 0:
        with st.container(border=True):
            st.subheader("Step 1：操作输入")
            with st.form("operation_input_form"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    operation_date = st.date_input("操作日期", value=to_date(st.session_state.get("selected_calendar_date", date.today())))
                with col2:
                    operation_type = st.selectbox("操作类型", ["定投", "加仓", "减仓", "其他"], index=0)
                with col3:
                    amount = st.number_input("操作金额", min_value=0.0, step=100.0)
                operation_reason = st.text_area("操作理由（可选）", placeholder="例如：最近波动较大，我想调整本月定投节奏。", height=90)
                future_success_reason = st.text_area("一个月后复盘：我希望这次操作被证明正确的原因是什么？", placeholder="例如：这次操作符合我提前写好的规则，并且没有影响现金流。", height=80)
                failure_reflection = st.text_area("如果这次操作失败，我愿意承认自己错在哪？", placeholder="例如：我可能高估了自己的风险承受能力，或者没有提前写清规则。", height=80)
                save_button = st.form_submit_button("保存操作输入", use_container_width=True)
            if save_button:
                st.session_state.current_operation = {
                    "plan_id": selected_plan.get("plan_id") or selected_plan.get("id"),
                    "fund_name": selected_plan.get("fund_name"),
                    "operation_date": operation_date.strftime("%Y-%m-%d"),
                    "operation_type": operation_type,
                    "amount": amount,
                    "reason": operation_reason.strip(),
                    "future_success_reason": future_success_reason.strip(),
                    "failure_reflection": failure_reflection.strip(),
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                st.session_state.answers = {}
                st.session_state.behavior_answers = {}
                st.session_state.diagnosis_report = {}
                st.session_state.diagnosis_in_progress = False
                st.session_state.current_step = 0
                st.session_state.answered_count = 0
                st.session_state.qa_step = 0
                st.session_state.step_index = 1
                st.session_state.diagnosis_record_key = ""
                for question in QUESTION_FLOW:
                    st.session_state.pop(f"qa_radio_{question['key']}", None)
                st.rerun()
    elif operation_stage == 1:
        if not st.session_state.current_operation:
            st.info("请先完成 Step 1 操作输入。")
            st.session_state.step_index = 0
            return
        st.subheader("Step 2：行为问答")
        st.caption("提交后进入诊断生成页。AI 只会在你点击生成按钮时调用。")
        answers = st.session_state.answers
        with st.form("behavior_qa_form"):
            draft_answers = {}
            for index, question in enumerate(QUESTION_FLOW, start=1):
                with st.container(border=True):
                    st.markdown(f"**Q{index}：{question['title']}**")
                    previous_answer = answers.get(question["key"])
                    default_index = question["options"].index(previous_answer) if previous_answer in question["options"] else None
                    draft_answers[question["key"]] = st.radio("请选择", question["options"], index=default_index, horizontal=True, label_visibility="collapsed", key=f"qa_radio_{question['key']}")
            submit_answers = st.form_submit_button("提交行为问答", use_container_width=True)
        if submit_answers:
            missing_questions = [question["title"] for question in QUESTION_FLOW if draft_answers.get(question["key"]) is None]
            if missing_questions:
                st.warning("请先完成所有行为问答后再提交。")
            else:
                st.session_state.answers = dict(draft_answers)
                st.session_state.current_step = len(QUESTION_FLOW)
                sync_question_state()
                st.session_state.diagnosis_report = {}
                st.session_state.diagnosis_in_progress = False
                st.session_state.step_index = 2
                st.rerun()
    else:
        operation = st.session_state.current_operation
        answers = st.session_state.answers
        if not operation:
            st.info("请先完成 Step 1 操作输入。")
            st.session_state.step_index = 0
            return
        if count_answered_questions(answers, QUESTION_FLOW) < len(QUESTION_FLOW):
            st.info("请先完成 Step 2 行为问答。")
            st.session_state.step_index = 1
            return
        st.subheader("Step 3：行为诊断")
        st.caption("确认无误后点击生成。页面刷新不会自动调用 AI。")
        summary_cols = st.columns(4)
        summary_cols[0].metric("操作类型", operation.get("operation_type", "未填写"))
        summary_cols[1].metric("操作金额", format_money(operation.get("amount", 0)))
        summary_cols[2].metric("计划内", answers.get("is_planned", "未填写"))
        summary_cols[3].metric("短期影响", answers.get("emotion_reason", "未填写"))
        with st.container(border=True):
            st.markdown("**保存前问答摘要**")
            st.write(f"- 是否影响现金流：{answers.get('cash_flow', '未填写')}")
            st.write(f"- 是否接受继续下跌 10%：{answers.get('can_accept_loss', '未填写')}")
            st.write(f"- 操作理由：{operation.get('reason') or '未填写'}")
        if st.session_state.diagnosis_report:
            st.session_state.diagnosis_in_progress = False
            st.success("诊断已生成，并已写入历史记录。")
            show_diagnosis_report(st.session_state.diagnosis_report)
            if st.button("保存记录，返回日历", use_container_width=True):
                reset_operation_flow()
                st.session_state.selected_calendar_date = to_date(operation.get("operation_date", date.today()))
                st.session_state.pending_tab = "日历"
                st.rerun()
        else:
            st.info("当前问答已完成，但尚未生成诊断，也未写入历史记录。")
            if st.button("生成行为诊断并保存记录", use_container_width=True):
                st.session_state.diagnosis_in_progress = True
                generate_and_save_diagnosis(show_progress=True)
                st.session_state.diagnosis_in_progress = False
                st.rerun()


def render_history_tab(selected_plan):
    st.header("历史记录")
    records = filter_history_for_plan(sync_operation_history(), selected_plan)
    if not selected_plan:
        st.info("当前还没有定投计划，请先新增一个基金计划。")
    elif not records:
        st.info("当前基金暂无历史记录。")
    else:
        st.caption(f"当前基金：{selected_plan.get('fund_name')}")
        for record in reversed(records):
            title = f"{record.get('operation_date', '未填写日期')} | {record.get('operation_type', '未填写类型')} | {format_money(record.get('amount', 0))}"
            with st.expander(title):
                left, right = st.columns([1, 2])
                with left:
                    st.write(f"**基金名称：** {record.get('fund_name', '未填写')}")
                    st.write(f"**操作金额：** {format_money(record.get('amount', 0))}")
                    st.write(f"**是否计划内：** {record.get('is_in_plan', '未填写')}")
                    st.write(f"**短期影响：** {record.get('market_emotion', '未填写')}")
                    st.write(f"**现金流：** {record.get('cash_flow_effect', '未填写')}")
                    st.write(f"**下跌承受：** {record.get('accept_drawdown', '未填写')}")
                    st.write(f"**操作理由：** {record.get('reason') or '未填写'}")
                    st.write(f"**一个月后希望被证明正确的原因：** {record.get('future_success_reason') or '未填写'}")
                    st.write(f"**如果失败愿意承认的问题：** {record.get('failure_reflection') or '未填写'}")
                with right:
                    show_diagnosis_report(record.get("behavior_diagnosis"))



def render_review_tab(selected_plan):
    st.header("月度复盘")
    st.caption("月度复盘只读取当前选中的基金计划和当前月份的操作记录，不混入其他基金记录。")
    if not selected_plan:
        st.info("当前还没有定投计划，请先新增一个基金计划。")
        return
    plan_id = selected_plan.get("plan_id") or selected_plan.get("id")
    plan_records = list_operation_checks(st.session_state.user_id, plan_id=plan_id)
    st.caption(f"当前基金：{selected_plan.get('fund_name', '未填写基金名称')}")
    review = build_monthly_review(plan_records, selected_plan)
    if not review.get("records"):
        st.info("当前基金本月暂无操作检查记录。完成一次本月操作检查后，这里会生成月度复盘。")
        return

    save_monthly_review_report(review, st.session_state.user_id)

    st.subheader("一、本月执行概览")
    cols = st.columns(6)
    cols[0].metric("计划定投金额", format_money(review.get("planned_amount", 0)))
    cols[1].metric("实际操作金额", format_money(review.get("actual_amount", 0)))
    cols[2].metric("计划内操作", review.get("in_plan_count", 0))
    cols[3].metric("计划外操作", review.get("out_plan_count", 0))
    cols[4].metric("暂停/减少/赎回", review.get("reduce_or_pause_count", 0))
    cols[5].metric("纪律完成率", f"{review.get('discipline_completion_rate', 0)}%")

    st.subheader("二、计划偏离分析")
    if review.get("out_plan_count", 0) == 0:
        st.info("本月没有记录到计划外操作，执行节奏较贴近当前定投计划。")
    else:
        st.warning("本月存在计划外操作，建议检查这些操作是否有提前写好的规则依据。")
        for item in review.get("evidence", []):
            st.write(f"- {item}")

    st.subheader("三、情绪触发统计")
    trigger_cols = st.columns(3)
    for index, (name, count) in enumerate(review.get("emotion_triggers", {}).items()):
        trigger_cols[index % 3].metric(name, count)

    st.subheader("四、行为偏差识别")
    top_biases = review.get("top_biases", [])
    if top_biases:
        st.warning("本月最明显的行为偏差：" + "、".join(top_biases))
        for item in review.get("evidence", [])[:3]:
            st.write(f"- 证据：{item}")
    else:
        st.info("本月没有从操作记录中识别到突出的行为偏差信号。")

    st.subheader("五、下月纪律建议")
    for item in review.get("suggestions", []):
        st.write(f"- {item}")

    st.subheader("六、一句话总结")
    st.info(review.get("one_sentence", "暂无总结。"))
    st.caption("以上复盘只用于行为记录和纪律检查，不构成投资建议。")
def render_personality_tab(plans):
    st.header("投资人格分析")
    st.caption("投资人格分析读取当前用户所有基金计划下的历史操作记录，用于长期行为画像。")
    all_records = list_operation_checks(st.session_state.user_id)
    if not plans:
        st.info("当前还没有定投计划，请先新增一个基金计划。")
    elif not all_records:
        st.info("当前用户还没有历史操作记录。完成几次操作检查后，这里会生成整体投资人格画像。")
    else:
        profile = build_investment_personality(all_records)
        scores = profile.get("scores", {})
        stats = profile.get("stats", {})
        sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        secondary = sorted_scores[1][0] if len(sorted_scores) > 1 else "暂无"

        st.subheader("一、主投资人格类型")
        st.info(f"**{profile.get('personality_type')}**\n\n{profile.get('pattern_summary')}")

        st.subheader("二、副投资人格倾向")
        st.write(f"当前第二突出维度：**{secondary}**。该倾向用于提示长期行为习惯，不代表收益能力。")

        st.subheader("三、行为偏差雷达")
        score_cols = st.columns(5)
        score_items = [("Emotional Sensitivity", "情绪敏感度"), ("Discipline", "纪律性"), ("FOMO Tendency", "FOMO倾向"), ("Risk Tolerance", "风险承受能力"), ("Long-term Orientation", "长期主义")]
        for col, (key, label) in zip(score_cols, score_items):
            value = scores.get(key, 0)
            col.metric(label, f"{value}/100")
            col.progress(value / 100)

        st.subheader("四、历史证据")
        stat_cols = st.columns(5)
        stat_cols[0].metric("全部历史操作", stats.get("total", 0))
        stat_cols[1].metric("偏离计划", stats.get("out_plan_count", 0))
        stat_cols[2].metric("加仓/减仓/调整", stats.get("adjust_count", 0))
        stat_cols[3].metric("情绪触发", stats.get("emotion_count", 0))
        stat_cols[4].metric("FOMO/羊群信号", stats.get("fomo_count", 0))
        with st.container(border=True):
            for item in profile.get("evidence", []):
                st.write(f"- {item}")

        st.subheader("五、长期改进建议")
        with st.container(border=True):
            for item in profile.get("suggestions", []):
                st.write(f"- {item}")
        st.caption("以上内容基于当前用户所有基金计划的操作记录，仅用于行为优化和心理建模，不构成投资建议。")

EMOTION_RISK_EMOJI = {"低": "", "中": "", "高": ""}


def load_user_emotions():
    return load_emotion_records(st.session_state.user_id)


def record_day(record):
    return record.get("record_date", "")


def emotion_for_day(records, day):
    key = day.strftime("%Y-%m-%d")
    for record in records:
        if record_day(record) == key:
            return record
    return {}


def emotion_emoji(record):
    """Emotion state is now expressed by background color, not emoji markers."""
    return ""

def fallback_emotion_analysis(record):
    anxiety = int(record.get("anxiety_level", 0) or 0)
    fomo = int(record.get("fomo_level", 0) or 0)
    impulse = int(record.get("impulse_level", 0) or 0)
    max_score = max(anxiety, fomo, impulse)
    biases = []
    if fomo >= 6 or "买入" in record.get("operation_impulse", "") or "上涨" in record.get("impulse_source", ""):
        biases.append("FOMO")
    if anxiety >= 6 or "下跌" in record.get("impulse_source", "") or "亏损" in record.get("impulse_source", ""):
        biases.append("亏损厌恶")
    if record.get("account_check_frequency") == "反复查看":
        biases.append("过度看盘")
    if impulse >= 7:
        biases.append("冲动交易倾向")
    if not biases:
        biases.append("暂无明显偏差")
    if max_score >= 8:
        label, risk = "高风险情绪型", "高"
    elif max_score >= 5:
        label, risk = ("FOMO冲动型" if fomo >= anxiety else "轻微焦虑型"), "中"
    elif record.get("operation_impulse") != "没有":
        label, risk = "犹豫观望型", "中"
    else:
        label, risk = "平静执行型", "低"
    return {
        "emotion_label": label,
        "risk_level": risk,
        "behavior_biases": biases[:3],
        "one_sentence_reminder": "请把今天的投资情绪当作观察对象，而不是立刻行动的理由。",
        "observation_point": "接下来几天观察自己是否继续反复看盘、焦虑或产生临时改变计划的冲动。",
        "fallback": True,
    }


def analyze_emotion_safely(record):
    try:
        analysis = analyze_daily_emotion(record)
        if isinstance(analysis, dict) and analysis.get("emotion_label"):
            return analysis
    except Exception:
        pass
    return fallback_emotion_analysis(record)


def build_emotion_record(day, account_check_frequency, strongest_emotion, operation_impulse, impulse_source, actual_action, anxiety_level, fomo_level, impulse_level, note):
    record = {
        "user_id": st.session_state.user_id,
        "record_date": day.strftime("%Y-%m-%d"),
        "account_check_frequency": account_check_frequency,
        "strongest_emotion": strongest_emotion,
        "operation_impulse": operation_impulse,
        "impulse_source": impulse_source,
        "actual_action": actual_action,
        "anxiety_level": anxiety_level,
        "fomo_level": fomo_level,
        "impulse_level": impulse_level,
        "note": note.strip(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    existing = get_emotion_record_by_date(st.session_state.user_id, record["record_date"])
    record["created_at"] = existing.get("created_at", record["updated_at"]) if existing else record["updated_at"]
    analysis = analyze_emotion_safely(record)
    record.update({
        "ai_emotion_label": analysis.get("emotion_label"),
        "ai_risk_level": analysis.get("risk_level"),
        "ai_behavior_biases": analysis.get("behavior_biases", []),
        "ai_reminder": analysis.get("one_sentence_reminder"),
        "ai_observation_point": analysis.get("observation_point"),
        "ai_analysis": analysis,
    })
    return record


def build_emotion_warning_summary(records):
    """Build a dynamic top emotion risk summary for the dashboard."""
    today = date.today()
    recent_7 = [r for r in records if 0 <= (today - to_date(r.get("record_date"))).days <= 6]
    recent_30 = [r for r in records if 0 <= (today - to_date(r.get("record_date"))).days <= 29]
    high_anxiety_7 = sum(1 for r in recent_7 if int(r.get("anxiety_level", 0) or 0) >= 7)
    high_fomo_30 = sum(1 for r in recent_30 if int(r.get("fomo_level", 0) or 0) >= 7)
    repeat_check_7 = sum(1 for r in recent_7 if r.get("account_check_frequency") == "反复查看")
    high_impulse_30 = sum(1 for r in recent_30 if int(r.get("impulse_level", 0) or 0) >= 7)

    reasons = [
        f"过去 7 天高焦虑记录：{high_anxiety_7} 次",
        f"过去 30 天高 FOMO 记录：{high_fomo_30} 次",
        f"最近 7 天反复看盘：{repeat_check_7} 次",
        f"最近 30 天高冲动记录：{high_impulse_30} 次",
    ]

    if high_anxiety_7 >= 4 or high_fomo_30 >= 5 or high_impulse_30 >= 5:
        level = "high"
        message = "最近一段时间内，你多次出现高焦虑、FOMO 或高冲动状态，情绪驱动信号较强。"
        reminder = "建议先把这些信号当作观察点，而不是行动理由。"
    elif high_anxiety_7 >= 3 or high_fomo_30 >= 3 or repeat_check_7 >= 4 or high_impulse_30 >= 3:
        level = "elevated"
        message = "最近记录中出现了较密集的焦虑、FOMO、反复看盘或操作冲动信号。"
        reminder = "建议先把这些信号当作观察点，而不是行动理由。"
    elif high_anxiety_7 >= 1 or high_fomo_30 >= 1 or repeat_check_7 >= 2 or high_impulse_30 >= 1:
        level = "medium"
        message = "最近记录中出现了轻微情绪波动，暂时不严重，但值得留意。"
        reminder = "建议先把这些信号当作观察点，而不是行动理由。"
    else:
        level = "low"
        message = "当前没有明显连续情绪风险，整体状态相对稳定。"
        reminder = "继续保持记录，观察情绪是否随市场波动出现连续变化。"

    colors = risk_color_map(level)
    return {
        "level": level,
        "title": colors["title"],
        "message": message,
        "reasons": reasons,
        "reminder": reminder,
        "bg": colors["bg"],
        "border": colors["border"],
        "reminder_bg": colors["reminder_bg"],
        "reminder_border": colors["reminder_border"],
    }

def build_emotion_warnings(records):
    summary = build_emotion_warning_summary(records)
    return [summary["message"], summary["reminder"]]


def count_by(items):
    data = {}
    for item in items:
        data[item] = data.get(item, 0) + 1
    return data


def render_emotion_history_page():
    st.header("情绪历史")
    records = sorted(load_user_emotions(), key=lambda item: item.get("record_date", ""), reverse=True)
    if not records:
        st.info("暂无情绪记录。")
        return
    for record in records:
        with st.container(border=True):
            cols = st.columns([1, 1, 1, 2])
            cols[0].write(record.get("record_date"))
            cols[1].write(record.get('ai_emotion_label'))
            cols[2].write(f"风险：{record.get('ai_risk_level')}")
            cols[3].write(record.get("ai_reminder", ""))
            st.caption(f"焦虑/FOMO/冲动：{record.get('anxiety_level')}/{record.get('fomo_level')}/{record.get('impulse_level')}｜冲动：{record.get('operation_impulse')}｜实际操作：{record.get('actual_action')}｜偏差：{'、'.join(record.get('ai_behavior_biases', []))}")
            if st.button("回到日历查看这一天", key=f"go_emotion_{record.get('record_date')}"):
                target = to_date(record.get("record_date"))
                st.session_state.selected_calendar_date = target
                st.session_state.calendar_month = target.replace(day=1)
                st.session_state.pending_tab = "情绪日历"
                st.rerun()


def render_emotion_review_page():
    st.header("情绪复盘")
    records = load_user_emotions()
    month_start = to_date(st.session_state.get("calendar_month", date.today().replace(day=1))).replace(day=1)
    month_key = month_start.strftime("%Y-%m")
    month_records = [r for r in records if r.get("record_date", "").startswith(month_key)]
    st.caption(f"当前月份：{month_key}")
    if not month_records:
        st.info("本月暂无情绪记录。")
        return
    total = len(month_records)
    calm = sum(1 for r in month_records if r.get("ai_risk_level") == "低")
    high = sum(1 for r in month_records if r.get("ai_risk_level") == "高")
    mid = total - calm - high
    avg_anxiety = round(sum(int(r.get("anxiety_level", 0) or 0) for r in month_records) / total, 1)
    avg_fomo = round(sum(int(r.get("fomo_level", 0) or 0) for r in month_records) / total, 1)
    avg_impulse = round(sum(int(r.get("impulse_level", 0) or 0) for r in month_records) / total, 1)
    cols = st.columns(6)
    cols[0].metric("记录天数", total)
    cols[1].metric("平静天数", calm)
    cols[2].metric("中风险天数", mid)
    cols[3].metric("高风险天数", high)
    cols[4].metric("平均焦虑", avg_anxiety)
    cols[5].metric("平均FOMO", avg_fomo)
    st.metric("平均冲动", avg_impulse)
    biases = []
    for record in month_records:
        biases.extend(record.get("ai_behavior_biases", []))
    st.subheader("本月主要行为金融偏差")
    render_mini_bar_chart("偏差出现次数", count_by(biases))
    st.subheader("本月情绪风险提醒")
    for item in build_emotion_warnings(month_records):
        st.write(f"- {item}")
    st.subheader("下月观察重点")
    if avg_fomo >= avg_anxiety and avg_fomo >= avg_impulse:
        st.info("下月重点观察：上涨或他人观点刺激后，是否更容易产生怕错过情绪。")
    elif avg_anxiety >= avg_impulse:
        st.info("下月重点观察：账户波动或亏损时，焦虑是否会带来反复看盘。")
    else:
        st.info("下月重点观察：高冲动状态是否会推动临时改变原计划。")


def level_from_count(count, total):
    ratio = count / total if total else 0
    if ratio >= 0.35:
        return "高"
    if ratio >= 0.15:
        return "中"
    return "低"


def render_emotion_personality_page():
    st.header("投资人格分析")
    records = load_user_emotions()
    if not records:
        st.info("暂无情绪记录。完成几次每日打卡后，这里会形成动态画像。")
        return
    total = len(records)
    fomo_days = sum(1 for r in records if int(r.get("fomo_level", 0) or 0) >= 7 or "FOMO" in r.get("ai_behavior_biases", []))
    anxiety_days = sum(1 for r in records if int(r.get("anxiety_level", 0) or 0) >= 7 or "亏损厌恶" in r.get("ai_behavior_biases", []))
    overcheck_days = sum(1 for r in records if r.get("account_check_frequency") == "反复查看")
    impulse_days = sum(1 for r in records if int(r.get("impulse_level", 0) or 0) >= 7)
    calm_days = sum(1 for r in records if r.get("ai_risk_level") == "低")
    if fomo_days >= max(anxiety_days, overcheck_days, impulse_days, calm_days):
        main_type = "FOMO 敏感型"
    elif anxiety_days >= max(overcheck_days, impulse_days, calm_days):
        main_type = "亏损焦虑型"
    elif overcheck_days >= max(impulse_days, calm_days):
        main_type = "过度看盘型"
    elif impulse_days >= calm_days:
        main_type = "冲动操作型"
    else:
        main_type = "冷静执行型"
    st.info(f"**主投资情绪人格：{main_type}**\n\n这是基于当前记录的动态画像，会随着后续记录变化。")
    st.subheader("副倾向")
    st.write(f"过去记录中，高 FOMO 天数 {fomo_days} 天，高焦虑天数 {anxiety_days} 天，反复看盘 {overcheck_days} 天，高冲动 {impulse_days} 天。")
    st.subheader("行为金融偏差雷达")
    radar = {"FOMO": level_from_count(fomo_days, total), "亏损厌恶": level_from_count(anxiety_days, total), "过度看盘": level_from_count(overcheck_days, total), "羊群效应": level_from_count(sum(1 for r in records if "羊群效应" in r.get("ai_behavior_biases", [])), total), "冲动交易": level_from_count(impulse_days, total)}
    for key, value in radar.items():
        st.write(f"- {key}：{value}")
    st.subheader("历史证据")
    st.write(f"- 共有 {total} 条情绪记录。")
    st.write(f"- FOMO 分数超过 7 或出现 FOMO 标签的天数为 {fomo_days} 天。")
    st.write(f"- 焦虑分数超过 7 或出现亏损厌恶标签的天数为 {anxiety_days} 天。")
    st.subheader("长期提醒")
    st.warning("你最需要关注的不是是否会交易，而是市场波动时是否会反复查看账户并积累焦虑。本画像不构成投资建议。")



# Apple-like Dashboard UI overrides. These functions keep the existing data and AI logic unchanged.
def inject_design_tokens():
    st.markdown("""
        <style>
        #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] { visibility: hidden !important; display: none !important; }
        header { visibility: hidden !important; height: 0 !important; }
        :root {
            --app-bg: #F5F5F7;
            --card-bg: #FFFFFF;
            --text-main: #1D1D1F;
            --text-muted: #6E6E73;
            --line-soft: rgba(0,0,0,0.08);
            --shadow-soft: 0 18px 45px rgba(0,0,0,0.06);
            --blue: #0A84FF;
            --green: #34C759;
            --yellow: #FFD60A;
            --orange: #FF9F0A;
            --red: #FF453A;
            --gray: #D1D5DB;
        }
        html, body, [data-testid="stAppViewContainer"], .stApp {
            background: var(--app-bg) !important;
            color: var(--text-main) !important;
        }
        [data-testid="stHeader"] { background: rgba(245,245,247,0.72) !important; backdrop-filter: blur(20px); }
        .block-container { max-width: 1240px; padding-top: 2.4rem; padding-bottom: 4rem; }
        h1, h2, h3, p, span, div { letter-spacing: 0 !important; }
        h1 { font-size: 2.75rem !important; line-height: 1.08 !important; font-weight: 760 !important; color: var(--text-main) !important; }
        h2, h3 { color: var(--text-main) !important; font-weight: 600 !important; }
        .apple-hero {
            background: linear-gradient(135deg, #FFFFFF 0%, #F2F7FF 58%, #F7FBF8 100%);
            border: 1px solid var(--line-soft);
            border-radius: 28px;
            padding: 34px 38px;
            box-shadow: var(--shadow-soft);
            margin-bottom: 24px;
        }
        .hero-kicker { color: var(--blue); font-size: 0.84rem; font-weight: 700; margin-bottom: 10px; }
        .hero-title { color: var(--text-main); font-size: 2.7rem; font-weight: 780; line-height: 1.08; margin: 0 0 10px 0; }
        .hero-subtitle { color: var(--text-muted); font-size: 1.08rem; margin-bottom: 18px; }
        .hero-disclaimer { color: #5C5C62; background: rgba(255,255,255,0.68); border: 1px solid var(--line-soft); border-radius: 18px; padding: 12px 16px; font-size: 0.92rem; }
        .apple-card {
            background: var(--card-bg);
            border: 1px solid var(--line-soft);
            border-radius: 24px;
            padding: 22px 24px;
            box-shadow: var(--shadow-soft);
            margin-bottom: 20px;
        }
        .compact-card { min-height: 116px; }
        .status-card-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 16px;
            margin-bottom: 4px;
        }
        .status-card-grid .apple-card { margin-bottom: 20px; }
        .card-label { color: var(--text-muted); font-size: 0.82rem; font-weight: 680; margin-bottom: 8px; }
        .card-value { color: var(--text-main); font-size: 1.58rem; font-weight: 760; line-height: 1.16; }
        .card-caption { color: var(--text-muted); font-size: 0.86rem; margin-top: 8px; }
        .user-pill { display: inline-block; background: rgba(255,255,255,0.78); color: var(--text-muted); border: 1px solid var(--line-soft); border-radius: 999px; padding: 8px 12px; margin-right: 8px; font-size: 0.82rem; }
        .warning-card { border-radius: 26px; padding: 24px 26px; box-shadow: var(--shadow-soft); margin: 4px 0 26px 0; border: 1px solid var(--line-soft); }
        .warning-low { background: linear-gradient(135deg, rgba(52,199,89,0.14), rgba(10,132,255,0.10), #FFFFFF); }
        .warning-medium { background: #F7EED2; border-color: #E8D7A2; }
        .warning-high { background: #F7EED2; border-color: #E8D7A2; }
        .warning-title { font-size: 1.28rem; font-weight: 760; color: var(--text-main); margin-bottom: 8px; }
        .warning-message { color: var(--text-main); font-size: 0.98rem; margin-bottom: 12px; }
        .reason-list { color: var(--text-muted); font-size: 0.88rem; line-height: 1.8; margin-top: 8px; }
        .warning-reminder { border-radius: 16px; padding: 10px 12px; color: #5C5C62; font-size: 0.86rem; line-height: 1.55; font-weight: 520; margin-top: 12px; }
        .section-title { font-size: 1.28rem; font-weight: 760; color: var(--text-main); margin: 4px 0 14px 0; }
        .weekday { text-align: center; color: var(--text-muted); font-size: 0.78rem; font-weight: 700; }
        div.stButton > button {
            border-radius: 18px !important;
            border: 1px solid var(--line-soft) !important;
            background: #FFFFFF !important;
            color: var(--text-main) !important;
            box-shadow: 0 8px 20px rgba(0,0,0,0.04) !important;
            min-height: 54px;
            white-space: pre-line;
            font-weight: 650 !important;
        }
        div.stButton > button:hover { border-color: rgba(10,132,255,0.35) !important; box-shadow: 0 12px 28px rgba(10,132,255,0.13) !important; }
        .calendar-note { color: var(--text-muted); font-size: 0.88rem; margin-top: -4px; margin-bottom: 14px; }
        .date-heading { font-size: 1.35rem; font-weight: 760; color: var(--text-main); margin-bottom: 4px; }
        .score-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
        .score-chip { background: #F5F5F7; border-radius: 16px; padding: 12px; text-align: center; }
        .score-num { font-size: 1.22rem; font-weight: 760; color: var(--text-main); }
        .score-label { font-size: 0.76rem; color: var(--text-muted); margin-top: 2px; }
        .ai-reminder { background: #F2F7FF; border: 1px solid rgba(10,132,255,0.16); border-radius: 20px; padding: 16px; color: var(--text-main); font-weight: 650; margin-top: 12px; }
        .emotion-status-panel { border-radius: 22px; padding: 18px; border: 1px solid rgba(0,0,0,0.045); box-shadow: inset 0 1px 0 rgba(255,255,255,0.58); }
        .bias-chip { display: inline-block; background: #F5F5F7; color: var(--text-main); border-radius: 999px; padding: 7px 11px; margin: 4px 6px 0 0; font-size: 0.82rem; }
        .emotion-legend-card {
            background: rgba(255,255,255,0.92);
            border: 1px solid rgba(0,0,0,0.055);
            border-radius: 26px;
            box-shadow: 0 14px 34px rgba(0,0,0,0.045);
            padding: 24px 26px 26px 26px;
            margin: 24px 0 8px 0;
        }
        .emotion-legend-title {
            color: var(--text-main);
            font-size: 1.12rem;
            font-weight: 760;
            margin-bottom: 16px;
        }
        .emotion-legend-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
        }
        .emotion-pill {
            border-radius: 999px;
            padding: 12px 16px;
            color: #1D1D1F;
            font-size: 0.95rem;
            font-weight: 650;
            text-align: center;
            border: 1px solid rgba(0,0,0,0.035);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.70);
            white-space: nowrap;
        }
        .stRadio, .stSelectbox, .stSlider, .stTextArea { background: transparent !important; }
        [data-testid="stMetric"] { background: #FFFFFF; border: 1px solid var(--line-soft); border-radius: 20px; padding: 14px 16px; box-shadow: 0 8px 20px rgba(0,0,0,0.04); }
        [data-testid="stAlert"] { border-radius: 20px; border: 1px solid var(--line-soft); }
        textarea, input, [data-baseweb="select"] > div { border-radius: 16px !important; }
        .mood-calendar-shell {
            margin-top: 10px;
        }
        .mood-calendar-grid {
            display: grid;
            grid-template-columns: repeat(7, minmax(72px, 1fr));
            gap: 12px;
            align-items: stretch;
        }
        .mood-weekday {
            text-align: center;
            color: var(--text-muted);
            font-size: 0.78rem;
            font-weight: 760;
            padding: 4px 0 8px 0;
        }
        .mood-day {
            min-height: 82px;
            border-radius: 18px;
            display: flex;
            align-items: center;
            justify-content: center;
            text-decoration: none !important;
            border: 1px solid rgba(0,0,0,0.05);
            box-shadow: 0 10px 22px rgba(0,0,0,0.045);
            transition: transform 150ms ease, box-shadow 150ms ease, border-color 150ms ease;
        }
        .mood-day:hover {
            transform: translateY(-2px);
            box-shadow: 0 16px 32px rgba(0,0,0,0.08);
            border-color: rgba(10,132,255,0.28);
        }
        .mood-day-number {
            font-size: clamp(24px, 2.2vw, 32px);
            line-height: 1;
            font-weight: 720;
            color: #1D1D1F;
        }
        .mood-empty { min-height: 82px; }
        .mood-unrecorded { background: #F5F5F7; }
        .mood-calm { background: #DDEADF; }
        .mood-mild { background: #F3E7B3; }
        .mood-alert { background: #E8D7DF; }
        .mood-high { background: #F6D6DA; }
        .mood-review { background: #D9E6F5; }
        .mood-regret { background: #D9E6F5; }
        .mood-numb { background: #E3DDF0; }
        .mood-selected {
            border: 2px solid var(--blue);
            box-shadow: 0 18px 36px rgba(10,132,255,0.22);
        }
        .mood-high .mood-day-number { color: #1D1D1F; }
        .mood-alert .mood-day-number { color: #1D1D1F; }
        .mood-review .mood-day-number { color: #1D1D1F; }
        .mood-cell-form [data-testid="stForm"] { border: 0 !important; padding: 0 !important; background: transparent !important; box-shadow: none !important; }
        .mood-cell-form div.stButton > button, .mood-cell-form [data-testid="stFormSubmitButton"] button {
            min-height: 82px !important;
            width: 100% !important;
            border-radius: 18px !important;
            font-size: 28px !important;
            font-weight: 600 !important;
            line-height: 1 !important;
            padding: 0 !important;
            color: #1D1D1F !important;
            border: 1px solid rgba(0,0,0,0.05) !important;
            box-shadow: 0 10px 22px rgba(0,0,0,0.045) !important;
        }
        .mood-cell-form div.stButton > button:hover, .mood-cell-form [data-testid="stFormSubmitButton"] button:hover {
            transform: translateY(-2px);
            box-shadow: 0 16px 32px rgba(0,0,0,0.08) !important;
            border-color: rgba(10,132,255,0.28) !important;
        }
        .mood-cell-form.calm [data-testid="stFormSubmitButton"] button { background: #DDEADF !important; }
        .mood-cell-form.mild [data-testid="stFormSubmitButton"] button { background: #F3E7B3 !important; }
        .mood-cell-form.alert [data-testid="stFormSubmitButton"] button { background: #E8D7DF !important; color: #1D1D1F !important; }
        .mood-cell-form.high [data-testid="stFormSubmitButton"] button { background: #F6D6DA !important; color: #1D1D1F !important; }
        .mood-cell-form.review [data-testid="stFormSubmitButton"] button { background: #D9E6F5 !important; color: #1D1D1F !important; }
        .mood-cell-form.unrecorded [data-testid="stFormSubmitButton"] button { background: #F5F5F7 !important; }
        .mood-cell-form.selected [data-testid="stFormSubmitButton"] button {
            border: 2px solid var(--blue) !important;
            box-shadow: 0 18px 36px rgba(10,132,255,0.22) !important;
        }
        </style>
    """, unsafe_allow_html=True)


def render_html_card(inner_html, class_name="apple-card"):
    st.markdown(f'<div class="{class_name}">{inner_html}</div>', unsafe_allow_html=True)


def render_dashboard_hero():
    st.markdown(
        """
        <div class="apple-hero">
            <div class="hero-kicker">Fund Investor Emotion Management Agent</div>
            <div class="hero-title">基金投资情绪管理 Agent</div>
            <div class="hero-subtitle">记录、识别并管理你的基金投资情绪波动。</div>
            <div class="hero-disclaimer">本工具不预测市场涨跌，不评价基金好坏，不提供买入、卖出、加仓、减仓建议。它只帮助你记录和识别投资情绪与行为偏差。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def latest_emotion_record(records):
    if not records:
        return {}
    return sorted(records, key=lambda item: item.get("record_date", ""))[-1]


def calculate_record_streak(records):
    dates = {to_date(record.get("record_date")) for record in records if record.get("record_date")}
    if not dates:
        return 0
    cursor = date.today()
    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def most_common_bias(records, days=7):
    today = date.today()
    recent = [record for record in records if 0 <= (today - to_date(record.get("record_date"))).days < days]
    biases = []
    for record in recent:
        biases.extend(record.get("ai_behavior_biases", []))
    if not biases:
        return "暂无"
    counts = count_by(biases)
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0][0]


def render_status_cards(records):
    today_record = emotion_for_day(records, date.today())
    latest = today_record or latest_emotion_record(records)
    emotion_text = latest.get("ai_emotion_label", "未记录") if latest else "未记录"
    risk_text = latest.get("ai_risk_level", "待观察") if latest else "待观察"
    streak = calculate_record_streak(records)
    bias = most_common_bias(records)
    cards = [
        ("今日情绪", emotion_text, "今天的情绪状态" if today_record else "今天还未打卡"),
        ("当前风险等级", risk_text, "基于最近一次记录"),
        ("连续记录", f"{streak} 天", "从今天向前连续计算"),
        ("本周主要偏差", bias, "最近 7 天出现最多"),
    ]
    parts = []
    for label, value, caption in cards:
        parts.append(
            '<div class="apple-card compact-card">'
            f'<div class="card-label">{escape(label)}</div>'
            f'<div class="card-value">{escape(str(value))}</div>'
            f'<div class="card-caption">{escape(caption)}</div>'
            '</div>'
        )
    st.markdown(f'<div class="status-card-grid">{"".join(parts)}</div>', unsafe_allow_html=True)

def render_user_strip():
    email = escape(st.session_state.get("email", "未绑定邮箱"))
    user_id = escape(str(st.session_state.get("user_id", "")))
    left, right = st.columns([5, 1])
    with left:
        st.markdown(f'<span class="user-pill">当前登录邮箱：{email}</span><span class="user-pill">user_id：{user_id}</span>', unsafe_allow_html=True)
    with right:
        if st.button("退出登录", use_container_width=True):
            logout()
            st.rerun()


def render_emotion_warning_card(records):
    summary = build_emotion_warning_summary(records)
    reasons = "".join([f"<div>{escape(item)}</div>" for item in summary["reasons"]])
    st.markdown(
        f"""
        <div class="warning-card" style="background:{summary['bg']}; border-color:{summary['border']};">
            <div class="warning-title">{escape(summary['title'])}</div>
            <div class="warning-message">{escape(summary['message'])}</div>
            <div class="reason-list">{reasons}</div>
            <div class="warning-reminder" style="background:{summary['reminder_bg']}; border:1px solid {summary['reminder_border']};">{escape(summary['reminder'])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def emotion_intensity(record):
    """Use the strongest of anxiety, FOMO, and impulse as a 0-10 emotion intensity."""
    return max(
        safe_int(record.get("anxiety_level")),
        safe_int(record.get("fomo_level")),
        safe_int(record.get("impulse_level")),
    )


def recent_emotion_records(records, days=30):
    today = date.today()
    recent = []
    for record in records:
        record_day = to_date(record.get("record_date"))
        if 0 <= (today - record_day).days <= days - 1:
            recent.append(record)
    return sorted(recent, key=lambda item: item.get("record_date", ""))


def emotion_distribution(records):
    counts = {}
    for record in records:
        emotion = normalize_emotion_choice(record.get("strongest_emotion") or record.get("ai_emotion_label") or "未记录")
        if emotion not in EMOTION_COLOR_MAP:
            emotion = "未记录"
        counts[emotion] = counts.get(emotion, 0) + 1
    return counts


def dominant_item(counts, fallback="暂无"):
    if not counts:
        return fallback, 0
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[0]


def build_ai_emotion_review(records):
    """Build an AI-style emotion review from saved AI analysis fields without extra API calls."""
    recent = recent_emotion_records(records, 30)
    if not recent:
        return {
            "overall": "暂无足够记录",
            "main_emotion": "待观察",
            "pattern": "完成几次情绪记录后，系统会开始识别你的投资情绪节奏。",
            "suggestion": "先保持每日轻量记录，重点观察市场波动时自己的第一反应。",
            "profile": "记录积累中",
            "profile_desc": "目前样本不足，暂不判断长期情绪模式。",
            "avg_intensity": 0,
            "high_days": 0,
            "bias_counts": {},
        }

    intensities = [emotion_intensity(record) for record in recent]
    avg_intensity = round(sum(intensities) / len(intensities), 1)
    high_days = sum(1 for value in intensities if value >= 7)
    medium_days = sum(1 for value in intensities if 4 <= value < 7)
    emotion_counts = emotion_distribution(recent)
    main_emotion, main_count = dominant_item(emotion_counts, "待观察")

    biases = []
    for record in recent:
        biases.extend(record.get("ai_behavior_biases", []) or [])
    bias_counts = count_by([str(item) for item in biases if str(item).strip()])
    top_bias, top_bias_count = dominant_item(bias_counts, "暂无明显偏差")

    overcheck_days = sum(1 for record in recent if record.get("account_check_frequency") == "反复查看")
    fomo_days = sum(1 for record in recent if safe_int(record.get("fomo_level")) >= 7)
    anxiety_days = sum(1 for record in recent if safe_int(record.get("anxiety_level")) >= 7)
    impulse_days = sum(1 for record in recent if safe_int(record.get("impulse_level")) >= 7)

    if avg_intensity <= 3.2 and high_days == 0:
        overall = "整体情绪较稳定"
        profile = "稳定执行型"
        profile_desc = "根据历史行为表现，目前更接近稳定执行模式：多数记录没有明显高强度情绪，操作冲动相对可控。"
    elif high_days >= 4 or avg_intensity >= 6.5:
        overall = "近期情绪波动偏强"
        profile = "情绪驱动型"
        profile_desc = "根据历史行为表现，目前更接近情绪驱动模式：高强度情绪出现较多，容易让短期感受影响投资注意力。"
    elif anxiety_days >= max(fomo_days, impulse_days, overcheck_days):
        overall = "焦虑敏感度偏高"
        profile = "敏感波动型"
        profile_desc = "根据历史行为表现，目前更接近敏感波动模式：市场变化或账户波动更容易引发焦虑和反复确认。"
    elif overcheck_days >= 3:
        overall = "过度关注信号较明显"
        profile = "高关注观察型"
        profile_desc = "根据历史行为表现，目前更接近高关注观察模式：不一定频繁操作，但容易被账户变化持续牵引注意力。"
    else:
        overall = "情绪有波动但仍可观察"
        profile = "温和波动型"
        profile_desc = "根据历史行为表现，目前更接近温和波动模式：存在阶段性情绪起伏，但尚未形成持续高风险节奏。"

    pattern_parts = []
    if top_bias_count:
        pattern_parts.append(f"最常出现的行为信号是“{top_bias}”，过去30天出现 {top_bias_count} 次。")
    if fomo_days:
        pattern_parts.append(f"有 {fomo_days} 天 FOMO 分数达到高位，说明上涨或外部刺激可能更容易激活追随感。")
    if anxiety_days:
        pattern_parts.append(f"有 {anxiety_days} 天焦虑分数达到高位，说明波动压力会更快进入情绪层面。")
    if overcheck_days:
        pattern_parts.append(f"有 {overcheck_days} 天出现反复查看账户，注意力本身已经成为需要观察的信号。")
    if not pattern_parts:
        pattern_parts.append("目前没有特别集中的行为偏差信号，重点是继续记录，让模式更稳定。")

    suggestion = "建议把高峰日期当作复盘样本：回看当时的触发源、情绪强度和是否产生操作冲动，而不是把它直接当作行动理由。"
    if high_days == 0 and avg_intensity <= 3.2:
        suggestion = "建议继续保持低负担记录，重点观察稳定状态是否能在市场波动时延续。"

    return {
        "overall": overall,
        "main_emotion": f"{main_emotion}（{main_count} 天）",
        "pattern": " ".join(pattern_parts),
        "suggestion": suggestion,
        "profile": profile,
        "profile_desc": profile_desc,
        "avg_intensity": avg_intensity,
        "high_days": high_days,
        "medium_days": medium_days,
        "bias_counts": bias_counts,
        "emotion_counts": emotion_counts,
    }


def render_ai_review_summary_card(insight, recent_count):
    render_html_card(
        f"""
        <div class="card-label">AI 投资情绪总结</div>
        <div class="review-title">{escape(insight['overall'])}</div>
        <div class="review-summary-grid">
            <div><span>周期</span><strong>最近30天 · {recent_count} 条记录</strong></div>
            <div><span>主要情绪</span><strong>{escape(insight['main_emotion'])}</strong></div>
            <div><span>平均强度</span><strong>{escape(str(insight['avg_intensity']))}/10</strong></div>
            <div><span>高峰天数</span><strong>{escape(str(insight['high_days']))} 天</strong></div>
        </div>
        <div class="insight-block-title">AI发现的行为模式</div>
        <div class="ai-reminder compact">{escape(insight['pattern'])}</div>
        <div class="insight-block-title muted">观察建议</div>
        <div class="card-caption">{escape(insight['suggestion'])}</div>
        """,
        "apple-card ai-review-card",
    )


def build_emotion_line_svg(recent):
    if not recent:
        return ""

    width, height = 760, 260
    pad_left, pad_right, pad_top, pad_bottom = 46, 26, 24, 46
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    values = [emotion_intensity(record) for record in recent]
    n = max(len(recent), 1)

    def x_at(index):
        if n == 1:
            return pad_left + plot_w / 2
        return pad_left + plot_w * index / (n - 1)

    def y_at(value):
        return pad_top + plot_h * (1 - max(0, min(10, value)) / 10)

    points = [(x_at(i), y_at(value), value, recent[i]) for i, value in enumerate(values)]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in points)
    area = f"{pad_left},{pad_top + plot_h} " + polyline + f" {pad_left + plot_w},{pad_top + plot_h}"
    max_value = max(values)
    high_threshold = max(7, max_value)
    peak_dots = []
    for x, y, value, record in points:
        if value >= high_threshold:
            peak_dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#FF9F0A" stroke="#FFFFFF" stroke-width="3" />'
                f'<text x="{x:.1f}" y="{max(16, y - 12):.1f}" text-anchor="middle" font-size="11" fill="#6E6E73">{escape(record.get("record_date", "")[-5:])}</text>'
            )
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#0A84FF" opacity="0.75" />'
        for x, y, _, _ in points
    )
    grid = "".join(
        f'<line x1="{pad_left}" y1="{y_at(v):.1f}" x2="{pad_left + plot_w}" y2="{y_at(v):.1f}" stroke="rgba(0,0,0,0.055)" />'
        f'<text x="{pad_left - 12}" y="{y_at(v) + 4:.1f}" text-anchor="end" font-size="11" fill="#8E8E93">{v}</text>'
        for v in [0, 5, 10]
    )
    start_label = escape(recent[0].get("record_date", "")[-5:])
    end_label = escape(recent[-1].get("record_date", "")[-5:])
    return f"""
    <svg class="emotion-line-svg" viewBox="0 0 {width} {height}" role="img" aria-label="过去30天情绪强度折线图">
        <rect x="0" y="0" width="{width}" height="{height}" rx="22" fill="#FFFFFF" />
        {grid}
        <polygon points="{area}" fill="rgba(10,132,255,0.10)" />
        <polyline points="{polyline}" fill="none" stroke="#0A84FF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />
        {dots}
        {''.join(peak_dots)}
        <text x="{pad_left}" y="{height - 14}" font-size="12" fill="#8E8E93">{start_label}</text>
        <text x="{pad_left + plot_w}" y="{height - 14}" text-anchor="end" font-size="12" fill="#8E8E93">{end_label}</text>
        <text x="{pad_left}" y="16" font-size="12" fill="#8E8E93">情绪强度 0-10</text>
    </svg>
    """


def render_emotion_trajectory(recent):
    # A component iframe makes the SVG render reliably instead of exposing the
    # markup as text in Streamlit markdown.
    with st.container(border=True):
        st.markdown("**情绪波动轨迹**")
        st.caption("过去30天情绪强度折线。高峰日期会被标记，点击下方日期可查看当天详情。")
        components.html(build_emotion_line_svg(recent), height=290, scrolling=False)

    peaks = sorted(recent, key=lambda record: emotion_intensity(record), reverse=True)[:3]
    peaks = [record for record in peaks if emotion_intensity(record) > 0]
    if peaks:
        st.caption("高峰日期")
        cols = st.columns(min(len(peaks), 3))
        for col, record in zip(cols, peaks):
            day = to_date(record.get("record_date"))
            if col.button(f"{day.strftime('%m-%d')} · {emotion_intensity(record)}/10", use_container_width=True, key=f"review_peak_{record.get('record_date')}"):
                st.session_state.selected_emotion_date = day
                st.rerun()


def behavior_pattern_explanation(pattern_name):
    mapping = {
        "FOMO": "怕错过信号通常来自上涨、他人观点或热门叙事。它不一定意味着会行动，但会让注意力更容易被短期刺激牵引。",
        "亏损厌恶": "亏损厌恶会让账户回撤带来的痛感被放大，进而推动补救、回避或反复确认。",
        "过度看盘": "过度看盘本身不是交易行为，但会提高情绪暴露频率，让波动更容易进入决策。",
        "羊群效应": "羊群效应意味着他人行为可能被误读成有效信号，需要区分事实、观点和情绪传染。",
        "可得性偏差": "最近看到的信息更容易被大脑高估重要性，尤其是新闻、热榜和短期涨跌。",
        "冲动交易倾向": "冲动倾向说明行动欲望先于复盘出现，重点是延长情绪和行动之间的距离。",
    }
    return mapping.get(pattern_name, "该模式来自历史记录中的 AI 标签和情绪分数，适合用作复盘观察点，而不是行动依据。")


def render_behavior_pattern_cards(insight):
    counts = insight.get("bias_counts", {})
    if not counts:
        with st.container(border=True):
            st.markdown("**行为模式观察**")
            st.markdown("### 暂无集中的行为偏差信号")
            st.caption("继续记录后，系统会把反复出现的情绪触发源整理成观察卡片。")
        return

    top_items = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:3]
    cards = []
    for name, count in top_items:
        cards.append(
            f"""
            <div class="pattern-card">
                <div class="card-label">出现 {escape(str(count))} 次</div>
                <div class="pattern-title">{escape(str(name))}</div>
                <div class="card-caption">{escape(behavior_pattern_explanation(str(name)))}</div>
            </div>
            """
        )
    components.html(
        f"""
        <style>
        body {{ margin: 0; background: transparent; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1D1D1F; }}
        .label {{ color: #6E6E73; font-size: 13px; font-weight: 650; margin-bottom: 12px; }}
        .pattern-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
        .pattern-card {{ background: #F5F5F7; border: 1px solid rgba(0,0,0,.04); border-radius: 20px; padding: 16px; min-height: 132px; box-sizing: border-box; }}
        .card-label {{ color: #6E6E73; font-size: 12px; font-weight: 650; }}
        .pattern-title {{ font-size: 18px; font-weight: 760; margin: 8px 0; }}
        .card-caption {{ color: #6E6E73; font-size: 13px; line-height: 1.6; }}
        </style>
        <div class="label">行为模式观察</div>
        <div class="pattern-grid">{"".join(cards)}</div>
        """,
        height=205,
        scrolling=False,
    )


def build_donut_segments(counts):
    total = sum(counts.values())
    if total <= 0:
        return "#F5F5F7 0deg 360deg"
    current = 0
    segments = []
    for emotion, value in counts.items():
        degrees = value / total * 360
        color = emotion_color_map(emotion).get("bg", "#F5F5F7")
        segments.append(f"{color} {current:.1f}deg {current + degrees:.1f}deg")
        current += degrees
    return ", ".join(segments)


def render_emotion_composition(counts):
    if not counts:
        with st.container(border=True):
            st.markdown("**情绪组成分析**")
            st.markdown("### 暂无组成数据")
            st.caption("完成情绪记录后，这里会显示不同情绪的占比。")
        return

    total = sum(counts.values())
    legend = []
    for emotion, value in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        percent = round(value / total * 100)
        style = emotion_color_map(emotion)
        legend.append(
            f"""
            <div class="donut-legend-item">
                <span style="background:{style['bg']};"></span>
                <strong>{escape(emotion)}</strong>
                <em>{escape(str(percent))}%</em>
            </div>
            """
        )
    components.html(
        f"""
        <style>
        body {{ margin: 0; background: transparent; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1D1D1F; }}
        .label {{ color: #6E6E73; font-size: 13px; font-weight: 650; margin-bottom: 14px; }}
        .donut-layout {{ display: grid; grid-template-columns: 210px 1fr; gap: 22px; align-items: center; }}
        .donut {{ width: 188px; height: 188px; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: inset 0 0 0 1px rgba(0,0,0,.04); }}
        .donut > div {{ width: 108px; height: 108px; border-radius: 50%; background: #FFFFFF; display: flex; flex-direction: column; align-items: center; justify-content: center; }}
        .donut strong {{ font-size: 21px; }} .donut span {{ color: #6E6E73; font-size: 12px; margin-top: 5px; }}
        .donut-legend {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
        .donut-legend-item {{ display: flex; align-items: center; gap: 8px; background: #F8F8FA; border-radius: 999px; padding: 10px 12px; }}
        .donut-legend-item span {{ width: 18px; height: 18px; border-radius: 50%; border: 1px solid rgba(0,0,0,.04); }}
        .donut-legend-item strong {{ font-size: 13px; flex: 1; }} .donut-legend-item em {{ color: #6E6E73; font-size: 12px; font-style: normal; }}
        </style>
        <div class="label">情绪组成分析</div>
        <div class="donut-layout">
            <div class="donut" style="background: conic-gradient({build_donut_segments(counts)});">
                <div><strong>{escape(str(total))}</strong><span>条记录</span></div>
            </div>
            <div class="donut-legend">{"".join(legend)}</div>
        </div>
        """,
        height=260,
        scrolling=False,
    )


def inject_ai_review_styles():
    st.markdown(
        """
        <style>
        .ai-review-card { margin-top: 8px; }
        .review-title { font-size: 1.42rem; line-height: 1.25; font-weight: 780; color: var(--text-main); margin: 6px 0 16px 0; }
        .review-summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 12px 0 16px 0; }
        .review-summary-grid div { background: #F5F5F7; border-radius: 18px; padding: 14px 15px; border: 1px solid rgba(0,0,0,0.035); }
        .review-summary-grid span { display: block; color: var(--text-muted); font-size: 0.76rem; font-weight: 650; margin-bottom: 5px; }
        .review-summary-grid strong { color: var(--text-main); font-size: 0.98rem; }
        .ai-reminder.compact { font-size: 0.94rem; line-height: 1.65; font-weight: 560; }
        .insight-block-title { color: var(--text-main); font-size: 0.82rem; font-weight: 760; margin: 12px 0 7px 0; }
        .insight-block-title.muted { color: var(--text-muted); margin-top: 14px; }
        .emotion-line-svg { width: 100%; height: auto; margin-top: 16px; display: block; }
        .profile-card { background: linear-gradient(135deg, #FFFFFF 0%, #F5F8FF 100%); }
        .pattern-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-top: 12px; }
        .pattern-card { background: #F5F5F7; border: 1px solid rgba(0,0,0,0.04); border-radius: 20px; padding: 16px; min-height: 132px; }
        .pattern-title { color: var(--text-main); font-size: 1.12rem; font-weight: 760; margin: 7px 0; }
        .donut-layout { display: grid; grid-template-columns: 210px 1fr; gap: 22px; align-items: center; margin-top: 14px; }
        .donut { width: 188px; height: 188px; border-radius: 999px; display: flex; align-items: center; justify-content: center; box-shadow: inset 0 0 0 1px rgba(0,0,0,0.04); }
        .donut > div { width: 108px; height: 108px; border-radius: 999px; background: #FFFFFF; display: flex; flex-direction: column; align-items: center; justify-content: center; box-shadow: 0 6px 18px rgba(0,0,0,0.05); }
        .donut strong { color: var(--text-main); font-size: 1.3rem; line-height: 1; }
        .donut span { color: var(--text-muted); font-size: 0.76rem; margin-top: 5px; }
        .donut-legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
        .donut-legend-item { display: flex; align-items: center; gap: 8px; background: #F8F8FA; border-radius: 999px; padding: 10px 12px; color: var(--text-main); }
        .donut-legend-item span { width: 18px; height: 18px; border-radius: 999px; border: 1px solid rgba(0,0,0,0.04); }
        .donut-legend-item strong { font-size: 0.88rem; flex: 1; }
        .donut-legend-item em { font-size: 0.82rem; color: var(--text-muted); font-style: normal; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_emotion_charts(records):
    inject_ai_review_styles()
    st.markdown('<div class="section-title">AI投资情绪复盘</div>', unsafe_allow_html=True)
    if not records:
        render_html_card(
            '<div class="card-value">暂无足够数据</div><div class="card-caption">完成几次情绪记录后，这里会生成 AI 投资情绪总结、波动轨迹和长期画像。</div>',
            "apple-card",
        )
        return

    recent = recent_emotion_records(records, 30)
    if not recent:
        recent = sorted(records, key=lambda item: item.get("record_date", ""))[-30:]
    insight = build_ai_emotion_review(records)

    render_ai_review_summary_card(insight, len(recent))
    render_emotion_trajectory(recent)

    left, right = st.columns([1, 1], gap="medium")
    with left:
        render_html_card(
            f"""
            <div class="card-label">投资情绪画像</div>
            <div class="review-title">{escape(insight['profile'])}</div>
            <div class="card-caption">{escape(insight['profile_desc'])}</div>
            <div class="ai-reminder compact">这个画像基于当前历史情绪记录动态生成，会随着后续记录持续变化。</div>
            """,
            "apple-card profile-card",
        )
    with right:
        render_emotion_composition(insight.get("emotion_counts", {}))

    render_behavior_pattern_cards(insight)


@st.cache_data(ttl=60, show_spinner=False)
def cached_load_user_emotions(user_id):
    """Cache Supabase reads per authenticated user to keep the desktop dashboard fast."""
    return load_emotion_records(user_id)


def clear_emotion_cache():
    cached_load_user_emotions.clear()


def load_user_emotions():
    records = cached_load_user_emotions(st.session_state.user_id)
    error = get_last_emotion_records_error()
    if error:
        if "permission denied" in error.lower():
            st.warning("情绪记录数据库权限未配置，请检查 Supabase emotion_records 表权限。")
        else:
            st.warning("情绪记录暂时无法读取。")
            st.caption(f"具体原因：{error}")
    return records


EMOTION_COLOR_MAP = {
    "未记录": {"emoji": "", "bg": "#F5F5F7", "hover": "#ECEEF2", "fg": "#1D1D1F", "border": "rgba(0,0,0,0.045)", "label": "未记录"},
    "平静": {"emoji": "😊", "bg": "#DDEADF", "hover": "#D2E1D5", "fg": "#1D1D1F", "border": "rgba(52,120,74,0.13)", "label": "平静"},
    "焦虑": {"emoji": "😟", "bg": "#F3E7B3", "hover": "#EADCA0", "fg": "#1D1D1F", "border": "rgba(180,142,18,0.16)", "label": "焦虑"},
    "兴奋": {"emoji": "🤩", "bg": "#CFEFE8", "hover": "#C2E6DE", "fg": "#1D1D1F", "border": "rgba(45,150,132,0.14)", "label": "兴奋"},
    "后悔": {"emoji": "😔", "bg": "#D9E6F5", "hover": "#CADCEF", "fg": "#1D1D1F", "border": "rgba(10,132,255,0.13)", "label": "后悔"},
    "恐惧": {"emoji": "😨", "bg": "#F6D6DA", "hover": "#EDC5CB", "fg": "#1D1D1F", "border": "rgba(205,68,78,0.15)", "label": "恐惧"},
    "贪婪": {"emoji": "🤑", "bg": "#F9DFC2", "hover": "#F0D1AD", "fg": "#1D1D1F", "border": "rgba(200,126,42,0.15)", "label": "贪婪"},
    "烦躁": {"emoji": "😣", "bg": "#E8D7DF", "hover": "#DDC9D3", "fg": "#1D1D1F", "border": "rgba(150,72,102,0.14)", "label": "烦躁"},
    "麻木": {"emoji": "😶", "bg": "#E3DDF0", "hover": "#D8D0E8", "fg": "#1D1D1F", "border": "rgba(106,76,147,0.13)", "label": "麻木"},
}

EMOTION_ORDER = ["平静", "焦虑", "兴奋", "后悔", "恐惧", "贪婪", "烦躁", "麻木"]

EMOTION_KEY_ALIASES = {
    "unrecorded": "未记录",
    "calm": "平静",
    "mild": "焦虑",
    "high": "恐惧",
    "regret": "后悔",
    "alert": "烦躁",
    "numb": "麻木",
    "review": "后悔",
    "犹豫": "焦虑",
    "FOMO": "贪婪",
    "冲动": "烦躁",
}

RISK_COLOR_MAP = {
    "low": {"bg": "#EAF4EE", "border": "#CFE3D5", "reminder_bg": "#F5FBF7", "reminder_border": "#D8E9DD", "title": "情绪状态稳定"},
    "medium": {"bg": "#FFF6DD", "border": "#E7D8A8", "reminder_bg": "#FFF9EA", "reminder_border": "#EBDDB3", "title": "情绪波动提醒"},
    "elevated": {"bg": "#FFEBD6", "border": "#EAC7A5", "reminder_bg": "#FFF5EA", "reminder_border": "#EBD2B9", "title": "需要关注"},
    "high": {"bg": "#FCE1E1", "border": "#E4BFC1", "reminder_bg": "#FFF3F3", "reminder_border": "#EBCDCF", "title": "情绪风险提醒"},
}


def emotion_color_map(key):
    """Return the single source of truth for emotion colors."""
    normalized_key = EMOTION_KEY_ALIASES.get(str(key), str(key))
    return EMOTION_COLOR_MAP.get(normalized_key, EMOTION_COLOR_MAP["未记录"])


def normalize_emotion_choice(value):
    """Convert display text like '😊 平静' into the stored emotion name."""
    text = str(value or "").strip()
    for emotion_name, style in EMOTION_COLOR_MAP.items():
        if emotion_name != "未记录" and emotion_name in text:
            return emotion_name
    return EMOTION_KEY_ALIASES.get(text, text)


def emotion_display_label(emotion_name):
    """Return 'emoji + emotion' from the unified emotion style config."""
    style = emotion_color_map(emotion_name)
    emoji = style.get("emoji", "")
    label = style.get("label", str(emotion_name))
    return f"{emoji} {label}".strip()

def risk_color_map(level):
    """Return the single source of truth for top risk card colors."""
    return RISK_COLOR_MAP.get(level, RISK_COLOR_MAP["low"])

def mood_calendar_class(record):
    """Map one saved record to a persistent emotion key."""
    if not record:
        return "未记录"
    strongest = str(record.get("strongest_emotion", ""))
    label = str(record.get("ai_emotion_label", ""))
    risk = str(record.get("ai_risk_level", ""))
    if strongest in EMOTION_COLOR_MAP:
        return strongest
    if "恐惧" in label or "高风险" in label or risk == "高":
        return "恐惧"
    if "后悔" in label or "反刍" in label:
        return "后悔"
    if "麻木" in label:
        return "麻木"
    if "兴奋" in label:
        return "兴奋"
    if "贪婪" in label or "FOMO" in label:
        return "贪婪"
    if "烦躁" in label or "冲动" in label:
        return "烦躁"
    if "焦虑" in label or risk == "中":
        return "焦虑"
    return "平静"
def mood_class_from_form_value(value):
    """Preview a selected but unsaved emotion on the current calendar day."""
    return EMOTION_KEY_ALIASES.get(str(value), str(value)) if value else "未记录"

def mood_day_palette(mood_class):
    """Return calendar colors from the unified emotion color map."""
    return emotion_color_map(mood_class)

def render_emotion_calendar_grid(records):
    if "selected_emotion_date" not in st.session_state:
        st.session_state.selected_emotion_date = date.today()
    month_start = to_date(st.session_state.get("calendar_month", date.today().replace(day=1))).replace(day=1)
    selected_day = to_date(st.session_state.get("selected_emotion_date", date.today()))
    nav_cols = st.columns([1, 2.2, 1])
    with nav_cols[0]:
        if st.button("‹ 上个月", use_container_width=True):
            last_month = month_start - timedelta(days=1)
            st.session_state.calendar_month = last_month.replace(day=1)
            st.session_state.selected_emotion_date = last_month.replace(day=1)
            st.session_state.show_emotion_record_dialog = False
            st.rerun()
    with nav_cols[1]:
        st.markdown(f'<div class="section-title" style="text-align:center;">{month_start.year} 年 {month_start.month} 月</div>', unsafe_allow_html=True)
    with nav_cols[2]:
        if st.button("下个月 ›", use_container_width=True):
            next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            st.session_state.calendar_month = next_month
            st.session_state.selected_emotion_date = next_month
            st.session_state.show_emotion_record_dialog = False
            st.rerun()

    day_styles = []
    month_days = []
    for week in calendar.monthcalendar(month_start.year, month_start.month):
        week_days = []
        for day_num in week:
            if day_num == 0:
                week_days.append(None)
                continue
            current_day = date(month_start.year, month_start.month, day_num)
            record = emotion_for_day(records, current_day)
            mood_class = mood_calendar_class(record)
            if not record and current_day == selected_day:
                preview_value = st.session_state.get(f"emotion_{selected_day}_strongest")
                mood_class = mood_class_from_form_value(preview_value)
            key = f"mood_day_{current_day.strftime('%Y%m%d')}"
            colors = mood_day_palette(mood_class)
            selected_css = "border: 2px solid #0A84FF !important; box-shadow: 0 14px 30px rgba(10,132,255,0.18) !important;" if current_day == selected_day else ""
            day_styles.append(
                f""".st-key-{key} div.stButton > button,
                .st-key-{key} button {{
                    background: {colors['bg']} !important;
                    background-color: {colors['bg']} !important;
                    color: {colors['fg']} !important;
                    border: 1px solid {colors['border']} !important;
                    min-height: 82px !important;
                    border-radius: 18px !important;
                    font-size: 28px !important;
                    font-weight: 600 !important;
                    line-height: 1 !important;
                    padding: 0 !important;
                    box-shadow: 0 10px 22px rgba(0,0,0,0.045) !important;
                    {selected_css}
                }}
                .st-key-{key} div.stButton > button:hover,
                .st-key-{key} button:hover {{
                    background: {colors['hover']} !important;
                    background-color: {colors['hover']} !important;
                    transform: translateY(-2px);
                    box-shadow: 0 14px 28px rgba(0,0,0,0.075) !important;
                }}"""
            )
            week_days.append({"date": current_day, "day": day_num, "key": key})
        month_days.append(week_days)
    if day_styles:
        st.markdown("<style>" + "\n".join(day_styles) + "</style>", unsafe_allow_html=True)
        st.markdown(
            """
            <style>
            </style>
            """,
            unsafe_allow_html=True,
        )

    for col, label in zip(st.columns(7), ["一", "二", "三", "四", "五", "六", "日"]):
        col.markdown(f'<div class="mood-weekday">{escape(label)}</div>', unsafe_allow_html=True)
    for week_days in month_days:
        day_cols = st.columns(7)
        for col, info in zip(day_cols, week_days):
            if info is None:
                col.markdown('<div class="mood-empty"></div>', unsafe_allow_html=True)
                continue
            with col:
                if st.button(str(info["day"]), key=info["key"], use_container_width=True):
                    st.session_state.selected_emotion_date = info["date"]
                    st.session_state.edit_emotion_record = False
                    st.session_state.show_emotion_record_dialog = False
                    st.rerun()


def save_emotion_record_with_ai(selected_day, account_check_frequency, strongest_emotion, operation_impulse, impulse_source, actual_action, anxiety_level, fomo_level, impulse_level, note):
    """Build, analyze, and persist one daily emotion record."""
    with st.status("AI 正在识别今天的投资情绪...", expanded=True) as status:
        st.write("整理记录")
        st.write("调用 DeepSeek")
        new_record = build_emotion_record(selected_day, account_check_frequency, strongest_emotion, operation_impulse, impulse_source, actual_action, anxiety_level, fomo_level, impulse_level, note)
        st.write("生成情绪标签")
        st.write("保存到日历")
        try:
            upsert_emotion_record(st.session_state.user_id, new_record)
            clear_emotion_cache()
        except Exception as error:
            status.update(label="保存失败", state="error")
            st.error(f"保存失败：\nSupabase error: {error}")
            return False
        analysis = new_record.get("ai_analysis", {})
        if analysis.get("fallback"):
            st.session_state.emotion_notice = "AI 分析暂时失败，系统已根据规则生成兜底情绪标签。保存成功：emotion_records 已写入 Supabase。"
            status.update(label="已使用规则兜底完成分析", state="complete")
        else:
            st.session_state.emotion_notice = "保存成功：emotion_records 已写入 Supabase。"
            status.update(label="AI 情绪分析已完成", state="complete")
    return True


if hasattr(st, "dialog"):
    @st.dialog("记录今日情绪")
    def render_emotion_record_dialog(selected_day, record=None):
        render_emotion_record_form(selected_day, record, in_dialog=True)
else:
    def render_emotion_record_dialog(selected_day, record=None):
        st.warning("当前 Streamlit 版本不支持浮窗，已在页面内显示记录入口。")
        render_emotion_record_form(selected_day, record, in_dialog=False)


def render_emotion_record_form(selected_day, record=None, in_dialog=False):
    st.caption(selected_day.strftime("%Y 年 %m 月 %d 日"))
    strongest_emotion_choice = st.radio("今日最强烈投资情绪", [emotion_display_label(name) for name in EMOTION_ORDER], horizontal=True, key=f"emotion_{selected_day}_strongest")
    strongest_emotion = normalize_emotion_choice(strongest_emotion_choice)
    operation_impulse = st.radio("是否产生操作冲动", ["没有", "想买入 / 加仓", "想卖出 / 减仓", "想暂停定投", "想改变原计划", "只是想反复看盘"], horizontal=False, key=f"emotion_{selected_day}_impulse")
    if operation_impulse == "没有":
        impulse_source = "无"
    else:
        impulse_source = st.selectbox("冲动来源", ["市场上涨", "市场下跌", "新闻刺激", "朋友 / 博主观点", "持仓亏损", "持仓盈利", "临近定投日", "其他"], key=f"emotion_{selected_day}_source")
    actual_action = st.selectbox("是否实际操作", ["没有操作", "按计划执行", "计划外买入 / 加仓", "计划外卖出 / 减仓", "暂停 / 跳过", "只是记录情绪"], key=f"emotion_{selected_day}_action")
    account_check_frequency = st.radio("查看账户频率", ["没看", "看了一次", "看了几次", "反复查看"], horizontal=True, key=f"emotion_{selected_day}_check")
    anxiety_level = st.slider("焦虑程度", 0, 10, int(record.get("anxiety_level", 0) if record else 0), key=f"emotion_{selected_day}_anxiety")
    fomo_level = st.slider("FOMO 程度", 0, 10, int(record.get("fomo_level", 0) if record else 0), key=f"emotion_{selected_day}_fomo")
    impulse_level = st.slider("冲动程度", 0, 10, int(record.get("impulse_level", 0) if record else 0), key=f"emotion_{selected_day}_impulse_level")
    note = st.text_area("一句话记录", value=record.get("note", "") if record else "", placeholder="例如：今天看到市场上涨，感觉如果不买就会错过。", key=f"emotion_{selected_day}_note")
    form_actions = st.columns(2)
    if form_actions[0].button("取消", use_container_width=True, key=f"emotion_{selected_day}_cancel"):
        st.session_state.show_emotion_record_dialog = False
        st.rerun()
    if form_actions[1].button("生成今日情绪分析", use_container_width=True, type="primary", key=f"emotion_{selected_day}_submit"):
        saved = save_emotion_record_with_ai(selected_day, account_check_frequency, strongest_emotion, operation_impulse, impulse_source, actual_action, anxiety_level, fomo_level, impulse_level, note)
        if saved:
            st.session_state.edit_emotion_record = False
            st.session_state.show_emotion_record_dialog = False
            st.rerun()


def render_emotion_day_panel(records):
    selected_day = to_date(st.session_state.get("selected_emotion_date", date.today()))
    record = emotion_for_day(records, selected_day)
    weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][selected_day.weekday()]
    status_text = "已记录" if record else "未记录"
    render_html_card(
        f'<div class="card-label">{escape(weekday)}</div><div class="date-heading">{escape(selected_day.strftime("%Y 年 %m 月 %d 日"))}</div><div class="card-caption">状态：{escape(status_text)}</div>',
        "apple-card",
    )

    if record:
        mood_colors = mood_day_palette(mood_calendar_class(record))
        bias_html = "".join([f'<span class="bias-chip">{escape(str(item))}</span>' for item in record.get("ai_behavior_biases", [])]) or '<span class="bias-chip">暂无</span>'
        render_html_card(
            f"""
            <div class="emotion-status-panel" style="background:{mood_colors['bg']}; border-color:{mood_colors['border']};">
                <div class="card-label">当天状态</div>
                <div class="card-value">{escape(record.get('ai_emotion_label', '已记录'))}</div>
                <div class="card-caption">风险等级：{escape(record.get('ai_risk_level', '未分析'))}</div>
            </div>
            <div class="score-row">
                <div class="score-chip"><div class="score-num">{escape(str(record.get('anxiety_level', 0)))}</div><div class="score-label">焦虑</div></div>
                <div class="score-chip"><div class="score-num">{escape(str(record.get('fomo_level', 0)))}</div><div class="score-label">FOMO</div></div>
                <div class="score-chip"><div class="score-num">{escape(str(record.get('impulse_level', 0)))}</div><div class="score-label">冲动</div></div>
            </div>
            <div style="margin-top:14px;">{bias_html}</div>
            <div class="ai-reminder">{escape(record.get('ai_reminder', '暂无提醒'))}</div>
            <div class="card-caption">{escape(record.get('ai_observation_point', ''))}</div>
            """,
            "apple-card",
        )
        b1, b2, b3 = st.columns(3)
        if b1.button("编辑记录", use_container_width=True):
            st.session_state.show_emotion_record_dialog = True
        if b2.button("重新分析", use_container_width=True):
            updated = dict(record)
            with st.status("AI 正在识别今天的投资情绪...", expanded=True) as status:
                st.write("整理记录")
                st.write("调用 DeepSeek")
                analysis = analyze_emotion_safely(updated)
                st.write("生成情绪标签")
                updated.update({"ai_emotion_label": analysis.get("emotion_label"), "ai_risk_level": analysis.get("risk_level"), "ai_behavior_biases": analysis.get("behavior_biases", []), "ai_reminder": analysis.get("one_sentence_reminder"), "ai_observation_point": analysis.get("observation_point"), "ai_analysis": analysis})
                st.write("保存到日历")
                try:
                    upsert_emotion_record(st.session_state.user_id, updated)
                    clear_emotion_cache()
                except Exception as error:
                    status.update(label="保存失败", state="error")
                    st.error(f"保存失败：\nSupabase error: {error}")
                    return
                if analysis.get("fallback"):
                    st.session_state.emotion_notice = "AI 分析暂时失败，系统已根据规则生成兜底情绪标签。保存成功：emotion_records 已写入 Supabase。"
                    status.update(label="已使用规则兜底完成分析", state="complete")
                else:
                    st.session_state.emotion_notice = "保存成功：emotion_records 已写入 Supabase。"
                    status.update(label="AI 情绪分析已完成", state="complete")
            st.rerun()
        if b3.button("删除记录", use_container_width=True):
            delete_emotion_record(st.session_state.user_id, selected_day.strftime("%Y-%m-%d"))
            clear_emotion_cache()
            st.session_state.edit_emotion_record = False
            st.rerun()
    else:
        render_html_card('<div class="card-label">当天状态</div><div class="card-value">今天还没有记录</div><div class="card-caption">点击下方按钮，用 30 秒记录今天的投资情绪。</div>', "apple-card")
        if st.button("+ 记录今日情绪", use_container_width=True, type="primary"):
            st.session_state.show_emotion_record_dialog = True

    if st.session_state.get("show_emotion_record_dialog"):
        render_emotion_record_dialog(selected_day, record)


def render_emotion_color_legend():
    """Render a small Apple-like legend from the unified emotion color map."""
    legend_order = EMOTION_ORDER
    pills = "".join(
        f'<div class="emotion-pill" style="background:{emotion_color_map(name)["bg"]};">{escape(emotion_display_label(name))}</div>'
        for name in legend_order
    )
    st.markdown(
        f"""
        <div class="emotion-legend-card">
            <div class="emotion-legend-title">情绪颜色</div>
            <div class="emotion-legend-grid">{pills}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def render_desktop_page(records):
    render_status_cards(records)
    render_emotion_warning_card(records)
    left, right = st.columns([1.1, 0.9], gap="medium")
    with left:
        render_html_card('<div class="section-title">情绪日历</div><div class="calendar-note">日期颜色来自当日情绪标签与风险等级。</div>', "apple-card")
        render_emotion_calendar_grid(records)
    with right:
        render_emotion_day_panel(records)
    render_emotion_color_legend()
    st.divider()
    render_emotion_charts(records)


def render_emotion_calendar_page():
    records = load_user_emotions()
    render_desktop_page(records)

inject_design_tokens()
current_user = get_authenticated_user()
if not current_user:
    render_login_page()
    st.stop()

user_id = get_current_user_id(current_user)
if not user_id:
    st.error("\u7528\u6237\u72b6\u6001\u521d\u59cb\u5316\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u518d\u8bd5\u3002")
    st.stop()

try:
    ensure_data_files(user_id)
except Exception as error:
    st.error("\u7528\u6237\u6570\u636e\u76ee\u5f55\u521d\u59cb\u5316\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u9875\u9762\u540e\u91cd\u8bd5\u3002")
    st.exception(error)
    st.stop()

if st.session_state.get("active_user_id") != user_id:
    st.session_state.active_user_id = user_id
    for key in [
        "selected_fund_plan",
        "current_operation",
        "answers",
        "behavior_answers",
        "diagnosis_report",
        "diagnosis_in_progress",
        "operation_history",
        "current_step",
        "answered_count",
        "qa_step",
        "step_index",
        "diagnosis_record_key",
        "show_create_plan_form",
        "plan_form_mode",
        "editing_plan_id",
        "delete_confirm_plan_id",
        "active_tab",
        "pending_tab",
        "quick_operation_type",
        "calendar_month",
        "selected_calendar_date",
        "selected_emotion_date",
        "edit_emotion_record",
        "emotion_notice",
    ]:
        st.session_state.pop(key, None)

try:
    initialize_state()
    if "calendar_month" not in st.session_state:
        st.session_state.calendar_month = date.today().replace(day=1)
    if "selected_calendar_date" not in st.session_state:
        st.session_state.selected_calendar_date = date.today()
    if "selected_emotion_date" not in st.session_state:
        st.session_state.selected_emotion_date = date.today()
except Exception as error:
    st.error("\u9875\u9762\u521d\u59cb\u5316\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u8bd5\u3002")
    st.exception(error)
    st.stop()

render_dashboard_hero()
render_user_strip()

notice = st.session_state.pop("emotion_notice", "") if "emotion_notice" in st.session_state else ""
if notice:
    if "\u515c\u5e95" in notice or "\u6682\u65f6\u5931\u8d25" in notice:
        st.warning(notice)
    else:
        st.success(notice)

render_emotion_calendar_page()
