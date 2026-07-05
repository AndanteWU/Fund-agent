"""Simple JSON storage helpers for the Fund DCA Decision Support Agent."""

import json
from pathlib import Path
from uuid import uuid4


DATA_DIR = Path("data")
PLAN_FILE = DATA_DIR / "plan.json"
TRANSACTIONS_FILE = DATA_DIR / "transactions.json"
CURRENT_STATE_FILE = DATA_DIR / "current_state.json"


def ensure_data_files():
    """Create the data folder and default JSON files if they do not exist."""
    DATA_DIR.mkdir(exist_ok=True)

    if not PLAN_FILE.exists():
        save_plans([])

    if not TRANSACTIONS_FILE.exists():
        save_transactions([])


def read_json(file_path, default_value):
    """Read JSON safely. If the file is missing or broken, return a default value."""
    try:
        with file_path.open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default_value


def write_json(file_path, data):
    """Write data to a JSON file with readable Chinese formatting."""
    DATA_DIR.mkdir(exist_ok=True)
    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def normalize_plan(plan):
    """Make sure one plan has the fields needed by the app."""
    normalized = dict(plan)
    if not normalized.get("id"):
        normalized["id"] = uuid4().hex
    return normalized


def load_plans():
    """Load all saved investment plans.

    Older versions saved one plan as a dict. This function converts that shape
    into a list so existing demo data can still be used.
    """
    ensure_data_files()
    data = read_json(PLAN_FILE, [])

    if isinstance(data, list):
        plans = [normalize_plan(item) for item in data if isinstance(item, dict)]
    elif isinstance(data, dict) and data.get("fund_name"):
        plans = [normalize_plan(data)]
    else:
        plans = []

    save_plans(plans)
    return plans


def save_plans(plans):
    """Save all investment plans."""
    write_json(PLAN_FILE, [normalize_plan(plan) for plan in plans])


def load_plan():
    """Load the first saved plan for backward compatibility."""
    plans = load_plans()
    return plans[0] if plans else {}


def get_plan_by_id(plan_id):
    """Find one plan by id."""
    for plan in load_plans():
        if plan.get("id") == plan_id:
            return plan
    return {}


def save_plan(plan):
    """Create or update one investment plan."""
    plan = normalize_plan(plan)
    plans = load_plans()

    for index, existing_plan in enumerate(plans):
        if existing_plan.get("id") == plan.get("id"):
            plans[index] = plan
            save_plans(plans)
            return plan

    plans.append(plan)
    save_plans(plans)
    return plan


def load_transactions():
    """Load all saved transaction records."""
    ensure_data_files()
    return read_json(TRANSACTIONS_FILE, [])


def save_transactions(transactions):
    """Save the whole transaction list."""
    write_json(TRANSACTIONS_FILE, transactions)


def add_transaction(transaction):
    """Append one transaction record and save it."""
    transactions = load_transactions()
    transactions.append(transaction)
    save_transactions(transactions)
    return transactions



def default_current_state():
    """Return the default latest-state structure for the behavior loop."""
    return {
        "latest_operation": {},
        "qa_answers": {},
        "behavior_diagnosis": {},
    }


def load_current_state():
    """Load the latest behavior decision loop state."""
    ensure_data_files()
    state = read_json(CURRENT_STATE_FILE, default_current_state())
    if not isinstance(state, dict):
        return default_current_state()

    default_state = default_current_state()
    default_state.update(state)
    return default_state


def save_current_state(state):
    """Save the latest behavior decision loop state, overwriting old state."""
    default_state = default_current_state()
    default_state.update(state or {})
    write_json(CURRENT_STATE_FILE, default_state)
    return default_state
