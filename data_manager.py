"""Simple JSON storage helpers for the Fund DCA Decision Support Agent."""

import json
from pathlib import Path
from uuid import uuid4


DATA_DIR = Path("data")
USERS_DIR = DATA_DIR / "users"
PLAN_FILE = DATA_DIR / "plan.json"
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
CURRENT_STATE_FILE = DATA_DIR / "current_state.json"


DEFAULT_TRANSACTIONS = []


def default_plan_data():
    """Return an empty multi-plan structure for a newly authenticated user."""
    return {"plans": [], "selected_plan_id": None}


def default_current_state():
    """Return the default latest-state structure for the behavior loop."""
    return {}


def get_user_dir(user_id=None):
    """Return the storage folder for one user.

    Authenticated users use data/{user_id}. The old data/users/{user_id}
    path is still read when it already exists, so previous local data remains available.
    """
    if not user_id:
        return DATA_DIR
    safe_user_id = "".join(char for char in str(user_id) if char.isalnum() or char in {"_", "-"})
    new_dir = DATA_DIR / safe_user_id
    old_dir = USERS_DIR / safe_user_id
    if old_dir.exists() and not new_dir.exists():
        return old_dir
    return new_dir


def get_user_files(user_id=None):
    """Return JSON file paths for one user."""
    base_dir = get_user_dir(user_id)
    state_file = base_dir / "state.json"
    legacy_state_file = base_dir / "current_state.json"
    if legacy_state_file.exists() and not state_file.exists():
        state_file = legacy_state_file
    return {
        "plan": base_dir / "plan.json",
        "transactions": base_dir / "transactions.json",
        "current_state": state_file,
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
    """Create empty JSON files for global data or one authenticated user."""
    DATA_DIR.mkdir(exist_ok=True)
    files = get_user_files(user_id)

    if not files["plan"].exists():
        write_json(files["plan"], default_plan_data())
    if not files["transactions"].exists():
        write_json(files["transactions"], DEFAULT_TRANSACTIONS)
    if not files["current_state"].exists():
        write_json(files["current_state"], default_current_state())


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


def get_plan_by_id(plan_id, user_id=None):
    """Find one plan by id."""
    for plan in load_plans(user_id):
        if plan.get("plan_id") == plan_id or plan.get("id") == plan_id:
            return plan
    return {}


def save_plan(plan, user_id=None):
    """Create or update one investment plan for backward compatibility."""
    plan_id = (plan or {}).get("plan_id") or (plan or {}).get("id")
    if plan_id and get_plan_by_id(plan_id, user_id):
        return update_plan(user_id, plan_id, plan)
    return add_plan(user_id, plan)


def load_transactions(user_id=None):
    """Load all saved transaction records for one authenticated user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    transactions = read_json(files["transactions"], [])
    return transactions if isinstance(transactions, list) else []


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


def load_current_state(user_id=None):
    """Load the latest behavior decision loop state for one authenticated user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    state = read_json(files["current_state"], default_current_state())
    return state if isinstance(state, dict) else default_current_state()


def save_current_state(state, user_id=None):
    """Save the latest behavior decision loop state, overwriting old state."""
    files = get_user_files(user_id)
    write_json(files["current_state"], state or {})
    return state or {}





