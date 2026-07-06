"""Simple JSON storage helpers for the Fund DCA Decision Support Agent."""

import json
import shutil
from pathlib import Path
from uuid import uuid4


DATA_DIR = Path("data")
USERS_DIR = DATA_DIR / "users"
PLAN_FILE = DATA_DIR / "plan.json"
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
CURRENT_STATE_FILE = DATA_DIR / "current_state.json"


DEFAULT_PLAN = []
DEFAULT_TRANSACTIONS = []


def default_current_state():
    """Return the default latest-state structure for the behavior loop."""
    return {
        "latest_operation": {},
        "qa_answers": {},
        "behavior_diagnosis": {},
    }


def get_user_dir(user_id=None):
    """Return the storage folder for one temporary user."""
    if not user_id:
        return DATA_DIR
    safe_user_id = "".join(char for char in str(user_id) if char.isalnum() or char in {"_", "-"})
    return USERS_DIR / safe_user_id


def get_user_files(user_id=None):
    """Return JSON file paths for one temporary user."""
    base_dir = get_user_dir(user_id)
    return {
        "plan": base_dir / "plan.json",
        "transactions": base_dir / "transactions.json",
        "current_state": base_dir / "current_state.json",
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


def copy_or_create_file(source_path, target_path, default_value):
    """Copy demo data when available, otherwise create a default JSON file."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        return
    if source_path.exists():
        shutil.copyfile(source_path, target_path)
    else:
        write_json(target_path, default_value)


def ensure_data_files(user_id=None):
    """Create default JSON files for global data or one temporary user."""
    DATA_DIR.mkdir(exist_ok=True)

    if user_id:
        files = get_user_files(user_id)
        copy_or_create_file(PLAN_FILE, files["plan"], DEFAULT_PLAN)
        copy_or_create_file(TRANSACTIONS_FILE, files["transactions"], DEFAULT_TRANSACTIONS)
        copy_or_create_file(CURRENT_STATE_FILE, files["current_state"], default_current_state())
        return

    if not PLAN_FILE.exists():
        write_json(PLAN_FILE, DEFAULT_PLAN)
    if not TRANSACTIONS_FILE.exists():
        write_json(TRANSACTIONS_FILE, DEFAULT_TRANSACTIONS)
    if not CURRENT_STATE_FILE.exists():
        write_json(CURRENT_STATE_FILE, default_current_state())


def normalize_plan(plan):
    """Make sure one plan has the fields needed by the app."""
    normalized = dict(plan)
    if not normalized.get("id"):
        normalized["id"] = uuid4().hex
    return normalized


def load_plans(user_id=None):
    """Load all saved investment plans for one temporary user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    data = read_json(files["plan"], [])

    if isinstance(data, list):
        plans = [normalize_plan(item) for item in data if isinstance(item, dict)]
    elif isinstance(data, dict) and data.get("fund_name"):
        plans = [normalize_plan(data)]
    else:
        plans = []

    save_plans(plans, user_id)
    return plans


def save_plans(plans, user_id=None):
    """Save all investment plans for one temporary user."""
    files = get_user_files(user_id)
    write_json(files["plan"], [normalize_plan(plan) for plan in plans])


def load_plan(user_id=None):
    """Load the first saved plan for backward compatibility."""
    plans = load_plans(user_id)
    return plans[0] if plans else {}


def get_plan_by_id(plan_id, user_id=None):
    """Find one plan by id."""
    for plan in load_plans(user_id):
        if plan.get("id") == plan_id:
            return plan
    return {}


def save_plan(plan, user_id=None):
    """Create or update one investment plan for one temporary user."""
    plan = normalize_plan(plan)
    plans = load_plans(user_id)

    for index, existing_plan in enumerate(plans):
        if existing_plan.get("id") == plan.get("id"):
            plans[index] = plan
            save_plans(plans, user_id)
            return plan

    plans.append(plan)
    save_plans(plans, user_id)
    return plan


def load_transactions(user_id=None):
    """Load all saved transaction records for one temporary user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    return read_json(files["transactions"], [])


def save_transactions(transactions, user_id=None):
    """Save the whole transaction list for one temporary user."""
    files = get_user_files(user_id)
    write_json(files["transactions"], transactions)


def add_transaction(transaction, user_id=None):
    """Append one transaction record and save it for one temporary user."""
    transactions = load_transactions(user_id)
    transactions.append(transaction)
    save_transactions(transactions, user_id)
    return transactions


def load_current_state(user_id=None):
    """Load the latest behavior decision loop state for one temporary user."""
    ensure_data_files(user_id)
    files = get_user_files(user_id)
    state = read_json(files["current_state"], default_current_state())
    if not isinstance(state, dict):
        return default_current_state()

    default_state = default_current_state()
    default_state.update(state)
    return default_state


def save_current_state(state, user_id=None):
    """Save the latest behavior decision loop state, overwriting old state."""
    default_state = default_current_state()
    default_state.update(state or {})
    files = get_user_files(user_id)
    write_json(files["current_state"], default_state)
    return default_state
