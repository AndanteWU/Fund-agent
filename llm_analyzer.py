"""DeepSeek-based behavior diagnosis for fund DCA operations."""

import json
import os
import random

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


FINAL_DISCLAIMER = "本分析不构成投资建议，不预测市场涨跌，也不提供买入、卖出、加仓、减仓建议。"
BIAS_DIMENSIONS = ["过度自信", "判断偏差", "羊群效应", "损失厌恶", "自豪与悔恨"]


FALLBACK_HISTORY_POOL = [
    {
        "title": "1929年美国股灾：杠杆乐观下的纪律失效",
        "context": "1920年代后期，美股经历多年上涨，保证金交易盛行，许多投资者用较少本金撬动更大头寸。1929年崩盘后，道琼斯指数从高点到1932年低点跌幅接近九成。",
        "simulation": "如果把用户的这类操作放到当时场景中，最需要观察的不是是否继续持有，而是计划是否能承受极端回撤。如果操作理由只是‘之前一直涨’或‘别人都在参与’，一旦价格连续反向波动，心理压力会迅速放大。",
        "data_logic": "当组合回撤达到30%时，需要上涨约43%才能回本；如果回撤达到50%，则需要上涨100%才能回本。损失越大，靠情绪补救越困难，靠预案管理越重要。",
        "real_case_note": "1929年股灾是真实历史事件；这里的用户情景是模拟推演，不代表用户会遭遇同类市场。",
        "lesson": "检查操作金额、现金流和继续下跌承受力，比判断短期方向更关键。",
    },
    {
        "title": "日本资产泡沫：长期乐观如何变成锚定",
        "context": "20世纪80年代后期，日本股票和房地产价格大幅上涨。日经225指数在1989年底接近38900点，此后进入长期调整，许多投资者多年后才意识到自己把高估值环境当成了常态。",
        "simulation": "如果用户在类似持续上涨环境中执行定投，风险不在于定投动作本身，而在于把过去的高增长当作未来基准。一旦心理锚点固定在高点，就容易在回撤中产生‘必须尽快修复账户’的冲动。",
        "data_logic": "锚定效应会让投资者过度参考买入价、高点或近期收益，而忽略原计划中的周期、预算和再平衡规则。",
        "real_case_note": "日本资产泡沫是真实历史背景；这里用于说明锚定和长期乐观偏差，不评价任何具体基金。",
        "lesson": "定投计划应写清触发条件和资金边界，避免让历史高点成为唯一参照。",
    },
    {
        "title": "2008年金融危机：亏损压力下的补救冲动",
        "context": "2008年金融危机期间，全球风险资产大幅波动，许多投资者同时面对账户亏损、新闻冲击和流动性压力。",
        "simulation": "如果用户在这种环境中因为下跌、亏损或焦虑而调整定投节奏，关键问题是这笔操作是否来自预留预算。如果资金来自临时挤压现金流，继续下跌会让财务压力和心理压力叠加。",
        "data_logic": "同样是增加投入，计划内再平衡和情绪化补救的差别在于：前者有金额上限、触发条件和现金流预案，后者通常只有‘想把亏损补回来’。",
        "real_case_note": "2008年金融危机是真实历史事件；这里的用户行为后果是基于行为金融逻辑的模拟。",
        "lesson": "先确认预算来源和最坏情形承受力，再评价操作是否符合纪律。",
    },
    {
        "title": "GameStop事件：群体情绪如何改变个人判断",
        "context": "2021年GameStop事件中，社群讨论、做空叙事和快速上涨共同放大了参与热情，价格短期剧烈波动。",
        "simulation": "如果用户的定投操作受到朋友、社交媒体或市场热度影响，可能会把‘很多人在行动’误读为‘这个决定更可靠’。这种情况下，真正需要检查的是自己的计划是否被外部叙事临时改写。",
        "data_logic": "群体热度可以提高信息可得性，却不等于提高决策质量。看到的信息越密集，越要区分事实、观点和情绪传染。",
        "real_case_note": "GameStop事件是真实市场事件；这里用于类比群体情绪，不代表当前基金存在类似结构。",
        "lesson": "把操作理由写下来，可以帮助区分自己的规则和外部噪音。",
    },
    {
        "title": "加密资产热潮：FOMO与波动承受力测试",
        "context": "多轮加密资产热潮中，价格快速上涨和社交媒体传播让许多新参与者产生强烈错过感，同时也伴随高波动和深度回撤。",
        "simulation": "如果用户因为上涨、怕错过或别人赚钱而改变定投节奏，短期可能感觉缓解焦虑，但一旦价格反向波动，就会暴露真实风险承受力。",
        "data_logic": "高波动环境下，仓位大小比方向判断更影响心理稳定性。金额超出预算时，轻微波动也可能变成行为压力。",
        "real_case_note": "加密资产热潮是真实市场现象；这里仅作为FOMO心理类比，不涉及任何产品判断。",
        "lesson": "把‘怕错过’翻译成可执行规则，否则它会不断推动临时决策。",
    },
]

def build_fallback_diagnosis(message="AI 返回结构异常，暂时无法生成完整诊断。"):
    """Return a safe diagnosis shape without exposing raw model output."""
    return {
        "structure_error": True,
        "error_message": "AI 返回结构异常，已停止展示原始内容。请稍后重新生成诊断报告。",
        "score": None,
        "explanation": "本次 AI 返回内容没有通过严格 JSON 校验，因此未生成结构化报告。",
        "suggestions": ["重新生成报告前，可以先检查操作理由是否填写清楚。"],
        "risks": ["报告结构异常，不能作为行为诊断依据。"],
        "rationality_score": None,
        "score_explanation": "",
        "improvement_suggestion": "",
        "bias_dimensions": [],
        "behavioral_explanation": "",
        "decision_chain": [],
        "historical_stories": [],
        "historical_analogy": "",
        "checklist": [],
        "final_disclaimer": FINAL_DISCLAIMER,
    }


def extract_json(text):
    """Strictly parse model output as JSON. Mixed text is rejected."""
    return json.loads((text or "").strip())


def normalize_bias_dimensions(items):
    """Ensure the five fixed bias dimensions are always present."""
    items = items or []
    by_name = {item.get("name"): item for item in items if isinstance(item, dict)}
    normalized = []

    for name in BIAS_DIMENSIONS:
        item = by_name.get(name, {})
        normalized.append(
            {
                "name": name,
                "level": item.get("level", "低"),
                "meaning": item.get("meaning", "该维度用于观察本次操作中是否存在对应的心理倾向。"),
                "evidence": item.get("evidence", "本次信息中该维度信号不明显，因此暂标为低。"),
                "question_to_confirm": item.get("question_to_confirm", "这次操作是否仍然符合你的原定计划、现金流和风险承受边界？"),
            }
        )

    return normalized


def normalize_decision_chain(items):
    """Normalize the AI decision-chain structure for display."""
    if isinstance(items, list) and items:
        chain = []
        for item in items[:6]:
            if isinstance(item, dict):
                chain.append(
                    {
                        "stage": item.get("stage", "决策阶段"),
                        "signal": item.get("signal", "本次信息不足"),
                        "psychology": item.get("psychology", "需要进一步确认心理机制"),
                        "discipline_check": item.get("discipline_check", "回到计划、现金流和风险承受边界"),
                    }
                )
        if chain:
            return chain

    return [
        {"stage": "触发信号", "signal": "操作想法出现", "psychology": "短期价格、账户盈亏或外部信息进入注意力", "discipline_check": "先确认这是否属于原定计划"},
        {"stage": "注意力聚焦", "signal": "近期波动被放大", "psychology": "可得性偏差让最近看到的信息显得更重要", "discipline_check": "区分事实、情绪和他人观点"},
        {"stage": "参照点形成", "signal": "以成本、高点或计划金额作比较", "psychology": "锚定效应可能影响对风险的判断", "discipline_check": "检查当前金额是否仍在预算内"},
        {"stage": "情绪反应", "signal": "后悔、焦虑或怕错过", "psychology": "损失厌恶和自豪/悔恨会推动补救冲动", "discipline_check": "确认继续下跌10%时是否能接受"},
        {"stage": "操作冲动", "signal": "想调整金额或节奏", "psychology": "过度自信可能把短期判断当成确定性", "discipline_check": "把操作理由写成可复盘规则"},
    ]


def normalize_history_stories(items, legacy_text=""):
    """Normalize financial history scenarios while supporting older records."""
    if isinstance(items, list) and items:
        scenarios = []
        for item in items[:3]:
            if isinstance(item, dict):
                scenarios.append(
                    {
                        "title": item.get("title", "情景推演"),
                        "context": item.get("context", item.get("story", "")),
                        "simulation": item.get("simulation", ""),
                        "data_logic": item.get("data_logic", ""),
                        "real_case_note": item.get("real_case_note", ""),
                        "lesson": item.get("lesson", item.get("connection", "")),
                        "story": item.get("story", ""),
                        "connection": item.get("connection", ""),
                    }
                )
        if scenarios:
            return scenarios

    if legacy_text:
        return [{"title": "历史类比", "context": legacy_text, "simulation": "", "data_logic": "", "real_case_note": "", "lesson": "这段类比用于提醒用户把注意力放回计划、现金流和风险承受边界。"}]

    return random.sample(FALLBACK_HISTORY_POOL, k=min(3, len(FALLBACK_HISTORY_POOL)))

def normalize_list(value):
    """Normalize optional list-like fields returned by the model."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_behavior_diagnosis(data):
    """Normalize strict AI JSON into fields used by the Streamlit page."""
    score = data.get("score", data.get("rationality_score"))
    explanation = data.get("explanation") or data.get("behavioral_explanation", "")
    suggestions = normalize_list(data.get("suggestions"))
    if data.get("improvement_suggestion"):
        suggestions.append(str(data.get("improvement_suggestion")).strip())
    risks = normalize_list(data.get("risks"))

    checklist = normalize_list(data.get("checklist"))
    return {
        "score": score,
        "explanation": explanation,
        "suggestions": suggestions,
        "risks": risks,
        "rationality_score": score,
        "score_explanation": data.get("score_explanation", ""),
        "improvement_suggestion": data.get("improvement_suggestion", ""),
        "bias_dimensions": normalize_bias_dimensions(data.get("bias_dimensions", [])),
        "behavioral_explanation": explanation,
        "decision_chain": normalize_decision_chain(data.get("decision_chain", [])),
        "historical_stories": normalize_history_stories(data.get("historical_stories", []), data.get("historical_analogy", "")),
        "historical_analogy": data.get("historical_analogy", ""),
        "checklist": checklist,
        "final_disclaimer": data.get("final_disclaimer") or FINAL_DISCLAIMER,
    }


def analyze_operation_reason(
    reason: str,
    operation_type: str,
    is_planned: str,
    emotion_reason: str,
    cash_flow: str,
    can_accept_loss: str,
):
    """Call DeepSeek API to diagnose one fund DCA operation behavior."""

    if OpenAI is None:
        return build_fallback_diagnosis(
            "未检测到 openai Python 包。请先安装依赖：py -m pip install openai"
        )

    api_key = os.getenv("DEEPSEEK_API_KEY")

    if not api_key:
        return build_fallback_diagnosis(
            "未检测到 DEEPSEEK_API_KEY。\n"
            "请先在 PyCharm Terminal 中设置环境变量：\n"
            '$env:DEEPSEEK_API_KEY="你的DeepSeek API Key"'
        )

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    system_prompt = f"""
你是“基金定投辅助决策 Agent”中的 Educational Behavioral Finance Agent，服务投资新手和非专业投资者。

你的任务不是分析基金好坏，不是预测市场，也不是提供买卖操作建议，而是把用户的一次定投相关操作，解释成一份有教育意义的行为金融报告。报告要像在讲一个投资心理故事，而不是输出一组冷冰冰的技术标签。

必须遵守：
1. 不预测市场涨跌；
2. 不评价任何具体基金的好坏；
3. 不推荐任何基金产品；
4. 不提供买入、卖出、加仓、减仓建议；
5. 不说“应该买”“应该卖”“建议买入”“建议卖出”“建议加仓”“建议减仓”；
6. 不输出与群体策略模型相关的技术化分析；
7. 不把行为偏差等同于用户一定错误，只说明需要进一步确认；
8. 面向投资新手解释，语言通俗、克制、有故事感和教育意义。

请严格返回一个合法 JSON object。不要输出 Markdown，不要输出代码块，不要在 JSON 前后添加任何自然语言。
JSON 字段必须包括：
- score: 0 到 100 的整数，只代表本次投资行为理性程度，不代表收益率、不代表操作正确性、不代表买卖建议。
- explanation: 字符串，用结构化摘要解释本次行为诊断结论。
- suggestions: 数组，包含 3 到 5 条行为层面的改进方向，只能围绕记录理由、检查计划、现金流和风险承受。
- risks: 数组，包含 1 到 3 条风险提醒，只能围绕情绪影响、计划偏离、现金流压力或风险承受边界。
- rationality_score: 0 到 100 的整数，只代表本次投资行为理性程度，不代表收益率、不代表操作正确性、不代表买卖建议。
- score_explanation: 1 到 2 句话解释评分原因。
- improvement_suggestion: 1 到 2 句话，给出行为改进建议，只能围绕记录理由、检查计划、现金流和风险承受，不给任何具体买卖操作建议。
- bias_dimensions: 数组，必须且只能包含以下五个固定维度，并且五个都要出现：过度自信、判断偏差、羊群效应、损失厌恶、自豪与悔恨。每个对象包括 name、level、meaning、evidence、question_to_confirm。每一项都必须结合本次操作解释，不允许只给定义。
- decision_chain: 数组，包含 4 到 5 个投资决策心理阶段。每个对象包括 stage、signal、psychology、discipline_check。请按真实决策过程组织，例如：触发信号、注意力聚焦、参照点形成、情绪反应、操作冲动。每个阶段必须结合本次用户输入。
- behavioral_explanation: 一整段叙事，用来解释上面的 decision_chain。不要写成理论清单。必须自然解释前景理论（损失厌恶）、锚定效应、过度自信、可得性偏差、羊群效应，并说明它们如何在本次操作中依次影响判断。
- historical_stories: 数组，包含 2 到 3 个“情景推演”。请根据用户本次行为特征自由选择最贴切的真实历史背景，不要每次固定使用同一组案例。可选方向包括但不限于：南海泡沫、密西西比泡沫、1929年美国股灾、漂亮50、拉美债务危机、日本资产泡沫、亚洲金融危机、长期资本管理公司、2000年互联网泡沫、2008年金融危机、欧债危机、2015年A股、GameStop事件、加密资产热潮、meme stock、商品周期泡沫等。每个对象包括 title、context、simulation、data_logic、real_case_note、lesson。context 只写真实历史背景和可公开验证的大致数据，不要编造具体人物；simulation 明确写“如果把用户这次决策放到当时环境中，可能出现什么后果”；data_logic 用数字、比例、回撤/回本逻辑、现金流压力或风险暴露解释；real_case_note 说明哪些是历史事实、哪些是模拟推演；lesson 回到行为纪律，不给买卖建议。
- checklist: 数组，包含 3 到 5 个决策前检查问题，围绕原定计划、现金流、继续下跌承受力、明确规则、市场情绪/短期涨跌影响、理由清晰度。
- final_disclaimer: 字符串，必须等于“{FINAL_DISCLAIMER}”。
"""

    user_prompt = f"""
请根据下面这次基金定投相关操作，生成一份“行为诊断报告”，并严格输出一个合法 JSON object。不要在 JSON 前后添加任何解释文字。

操作类型：{operation_type}
是否符合定投计划：{is_planned}
短期涨跌/情绪影响：{emotion_reason}
是否影响现金流：{cash_flow}
如果继续下跌 10% 是否能接受：{can_accept_loss}
用户填写的操作理由：{reason or '未填写'}

请把重点放在：投资决策心理链条、行为金融理论解释、情景推演、五维心理偏差总结和理性评分。
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.55,
        )

        content = response.choices[0].message.content
        try:
            data = extract_json(content)
            return normalize_behavior_diagnosis(data)
        except json.JSONDecodeError:
            return build_fallback_diagnosis()

    except Exception as error:
        return build_fallback_diagnosis(f"AI 分析失败：{error}")










