"""Streamlit web app for the Fund DCA Decision Support Agent."""

from datetime import date, datetime
from html import escape
from uuid import uuid4

import streamlit as st

from agent_logic import generate_conversation_check, generate_operation_check
from data_manager import add_transaction, ensure_data_files, load_plans, load_transactions, save_plan
from llm_analyzer import analyze_operation_reason


st.set_page_config(page_title="基金定投辅助决策 Agent", page_icon="📌", layout="wide")

FINAL_DISCLAIMER = "本分析不构成投资建议，不预测市场涨跌，也不提供买入、卖出、加仓、减仓建议。"
QUESTION_FLOW = [
    {"key": "is_planned", "title": "是否符合定投计划？", "type": "radio", "options": ["是", "否"]},
    {"key": "emotion_reason", "title": "是否受短期波动影响？", "type": "radio", "options": ["无影响", "轻微影响", "明显影响"]},
    {"key": "cash_flow", "title": "是否影响现金流？", "type": "radio", "options": ["不会", "可能会", "会"]},
    {"key": "can_accept_loss", "title": "是否接受继续下跌 10%？", "type": "radio", "options": ["能接受", "不能接受"]},
]
def normalize_user_id(value):
    """Keep a temporary user id URL-safe and simple."""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = str(value or "").strip()
    cleaned = "".join(char for char in text if char.isalnum() or char in {"_", "-"})
    if cleaned.startswith("user_") and len(cleaned) >= 8:
        return cleaned
    return ""


def get_or_create_user_id():
    """Read a temporary user id from URL, or create one for this visitor."""
    query_user_id = normalize_user_id(st.query_params.get("user_id", ""))
    session_user_id = normalize_user_id(st.session_state.get("user_id", ""))

    user_id = query_user_id or session_user_id or f"user_{uuid4().hex[:6]}"
    if st.session_state.get("user_id") != user_id:
        st.session_state.user_id = user_id
    if st.query_params.get("user_id") != user_id:
        st.query_params["user_id"] = user_id
    return user_id


def format_money(amount):
    """Format money for display."""
    return f"{float(amount or 0):,.2f} 元"


def get_score_value(value):
    """Read score safely."""
    if isinstance(value, dict):
        return value.get("score")
    return value

def clamp_score(value):
    """Keep a score within 0 to 100."""
    return max(0, min(100, round(value)))


def contains_any(text, keywords):
    """Check whether text contains any keyword."""
    text = (text or "").lower()
    return any(keyword.lower() in text for keyword in keywords)


def get_bias_level(record, bias_name):
    """Read one AI diagnosis bias level from a transaction."""
    diagnosis = record.get("behavior_diagnosis") or {}
    for item in diagnosis.get("bias_dimensions", []):
        if item.get("name") == bias_name:
            return item.get("level", "低")
    return "低"


def build_investment_personality(records):
    """Build an investment personality profile from all historical operations."""
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

    personality_type, personality_summary = classify_personality(scores, out_plan_ratio, adjust_ratio)
    risk_level = classify_risk_level(scores)
    evidence = build_personality_evidence(
        total,
        out_plan_count,
        adjust_count,
        emotion_count,
        fomo_count,
        panic_count,
        friend_count,
        vague_count,
        clear_reason_count,
        cannot_accept_count,
        cash_warning_count,
    )
    pattern_summary = build_behavior_pattern_summary(scores, evidence)
    suggestions = build_personality_suggestions(scores, evidence)

    return {
        "scores": scores,
        "personality_type": personality_type,
        "personality_summary": personality_summary,
        "risk_level": risk_level,
        "evidence": evidence,
        "pattern_summary": pattern_summary,
        "suggestions": suggestions,
        "stats": {
            "total": total,
            "out_plan_count": out_plan_count,
            "adjust_count": adjust_count,
            "emotion_count": emotion_count,
            "fomo_count": fomo_count,
            "panic_count": panic_count,
        },
    }


def classify_personality(scores, out_plan_ratio, adjust_ratio):
    """Classify investment personality by score combination."""
    discipline = scores["Discipline"]
    emotion = scores["Emotional Sensitivity"]
    fomo = scores["FOMO Tendency"]
    risk = scores["Risk Tolerance"]
    long_term = scores["Long-term Orientation"]

    if discipline >= 78 and long_term >= 72 and emotion <= 45:
        return "理性定投型（Rational DCA Investor）", "长期计划和纪律执行是主要特征，短期情绪对操作的影响相对有限。"
    if discipline >= 72 and long_term >= 78:
        return "纪律型长期投资者（Disciplined Investor）", "整体围绕计划行动，适合继续强化记录质量和预算边界。"
    if emotion >= 68 and fomo >= 55:
        return "情绪驱动型交易者（Emotion-driven Trader）", "操作较容易被波动、他人信息或错过感触发，需要重点管理情绪到行动之间的距离。"
    if fomo >= 65 and risk >= 55:
        return "机会捕捉型投资者（Opportunistic Investor）", "更容易关注市场机会和外部信号，需要避免把机会感误认为确定性。"
    if adjust_ratio >= 0.45 or out_plan_ratio >= 0.5:
        return "高频调整型（Reactive Adjuster）", "历史操作中临时调整占比较高，行为重点是建立更明确的触发规则。"
    return "平衡观察型投资者（Balanced Observer）", "当前画像没有极端倾向，适合继续积累记录，让人格评分更稳定。"


def classify_risk_level(scores):
    """Return risk personality level."""
    if scores["Emotional Sensitivity"] >= 70 or scores["Risk Tolerance"] <= 42:
        return "高敏感风险人格"
    if scores["FOMO Tendency"] >= 65 and scores["Discipline"] <= 60:
        return "机会冲动风险人格"
    if scores["Discipline"] >= 75 and scores["Risk Tolerance"] >= 65:
        return "稳健纪律风险人格"
    return "中性波动风险人格"


def build_personality_evidence(total, out_plan_count, adjust_count, emotion_count, fomo_count, panic_count, friend_count, vague_count, clear_reason_count, cannot_accept_count, cash_warning_count):
    """Build evidence bullets from historical behavior."""
    evidence = [f"历史样本共 {total} 次操作，画像会随着记录增加而更稳定。"]
    evidence.append(f"计划外或未确认计划内操作 {out_plan_count} 次，操作类型调整 {adjust_count} 次。")
    evidence.append(f"短期波动影响操作 {emotion_count} 次，理由中出现下跌、亏损或焦虑信号 {panic_count} 次。")
    evidence.append(f"理由中出现怕错过、朋友推荐、感觉会涨等 FOMO/羊群信号 {fomo_count + friend_count} 次。")
    evidence.append(f"理由较短或偏模糊 {vague_count} 次，包含长期、计划、预算、目标等清晰依据 {clear_reason_count} 次。")
    if cannot_accept_count or cash_warning_count:
        evidence.append(f"现金流或回撤承受压力信号 {cannot_accept_count + cash_warning_count} 次。")
    return evidence[:5]


def build_behavior_pattern_summary(scores, evidence):
    """Build a behavior-finance oriented personality summary."""
    return (
        "这个人格画像不是收益预测，而是把长期操作记录转化为心理结构。"
        f"当前情绪敏感度为 {scores['Emotional Sensitivity']}/100，FOMO 倾向为 {scores['FOMO Tendency']}/100，"
        "如果两者偏高，行为上会更接近行为金融学中的羊群效应和散户追涨结构：外部信息越密集，越容易把热闹误读为确定性。"
        f"纪律性为 {scores['Discipline']}/100，长期主义为 {scores['Long-term Orientation']}/100，"
        "这两个分数越高，说明用户越能用计划抵消短期噪音。类似2000年互联网泡沫中的追涨心理，真正的风险往往不是单次操作，而是反复让短期叙事覆盖原本规则。"
    )


def build_personality_suggestions(scores, evidence):
    """Build behavior-only improvement suggestions."""
    suggestions = []
    if scores["Emotional Sensitivity"] >= 60:
        suggestions.append("把“看到波动后的第一反应”和“最终操作理由”分开记录，降低情绪直接进入操作的概率。")
    if scores["Discipline"] < 70:
        suggestions.append("为计划外操作写固定模板：触发条件、金额上限、资金来源、复盘日期。")
    if scores["FOMO Tendency"] >= 55:
        suggestions.append("遇到朋友推荐、热门讨论或怕错过时，先记录信息来源，再检查它是否改变了原定目标。")
    if scores["Risk Tolerance"] < 65:
        suggestions.append("每次操作前保留现金流和继续下跌情景检查，避免让资金压力放大心理压力。")
    if scores["Long-term Orientation"] < 70:
        suggestions.append("把每次操作绑定到长期目标或预算规则，减少只因短期涨跌产生的临时调整。")
    if not suggestions:
        suggestions.append("继续保持操作前问答和理由记录，让长期人格画像更稳定。")
    return suggestions[:5]


def get_selected_plan(plans):
    """Return selected plan from session state."""
    selected = st.session_state.get("selected_fund_plan")
    if selected and any(plan.get("id") == selected.get("id") for plan in plans):
        return selected
    if plans:
        st.session_state.selected_fund_plan = plans[0]
        return plans[0]
    st.session_state.selected_fund_plan = {}
    return {}


def filter_history_for_plan(records, plan):
    """Filter operation history for current selected plan."""
    if not plan:
        return []
    plan_id = plan.get("id")
    fund_name = plan.get("fund_name")
    return [
        item for item in records
        if item.get("plan_id") == plan_id or item.get("fund_name") == fund_name
    ]


def merge_reminders(*groups):
    """Merge reminder lists while preserving order."""
    merged = []
    for group in groups:
        for item in group:
            if item not in merged:
                merged.append(item)
    return merged


def count_answered_questions(answers, questions):
    """Count answered wizard questions from session state."""
    values = [answers.get(question["key"]) for question in questions]
    return len([value for value in values if value is not None])


def sync_question_state():
    """Keep new wizard state and older behavior_answers in sync."""
    st.session_state.behavior_answers = dict(st.session_state.answers)
    st.session_state.qa_step = st.session_state.current_step
    st.session_state.answered_count = count_answered_questions(st.session_state.answers, QUESTION_FLOW)


def get_flow_statuses(operation_stage):
    """Return display statuses based on actual completion, not only current view."""
    has_operation = bool(st.session_state.get("current_operation"))
    answered_count = count_answered_questions(st.session_state.get("answers", {}), QUESTION_FLOW)
    qa_done = answered_count >= len(QUESTION_FLOW)
    diagnosis_done = bool(st.session_state.get("diagnosis_report"))
    diagnosis_running = bool(st.session_state.get("diagnosis_in_progress")) or (operation_stage == 2 and qa_done and not diagnosis_done)

    statuses = []
    statuses.append("已完成" if has_operation else ("进行中" if operation_stage == 0 else "待完成"))
    statuses.append("已完成" if qa_done else ("进行中" if operation_stage == 1 else "待完成"))
    statuses.append("已完成" if diagnosis_done else ("进行中" if diagnosis_running else "待完成"))
    return statuses

def show_behavior_chain(diagnosis):
    """Show a visual investment decision psychology chain."""
    chain = [
        item for item in (diagnosis.get("decision_chain") or [])
        if "纪律校验" not in str(item.get("stage", ""))
    ]
    if not chain:
        chain = [
            {"stage": "触发信号", "signal": "操作想法出现", "psychology": "短期价格、账户盈亏或外部信息进入注意力", "discipline_check": "先确认这是否属于原定计划"},
            {"stage": "注意力聚焦", "signal": "近期波动被放大", "psychology": "可得性偏差让最近看到的信息显得更重要", "discipline_check": "区分事实、情绪和他人观点"},
            {"stage": "参照点形成", "signal": "以成本、高点或计划金额作比较", "psychology": "锚定效应可能影响对风险的判断", "discipline_check": "检查当前金额是否仍在预算内"},
            {"stage": "情绪反应", "signal": "后悔、焦虑或怕错过", "psychology": "损失厌恶和自豪/悔恨会推动补救冲动", "discipline_check": "确认继续下跌10%时是否能接受"},
        ]

    st.caption("这条链路不是市场预测，而是把一次操作拆成：信息进入注意力、心理解释、操作冲动和纪律校验。")
    for index, item in enumerate(chain, start=1):
        stage = escape(str(item.get("stage", f"阶段 {index}")))
        signal = escape(str(item.get("signal", "暂无触发信号")))
        psychology = escape(str(item.get("psychology", "暂无心理机制")))
        check = escape(str(item.get("discipline_check", "回到计划、现金流和风险承受边界")))
        st.markdown(
            f"""
            <div style="display:flex;gap:14px;margin:0 0 12px 0;align-items:stretch;">
                <div style="width:38px;height:38px;border-radius:999px;background:#2563eb;color:white;display:flex;align-items:center;justify-content:center;font-weight:700;flex:0 0 auto;margin-top:8px;">{index}</div>
                <div style="border:1px solid #d8dee9;border-radius:8px;background:#f8fafc;padding:14px 16px;flex:1;">
                    <div style="font-size:18px;font-weight:700;color:#1f2937;margin-bottom:10px;">{stage}</div>
                    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
                        <div style="background:white;border-radius:6px;padding:10px 12px;border-left:3px solid #60a5fa;">
                            <div style="font-size:12px;color:#64748b;margin-bottom:4px;">触发信号</div>
                            <div style="line-height:1.6;color:#334155;">{signal}</div>
                        </div>
                        <div style="background:white;border-radius:6px;padding:10px 12px;border-left:3px solid #f59e0b;">
                            <div style="font-size:12px;color:#64748b;margin-bottom:4px;">心理解释</div>
                            <div style="line-height:1.6;color:#334155;">{psychology}</div>
                        </div>
                        <div style="background:white;border-radius:6px;padding:10px 12px;border-left:3px solid #22c55e;">
                            <div style="font-size:12px;color:#64748b;margin-bottom:4px;">纪律校验</div>
                            <div style="line-height:1.6;color:#334155;">{check}</div>
                        </div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    explanation = escape(diagnosis.get("behavioral_explanation") or "暂无行为金融解释。")
    st.markdown(
        f"""
        <div style="margin-top:16px;border-left:4px solid #3b82f6;background:#eff6ff;border-radius:6px;padding:16px 18px 16px 22px;">
            <div style="font-weight:700;color:#1e3a8a;margin-bottom:8px;">综合解读</div>
            <div style="line-height:1.9;color:#1f2937;text-indent:2em;">{explanation}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def show_diagnosis_report(diagnosis):
    """Display the educational behavior finance report."""
    if not diagnosis:
        st.info("本次操作未进行行为诊断。")
        return

    st.subheader("行为诊断报告")
    st.caption("这份报告用于帮助理解投资行为背后的心理机制，不提供交易信号。")

    if diagnosis.get("raw_text"):
        if diagnosis.get("format_warning"):
            st.warning(diagnosis.get("format_warning"))
        st.info(diagnosis.get("raw_text"))
        st.warning(diagnosis.get("final_disclaimer") or FINAL_DISCLAIMER)
        return

    score = get_score_value(diagnosis.get("rationality_score"))
    score_col, desc_col = st.columns([1, 2])
    with score_col:
        st.metric("理性评分", "暂无" if score is None else f"{score} / 100")
        if isinstance(score, (int, float)):
            st.progress(max(0, min(100, int(score))) / 100)
    with desc_col:
        st.info("该评分只代表本次投资行为的理性程度，不代表收益率，不代表操作正确性，也不代表买卖建议。")
        if diagnosis.get("score_explanation"):
            st.write(diagnosis.get("score_explanation"))
        if diagnosis.get("improvement_suggestion"):
            st.caption(f"改进方向：{diagnosis.get('improvement_suggestion')}")

    st.markdown("**一、行为金融解释：从一次操作看见一条心理链条**")
    show_behavior_chain(diagnosis)

    st.markdown("**二、情景推演**")
    st.caption("以下内容优先使用真实历史背景，再把本次决策模式放入当时环境做模拟推演；不是在编造某个真实用户案例。")
    stories = diagnosis.get("historical_stories") or []
    if not stories and diagnosis.get("historical_analogy"):
        stories = [{"title": "情景推演", "context": diagnosis.get("historical_analogy"), "lesson": "这段类比用于提醒用户把注意力放回计划、现金流和风险承受边界。"}]
    if stories:
        for story in stories:
            title = (story.get("title", "情景推演") or "情景推演").replace("金融史情景推演", "情景推演").replace("金融史故事类比", "情景推演")
            with st.expander(title, expanded=True):
                context = story.get("context") or story.get("story") or "暂无历史背景。"
                simulation = story.get("simulation") or "暂无情景推演。"
                data_logic = story.get("data_logic") or "暂无数据逻辑。"
                real_case_note = story.get("real_case_note") or "历史背景用于类比，用户情景为模拟推演。"
                lesson = story.get("lesson") or story.get("connection") or "请把这个类比作为行为提醒，而不是市场判断。"
                st.markdown(f"**历史背景：** {context}")
                st.markdown(f"**如果放到当时：** {simulation}")
                st.markdown(f"**数据与逻辑：** {data_logic}")
                st.caption(f"事实边界：{real_case_note}")
                st.info(lesson)
    else:
        st.info("暂无历史情景推演。")

    st.markdown("**三、心理偏差总结（五维）**")
    for dimension in diagnosis.get("bias_dimensions", []):
        name = dimension.get("name", "未命名维度")
        level = dimension.get("level", "未标注")
        with st.expander(f"{name}｜程度：{level}"):
            st.write(f"**通俗解释：** {dimension.get('meaning', '未提供')}")
            st.write(f"**本次操作中的体现：** {dimension.get('evidence', '未提供')}")
            st.write(f"**需要确认的问题：** {dimension.get('question_to_confirm', '请确认这次操作是否符合原计划。')}")

    st.markdown("**四、决策前检查清单**")
    checklist = diagnosis.get("checklist") or []
    if checklist:
        for question in checklist:
            st.write(f"- {question}")
    else:
        st.info("暂无检查清单。")

    st.warning(diagnosis.get("final_disclaimer") or FINAL_DISCLAIMER)

def sync_operation_history():
    """Sync history from JSON into session state."""
    records = load_transactions(st.session_state.user_id)
    st.session_state.operation_history = records
    return records


def reset_operation_flow():
    """Reset current operation flow while keeping selected fund and history."""
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
    """Build a stable key to prevent duplicate history writes during reruns."""
    return "|".join([
        str(operation.get("plan_id", "")),
        str(operation.get("operation_date", "")),
        str(operation.get("operation_type", "")),
        str(operation.get("amount", "")),
        str(operation.get("created_at", "")),
    ])


def generate_and_save_diagnosis(show_progress=True):
    """Generate diagnosis once, save it to history, and keep it in session state."""
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
        diagnosis = analyze_operation_reason(
            reason=operation.get("reason", ""),
            operation_type=operation.get("operation_type", ""),
            is_planned=answers.get("is_planned", ""),
            emotion_reason=answers.get("emotion_reason", ""),
            cash_flow=answers.get("cash_flow", ""),
            can_accept_loss=answers.get("can_accept_loss", ""),
        )

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
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    add_transaction(record, st.session_state.user_id)
    st.session_state.diagnosis_report = diagnosis
    st.session_state.diagnosis_record_key = operation_key
    sync_operation_history()
    return diagnosis


def initialize_state():
    """Initialize required session state keys."""
    st.session_state.setdefault("selected_fund_plan", {})
    st.session_state.setdefault("current_operation", {})
    st.session_state.setdefault("answers", {})
    st.session_state.setdefault("behavior_answers", dict(st.session_state.answers))
    st.session_state.setdefault("diagnosis_report", {})
    st.session_state.setdefault("diagnosis_in_progress", False)
    st.session_state.setdefault("operation_history", load_transactions(st.session_state.user_id))
    st.session_state.setdefault("current_step", 0)
    st.session_state.setdefault("answered_count", count_answered_questions(st.session_state.answers, QUESTION_FLOW))
    st.session_state.setdefault("qa_step", st.session_state.current_step)
    st.session_state.setdefault("step_index", 0)
    st.session_state.setdefault("diagnosis_record_key", "")


user_id = get_or_create_user_id()
ensure_data_files(user_id)
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
    ]:
        st.session_state.pop(key, None)
initialize_state()
if not st.session_state.answers and st.session_state.behavior_answers:
    st.session_state.answers = dict(st.session_state.behavior_answers)
sync_question_state()
plans = load_plans(st.session_state.user_id)
selected_plan = get_selected_plan(plans)
operation_history = sync_operation_history()

st.title("基金定投辅助决策 Agent")
st.write("本系统是 Educational Behavioral Finance Agent，通过行为金融与金融史视角，帮助用户在定投相关操作前理解心理机制、检查纪律边界，并形成长期投资人格画像。")
st.warning("本工具不预测市场涨跌，不评价基金好坏，不推荐基金，也不提供买入、卖出、加仓、减仓建议。")
st.info(
    f"当前临时用户ID：{st.session_state.user_id}。当前版本为临时演示版，系统会为每位访问者生成一个临时 user_id，并将数据暂存在该用户目录下。保存当前链接，可以在短期内继续查看自己的演示数据。正式产品化后将升级为账号登录和云数据库存储。"
)

plan_tab, operation_tab, history_tab, personality_tab = st.tabs(["定投计划", "操作检查", "历史记录", "投资人格分析"])


with plan_tab:
    st.header("定投计划")
    list_col, form_col = st.columns([1, 2])

    with list_col:
        with st.container(border=True):
            st.subheader("已保存计划")
            if not plans:
                st.info("还没有保存定投计划。")
            else:
                for index, plan in enumerate(plans, start=1):
                    label = plan.get("fund_name") or f"未命名基金 {index}"
                    if selected_plan.get("id") == plan.get("id"):
                        label = "当前：" + label
                    if st.button(label, key=f"select_plan_{plan.get('id')}", use_container_width=True):
                        st.session_state.selected_fund_plan = plan
                        selected_plan = plan
                        reset_operation_flow()
                        st.rerun()
                    st.caption(f"每月 {format_money(plan.get('monthly_amount', 0))} | {plan.get('monthly_day', 1)} 日 | 回撤 {plan.get('max_drawdown') or '未填写'}")
                    st.divider()

            if st.button("新建基金计划", use_container_width=True):
                st.session_state.selected_fund_plan = {}
                selected_plan = {}
                st.rerun()

    with form_col:
        editing_plan = selected_plan or {}
        with st.container(border=True):
            st.subheader("新增或更新计划")
            with st.form("plan_form"):
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    fund_name = st.text_input("基金名称", value=editing_plan.get("fund_name", ""))
                with col2:
                    monthly_amount = st.number_input("每月定投金额", min_value=0.0, step=100.0, value=float(editing_plan.get("monthly_amount", 0) or 0))
                with col3:
                    monthly_day = st.number_input("定投日期", min_value=1, max_value=28, step=1, value=int(editing_plan.get("monthly_day", 1) or 1))
                investment_goal = st.text_area("投资目标", value=editing_plan.get("investment_goal", ""))
                max_drawdown = st.text_input("最大可接受回撤", value=editing_plan.get("max_drawdown", ""), placeholder="例如：20%")
                save_plan_button = st.form_submit_button("保存定投计划", use_container_width=True)

            if save_plan_button:
                plan = {
                    "id": editing_plan.get("id"),
                    "fund_name": fund_name,
                    "monthly_amount": monthly_amount,
                    "monthly_day": monthly_day,
                    "investment_goal": investment_goal,
                    "max_drawdown": max_drawdown,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                saved_plan = save_plan(plan, st.session_state.user_id)
                st.session_state.selected_fund_plan = saved_plan
                st.success("定投计划已保存。")
                st.rerun()


with operation_tab:
    st.header("操作检查")
    if not selected_plan:
        st.info("请先在“定投计划”中选择或新增一个基金计划。")
    else:
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
                st.caption("填写本次操作的基础信息，保存后系统会自动进入行为问答。")
                with st.form("operation_input_form"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        operation_date = st.date_input("操作日期", value=date.today())
                    with col2:
                        operation_type = st.selectbox("操作类型", ["定投", "加仓", "减仓", "其他"], index=0)
                    with col3:
                        amount = st.number_input("操作金额", min_value=0.0, step=100.0)
                    operation_reason = st.text_area(
                        "操作理由（可选）",
                        placeholder="例如：最近波动较大，我想调整本月定投节奏。",
                        height=90,
                    )
                    save_button = st.form_submit_button("保存操作输入", use_container_width=True)

                if save_button:
                    st.session_state.current_operation = {
                        "plan_id": selected_plan.get("id"),
                        "fund_name": selected_plan.get("fund_name"),
                        "operation_date": operation_date.strftime("%Y-%m-%d"),
                        "operation_type": operation_type,
                        "amount": amount,
                        "reason": operation_reason.strip(),
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
            operation = st.session_state.current_operation
            if not operation:
                st.info("请先完成 Step 1 操作输入。")
                st.session_state.step_index = 0
            else:
                st.subheader("Step 2：行为问答")
                st.caption("请一次性完成以下保存前检查，提交后系统会生成行为诊断报告。")

                total_questions = len(QUESTION_FLOW)
                answers = st.session_state.answers

                with st.form("behavior_qa_form"):
                    draft_answers = {}
                    for index, question in enumerate(QUESTION_FLOW, start=1):
                        with st.container(border=True):
                            st.markdown(f"**Q{index}：{question['title']}**")
                            previous_answer = answers.get(question["key"])
                            default_index = question["options"].index(previous_answer) if previous_answer in question["options"] else None
                            draft_answers[question["key"]] = st.radio(
                                "请选择",
                                question["options"],
                                index=default_index,
                                horizontal=True,
                                label_visibility="collapsed",
                                key=f"qa_radio_{question['key']}",
                            )
                    submit_answers = st.form_submit_button("提交行为问答并生成诊断", use_container_width=True)

                if submit_answers:
                    missing_questions = [
                        question["title"]
                        for question in QUESTION_FLOW
                        if draft_answers.get(question["key"]) is None
                    ]
                    if missing_questions:
                        st.warning("请先完成所有行为问答后再提交。")
                    else:
                        st.session_state.answers = dict(draft_answers)
                        st.session_state.current_step = total_questions
                        sync_question_state()
                        st.session_state.diagnosis_report = {}
                        st.session_state.diagnosis_in_progress = True
                        st.session_state.step_index = 2
                        st.rerun()

        else:
            operation = st.session_state.current_operation
            answers = st.session_state.answers
            answered_count = count_answered_questions(answers, QUESTION_FLOW)

            if not operation:
                st.info("请先完成 Step 1 操作输入。")
                st.session_state.step_index = 0
            elif answered_count < len(QUESTION_FLOW):
                st.info("请先完成 Step 2 行为问答。")
                st.session_state.step_index = 1
            else:
                sync_question_state()
                if not st.session_state.diagnosis_report:
                    st.session_state.diagnosis_in_progress = True
                    generate_and_save_diagnosis(show_progress=True)
                    st.session_state.diagnosis_in_progress = False
                    st.rerun()
                st.session_state.diagnosis_in_progress = False
                st.success("诊断已生成，并已写入历史记录。")
                show_diagnosis_report(st.session_state.diagnosis_report)
with history_tab:
    st.header("历史记录")
    records = filter_history_for_plan(sync_operation_history(), selected_plan)
    if not selected_plan:
        st.info("请先选择基金计划。")
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
                with right:
                    show_diagnosis_report(record.get("behavior_diagnosis"))


with personality_tab:
    st.header("投资人格分析（Investment Personality）")
    st.caption("这是投资行为的长期心理画像系统，不预测市场，不提供投资建议，只做行为分析、心理建模和历史归因。")

    all_records = sync_operation_history()
    if not all_records:
        st.info("还没有历史操作记录。完成几次操作检查后，这里会生成长期投资人格画像。")
    else:
        profile = build_investment_personality(all_records)
        scores = profile.get("scores", {})
        stats = profile.get("stats", {})

        st.subheader("投资人格评分")
        score_cols = st.columns(5)
        score_items = [
            ("Emotional Sensitivity", "情绪敏感度"),
            ("Discipline", "纪律性"),
            ("FOMO Tendency", "FOMO倾向"),
            ("Risk Tolerance", "风险承受能力"),
            ("Long-term Orientation", "长期主义"),
        ]
        for col, (key, label) in zip(score_cols, score_items):
            value = scores.get(key, 0)
            col.metric(label, f"{value}/100")
            col.progress(value / 100)

        st.subheader("人格类型")
        type_col, risk_col = st.columns([2, 1])
        with type_col:
            st.info(f"**{profile.get('personality_type')}**\n\n{profile.get('personality_summary')}")
        with risk_col:
            st.warning(f"风险人格等级：\n\n**{profile.get('risk_level')}**")

        st.subheader("行为统计")
        stat_cols = st.columns(5)
        stat_cols[0].metric("历史操作", stats.get("total", 0))
        stat_cols[1].metric("偏离计划", stats.get("out_plan_count", 0))
        stat_cols[2].metric("加仓/减仓/调整", stats.get("adjust_count", 0))
        stat_cols[3].metric("情绪触发", stats.get("emotion_count", 0))
        stat_cols[4].metric("FOMO/羊群信号", stats.get("fomo_count", 0))

        st.subheader("行为证据摘要")
        with st.container(border=True):
            for item in profile.get("evidence", []):
                st.write(f"- {item}")

        st.subheader("行为模式总结")
        st.info(profile.get("pattern_summary", "暂无行为模式总结。"))

        st.subheader("改善方向")
        with st.container(border=True):
            for item in profile.get("suggestions", []):
                st.write(f"- {item}")
        st.caption("以上内容仅用于行为优化和心理建模，不构成任何买入、卖出、加仓或减仓建议。")






