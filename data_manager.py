"""Simple JSON storage helpers for the Fund Investor Emotion Management Agent."""

import json
import os
from datetime import datetime

from supabase_client import get_supabase_client
from pathlib import Path
from uuid import uuid4


DATA_DIR = Path("data")
PLAN_FILE = DATA_DIR / "plan.json"
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
REVIEW_REPORTS_FILE = DATA_DIR / "review_reports.json"
DEFAULT_TRANSACTIONS = []
DEFAULT_REVIEW_REPORTS = []


def default_plan_data():
    """Return an empty multi-plan structure for a newly authenticated user."""
    return {"plans": [], "selected_plan_id": None}



def get_user_dir(user_id=None):
    """Return the storage folder for one Supabase Auth user.

    The user_id must come from Supabase auth.users.id.
    """
    if not user_id:
        return DATA_DIR
    safe_user_id = "".join(char for char in str(user_id) if char.isalnum() or char in {"_", "-"})
    return DATA_DIR / safe_user_id


def get_user_files(user_id=None):
    """Return JSON file paths for one user."""
    base_dir = get_user_dir(user_id)
    return {
        "plan": base_dir / "plan.json",
        "transactions": base_dir / "transactions.json",
        "review_reports": base_dir / "review_reports.json",
        "current_state": base_dir / "state.json",
        "emotion_records": base_dir / "emotion_records.json",
    }


def read_json(file_path, default_value):
    """Read JSON safely. If the file is missing or broken, return a default value."""
    try:
        with file_path.open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_value


def write_json(file_path, data):
    """Write data to a JSON file with readable Chinese formatting."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def ensure_data_files(user_id=None):
    """Create long-term JSON files for one authenticated user."""
    DATA_DIR.mkdir(exist_ok=True)
    files = get_user_files(user_id)

    if not files["plan"].exists():
        write_json(files["plan"], default_plan_data())
    if not files["transactions"].exists():
        write_json(files["transactions"], DEFAULT_TRANSACTIONS)
    if not files["review_reports"].exists():
        write_json(files["review_reports"], DEFAULT_REVIEW_REPORTS)
    if not files["emotion_records"].exists():
        write_json(files["emotion_records"], [])


def make_plan_id():
    """Create a short readable plan id."""
    return f"plan_{uuid4().hex[:6]}"


def normalize_plan(plan):
    """Normalize one plan and keep old field aliases readable."""
    source = dict(plan or {})
    plan_id = source.get("plan_id") or source.get("id") or make_plan_id()
    dca_day = source.get("dca_day", source.get("monthly_day", 1))
    goal = source.get("goal", source.get("investment_goal", ""))

    try:
        dca_day = int(dca_day or 1)
    except (TypeError, ValueError):
        dca_day = 1
    dca_day = max(1, min(28, dca_day))

    normalized = {
        "plan_id": plan_id,
        "id": plan_id,
        "fund_name": source.get("fund_name", ""),
        "monthly_amount": float(source.get("monthly_amount", 0) or 0),
        "dca_day": dca_day,
        "monthly_day": dca_day,
        "goal": goal,
        "investment_goal": goal,
        "max_drawdown": source.get("max_drawdown", ""),
        "fund_code": source.get("fund_code", ""),
        "dca_frequency": source.get("dca_frequency", "每月"),
        "start_date": source.get("start_date", ""),
        "end_date": source.get("end_date", ""),
        "notes": source.get("notes", ""),
    }

    if source.get("created_at"):
        normalized["created_at"] = source.get("created_at")
    if source.get("updated_at"):
        normalized["updated_at"] = source.get("updated_at")
    return normalized


def normalize_plan_data(data):
    """Normalize old single/list plan formats into the new multi-plan structure."""
    if isinstance(data, dict) and isinstance(data.get("plans"), list):
        raw_plans = data.get("plans", [])
        selected_plan_id = data.get("selected_plan_id")
    elif isinstance(data, list):
        raw_plans = data
        selected_plan_id = None
    elif isinstance(data, dict) and data.get("fund_name"):
        raw_plans = [data]
        selected_plan_id = data.get("plan_id") or data.get("id")
    else:
        raw_plans = []
        selected_plan_id = None

    plans = [normalize_plan(item) for item in raw_plans if isinstance(item, dict)]
    valid_ids = {plan.get("plan_id") for plan in plans}
    if selected_plan_id not in valid_ids:
        selected_plan_id = plans[0].get("plan_id") if plans else None

    return {"plans": plans, "selected_plan_id": selected_plan_id}


def load_plan_data(user_id=None):
    """Load the full multi-plan JSON data for one authenticated user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    data = normalize_plan_data(read_json(files["plan"], default_plan_data()))
    write_json(files["plan"], data)
    return data


def save_plan_data(user_id, data):
    """Save the full multi-plan JSON data for one authenticated user."""
    normalized = normalize_plan_data(data)
    files = get_user_files(user_id)
    write_json(files["plan"], normalized)
    return normalized


def load_plans(user_id=None):
    """Load all saved investment plans for one authenticated user."""
    return load_plan_data(user_id).get("plans", [])


def list_fund_plans(user_id=None):
    """List all fund plans for one authenticated user."""
    return load_plans(user_id)


def save_plans(user_id, data):
    """Save plan data. Accepts either the full dict or a plain plans list."""
    if isinstance(data, list):
        current = load_plan_data(user_id)
        data = {"plans": data, "selected_plan_id": current.get("selected_plan_id")}
    return save_plan_data(user_id, data)


def add_plan(user_id, plan):
    """Append a new fund plan and select it."""
    data = load_plan_data(user_id)
    new_plan = normalize_plan({**dict(plan or {}), "plan_id": make_plan_id()})
    data["plans"].append(new_plan)
    data["selected_plan_id"] = new_plan.get("plan_id")
    save_plan_data(user_id, data)
    return new_plan


def update_plan(user_id, plan_id, updated_plan):
    """Update only the selected plan, leaving other plans untouched."""
    data = load_plan_data(user_id)
    for index, existing_plan in enumerate(data.get("plans", [])):
        if existing_plan.get("plan_id") == plan_id:
            merged = {**existing_plan, **dict(updated_plan or {}), "plan_id": plan_id, "id": plan_id}
            data["plans"][index] = normalize_plan(merged)
            data["selected_plan_id"] = plan_id
            save_plan_data(user_id, data)
            return data["plans"][index]
    return {}


def set_selected_plan(user_id, plan_id):
    """Select one existing plan by plan_id."""
    data = load_plan_data(user_id)
    if any(plan.get("plan_id") == plan_id for plan in data.get("plans", [])):
        data["selected_plan_id"] = plan_id
        save_plan_data(user_id, data)
    return get_selected_plan(user_id)


def get_selected_plan(user_id=None):
    """Return the currently selected plan for one authenticated user."""
    data = load_plan_data(user_id)
    selected_plan_id = data.get("selected_plan_id")
    for plan in data.get("plans", []):
        if plan.get("plan_id") == selected_plan_id:
            return plan
    return {}


def get_active_plan(user_id=None):
    """Return the active fund plan for one authenticated user."""
    return get_selected_plan(user_id)


def create_fund_plan(user_id, plan_data):
    """Create one fund plan and make it active."""
    return add_plan(user_id, plan_data)


def update_fund_plan(user_id, plan_id, plan_data):
    """Update one fund plan by plan_id."""
    return update_plan(user_id, plan_id, plan_data)


def create_sample_plans(user_id=None):
    """Load demo plans only when the user explicitly asks for examples."""
    data = load_plan_data(user_id)
    if data.get("plans"):
        return data

    sample_plans = [
        normalize_plan({
            "plan_id": make_plan_id(),
            "fund_name": "易方达全球成长精选混合 QDII A",
            "monthly_amount": 500,
            "dca_day": 15,
            "goal": "长期持有三年以上，作为海外资产配置的一部分",
            "max_drawdown": "20%",
        }),
        normalize_plan({
            "plan_id": make_plan_id(),
            "fund_name": "易方达全球成长精选混合 QDII C",
            "monthly_amount": 800,
            "dca_day": 20,
            "goal": "长期持有三年以上，作为海外资产配置的一部分",
            "max_drawdown": "20%",
        }),
    ]
    data = {"plans": sample_plans, "selected_plan_id": sample_plans[0].get("plan_id")}
    save_plan_data(user_id, data)
    return data


def load_plan(user_id=None):
    """Load the selected saved plan for backward compatibility."""
    return get_selected_plan(user_id)


def get_plan_by_id(user_id=None, plan_id=None):
    """Find one plan by id. Accepts both (user_id, plan_id) and legacy (plan_id, user_id)."""
    if plan_id is None:
        user_id, plan_id = None, user_id
    for plan in load_plans(user_id):
        if plan.get("plan_id") == plan_id or plan.get("id") == plan_id:
            return plan
    return {}


def save_plan(plan, user_id=None):
    """Create or update one investment plan for backward compatibility."""
    plan_id = (plan or {}).get("plan_id") or (plan or {}).get("id")
    if plan_id and get_plan_by_id(user_id, plan_id):
        return update_plan(user_id, plan_id, plan)
    return add_plan(user_id, plan)


def load_transactions(user_id=None):
    """Load all saved transaction records for one authenticated user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    transactions = read_json(files["transactions"], [])
    return transactions if isinstance(transactions, list) else []


def load_transactions_for_plan(user_id=None, plan_id=None):
    """Load operation records that belong to one active plan."""
    if not plan_id:
        return []
    return [
        record for record in load_transactions(user_id)
        if record.get("plan_id") == plan_id
    ]


def list_operation_checks(user_id=None, plan_id=None):
    """List operation checks for all plans or one specific plan."""
    if plan_id:
        return load_transactions_for_plan(user_id, plan_id)
    return load_transactions(user_id)


def save_transactions(transactions, user_id=None):
    """Save the whole transaction list for one authenticated user."""
    files = get_user_files(user_id)
    write_json(files["transactions"], transactions)


def add_transaction(transaction, user_id=None):
    """Append one transaction record and save it for one authenticated user."""
    transactions = load_transactions(user_id)
    transactions.append(transaction)
    save_transactions(transactions, user_id)
    return transactions


def load_review_reports(user_id=None):
    """Load stored review reports for one authenticated user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    reports = read_json(files["review_reports"], [])
    return reports if isinstance(reports, list) else []


def save_review_reports(reports, user_id=None):
    """Save review reports for one authenticated user."""
    files = get_user_files(user_id)
    write_json(files["review_reports"], reports or [])


def list_review_reports(user_id=None, plan_id=None):
    """List review reports for all plans or one specific plan."""
    reports = load_review_reports(user_id)
    if not plan_id:
        return reports
    return [report for report in reports if report.get("plan_id") == plan_id]


def delete_fund_plan_with_records(user_id, plan_id):
    """Delete one plan and its related operation/review records for the current user."""
    data = load_plan_data(user_id)
    plans = [plan for plan in data.get("plans", []) if plan.get("plan_id") != plan_id and plan.get("id") != plan_id]
    selected_plan_id = plans[0].get("plan_id") if plans else None
    save_plan_data(user_id, {"plans": plans, "selected_plan_id": selected_plan_id})

    transactions = [
        record for record in load_transactions(user_id)
        if record.get("plan_id") != plan_id
    ]
    save_transactions(transactions, user_id)

    reports = [
        report for report in load_review_reports(user_id)
        if report.get("plan_id") != plan_id
    ]
    save_review_reports(reports, user_id)

    return {"plans": plans, "selected_plan_id": selected_plan_id}


def load_current_state(user_id=None):
    """Load temporary latest state for backward compatibility only."""
    files = get_user_files(user_id)
    state = read_json(files["current_state"], {})
    return state if isinstance(state, dict) else {}


def save_current_state(state, user_id=None):
    """Save temporary latest state for backward compatibility only."""
    files = get_user_files(user_id)
    write_json(files["current_state"], state or {})
    return state or {}















LAST_EMOTION_RECORD_ERROR = ""


def get_last_emotion_records_error():
    """Return the latest emotion_records read error for friendly UI hints."""
    return LAST_EMOTION_RECORD_ERROR


EMOTION_RECORD_FIELDS = [
    "id",
    "user_id",
    "record_date",
    "account_check_frequency",
    "strongest_emotion",
    "operation_impulse",
    "impulse_source",
    "actual_action",
    "anxiety_level",
    "fomo_level",
    "impulse_level",
    "note",
    "ai_emotion_label",
    "ai_risk_level",
    "ai_behavior_biases",
    "ai_reminder",
    "ai_observation_point",
    "ai_analysis",
    "created_at",
    "updated_at",
]


def get_supabase_or_none():
    """Return Supabase when configured; use JSON fallback only without env vars."""
    has_supabase_env = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))
    if not has_supabase_env:
        return None
    return get_supabase_client()


def make_emotion_record_id(user_id, record_date):
    """Create a stable text id for one user + day."""
    safe_user_id = "".join(char for char in str(user_id) if char.isalnum() or char in {"_", "-"})
    safe_date = str(record_date or "").replace("-", "")
    return f"emotion_{safe_user_id}_{safe_date}"


def normalize_emotion_record_for_supabase(user_id, record):
    """Keep only columns that exist in the Supabase emotion_records table."""
    now = datetime.utcnow().isoformat()
    source = dict(record or {})
    record_date = source.get("record_date")
    normalized = {key: source.get(key) for key in EMOTION_RECORD_FIELDS if key in source}
    normalized["user_id"] = str(user_id)
    normalized["record_date"] = str(record_date)
    # Always derive the primary key from user_id + date. Older app versions used emotion_YYYYMMDD, which collides across users.
    normalized["id"] = make_emotion_record_id(user_id, record_date)
    normalized["updated_at"] = now
    normalized.setdefault("created_at", source.get("created_at") or now)
    normalized["anxiety_level"] = int(normalized.get("anxiety_level") or 0)
    normalized["fomo_level"] = int(normalized.get("fomo_level") or 0)
    normalized["impulse_level"] = int(normalized.get("impulse_level") or 0)
    if normalized.get("ai_behavior_biases") is None:
        normalized["ai_behavior_biases"] = []
    if normalized.get("ai_analysis") is None:
        normalized["ai_analysis"] = {}
    return normalized


def read_supabase_rows(response):
    """Read data from a Supabase response object across SDK versions."""
    rows = getattr(response, "data", None)
    if rows is None and isinstance(response, dict):
        rows = response.get("data")
    return rows or []


def load_emotion_records(user_id=None):
    """Load all daily investor emotion records for one authenticated user from Supabase."""
    global LAST_EMOTION_RECORD_ERROR
    LAST_EMOTION_RECORD_ERROR = ""
    if not user_id:
        return []
    supabase = get_supabase_or_none()
    if supabase is not None:
        try:
            response = (
                supabase.table("emotion_records")
                .select("*")
                .eq("user_id", str(user_id))
                .order("record_date")
                .execute()
            )
            rows = read_supabase_rows(response)
            return rows if isinstance(rows, list) else []
        except Exception as error:
            LAST_EMOTION_RECORD_ERROR = str(error)
            return []

    ensure_data_files(user_id)
    files = get_user_files(user_id)
    records = read_json(files["emotion_records"], [])
    return records if isinstance(records, list) else []


def save_emotion_records(records, user_id=None):
    """Save daily emotion records. Supabase is primary; JSON is local fallback."""
    if not user_id:
        return []
    supabase = get_supabase_or_none()
    if supabase is not None:
        try:
            normalized_records = [normalize_emotion_record_for_supabase(user_id, record) for record in (records or [])]
            if normalized_records:
                supabase.table("emotion_records").upsert(
                    normalized_records,
                    on_conflict="user_id,record_date",
                ).execute()
            return normalized_records
        except Exception as error:
            raise RuntimeError(f"Supabase 保存 emotion_records 失败：{error}") from error

    files = get_user_files(user_id)
    write_json(files["emotion_records"], records or [])
    return records or []


def upsert_emotion_record(user_id, record):
    """Create or update one record by user_id + record_date in Supabase."""
    if not user_id:
        raise ValueError("保存情绪记录失败：缺少 user_id。")
    record_date = (record or {}).get("record_date")
    if not record_date:
        raise ValueError("保存情绪记录失败：缺少 record_date。")
    normalized = normalize_emotion_record_for_supabase(user_id, record)
    supabase = get_supabase_or_none()
    if supabase is not None:
        try:
            response = (
                supabase.table("emotion_records")
                .upsert(normalized, on_conflict="user_id,record_date")
                .execute()
            )
            rows = read_supabase_rows(response)
            return rows[0] if rows else normalized
        except Exception as error:
            raise RuntimeError(f"Supabase 写入 emotion_records 失败：{error}") from error

    records = load_emotion_records(user_id)
    replaced = False
    for index, existing in enumerate(records):
        if existing.get("record_date") == record_date:
            records[index] = {**existing, **record}
            replaced = True
            break
    if not replaced:
        records.append(record)
    files = get_user_files(user_id)
    write_json(files["emotion_records"], records)
    return record


def delete_emotion_record(user_id, record_date):
    """Delete one daily emotion record for the current Supabase user."""
    if not user_id or not record_date:
        return []
    supabase = get_supabase_or_none()
    if supabase is not None:
        try:
            supabase.table("emotion_records").delete().eq("user_id", str(user_id)).eq("record_date", str(record_date)).execute()
            return load_emotion_records(user_id)
        except Exception as error:
            raise RuntimeError(f"Supabase 删除 emotion_records 失败：{error}") from error

    records = [record for record in load_emotion_records(user_id) if record.get("record_date") != record_date]
    files = get_user_files(user_id)
    write_json(files["emotion_records"], records)
    return records


def get_emotion_record_by_date(user_id, record_date):
    """Return one daily emotion record by user_id + date from Supabase."""
    if not user_id or not record_date:
        return {}
    supabase = get_supabase_or_none()
    if supabase is not None:
        try:
            response = (
                supabase.table("emotion_records")
                .select("*")
                .eq("user_id", str(user_id))
                .eq("record_date", str(record_date))
                .limit(1)
                .execute()
            )
            rows = read_supabase_rows(response)
            return rows[0] if rows else {}
        except Exception as error:
            raise RuntimeError(f"Supabase 查询 emotion_records 失败：{error}") from error

    for record in load_emotion_records(user_id):
        if record.get("record_date") == record_date:
            return record
    return {}

