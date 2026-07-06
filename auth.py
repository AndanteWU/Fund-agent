"""Email verification login helpers for the Streamlit app."""

import hashlib
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from uuid import uuid4

import streamlit as st


ALLOWED_DOMAINS = {"163.com", "126.com", "yeah.net", "gmail.com"}
CODE_TTL_SECONDS = 5 * 60
SEND_COOLDOWN_SECONDS = 60


SMTP_CONFIG = {
    "163.com": {
        "host": "smtp.163.com",
        "port": 465,
        "user_env": "NETEASE_SMTP_USER",
        "password_env": "NETEASE_SMTP_APP_PASSWORD",
    },
    "126.com": {
        "host": "smtp.163.com",
        "port": 465,
        "user_env": "NETEASE_SMTP_USER",
        "password_env": "NETEASE_SMTP_APP_PASSWORD",
    },
    "yeah.net": {
        "host": "smtp.163.com",
        "port": 465,
        "user_env": "NETEASE_SMTP_USER",
        "password_env": "NETEASE_SMTP_APP_PASSWORD",
    },
    "gmail.com": {
        "host": "smtp.gmail.com",
        "port": 465,
        "user_env": "GMAIL_SMTP_USER",
        "password_env": "GMAIL_SMTP_APP_PASSWORD",
    },
}


EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def normalize_email(email):
    """Normalize an email address for login."""
    return (email or "").strip().lower()


def validate_email(email):
    """Return (is_valid, message, domain)."""
    email = normalize_email(email)
    match = EMAIL_PATTERN.match(email)
    if not match:
        return False, "请输入有效的邮箱地址。", ""

    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS:
        return False, "当前仅支持 163.com、126.com、yeah.net 和 gmail.com 邮箱。", domain

    return True, "", domain


def hash_code(code, salt):
    """Hash verification code before saving it in session state."""
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def generate_code():
    """Generate a six-digit verification code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_user_id(email):
    """Create a stable user id for one email address."""
    normalized_email = normalize_email(email)
    digest = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()[:12]
    return f"user_{digest}"


def get_smtp_settings(domain):
    """Read SMTP settings from environment variables."""
    config = SMTP_CONFIG[domain]
    sender = os.getenv(config["user_env"], "").strip()
    password = os.getenv(config["password_env"], "").strip()
    if not sender or not password:
        return None, (
            f"SMTP 环境变量未配置完整：请设置 {config['user_env']} 和 "
            f"{config['password_env']}。"
        )

    return {
        "host": config["host"],
        "port": config["port"],
        "sender": sender,
        "password": password,
    }, ""


def send_verification_email(email, code):
    """Send the verification code by SMTP over SSL."""
    is_valid, message, domain = validate_email(email)
    if not is_valid:
        return False, message

    settings, error = get_smtp_settings(domain)
    if error:
        return False, error

    msg = EmailMessage()
    msg["Subject"] = "基金定投辅助决策 Agent 登录验证码"
    msg["From"] = settings["sender"]
    msg["To"] = email
    msg.set_content(
        "您好，\n\n"
        f"您的登录验证码是：{code}\n"
        "验证码 5 分钟内有效。若非本人操作，请忽略此邮件。\n\n"
        "本系统不预测市场涨跌，也不提供买卖建议。"
    )

    try:
        with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=20) as server:
            server.login(settings["sender"], settings["password"])
            server.send_message(msg)
    except Exception as error:
        return False, f"验证码发送失败：{error}"

    return True, "验证码已发送，请查收邮箱。"


def init_auth_state():
    """Initialize auth-related session state keys."""
    st.session_state.setdefault("is_logged_in", False)
    st.session_state.setdefault("email", "")
    st.session_state.setdefault("user_id", "")
    st.session_state.setdefault("auth_code_hash", "")
    st.session_state.setdefault("auth_code_salt", "")
    st.session_state.setdefault("auth_code_email", "")
    st.session_state.setdefault("auth_code_expires_at", 0.0)
    st.session_state.setdefault("auth_last_sent_at", 0.0)

def get_supabase_user():
    """Reserved integration point for future Supabase Auth.

    Return a dict like {"user_id": "user_xxx", "email": "name@example.com"}
    when Supabase is connected. Keeping this function here lets the app switch
    auth providers without changing business pages.
    """
    return None


def get_authenticated_user():
    """Return current authenticated user from session or future Supabase Auth."""
    init_auth_state()
    if st.session_state.get("is_logged_in") and st.session_state.get("user_id"):
        return {
            "user_id": st.session_state.get("user_id"),
            "email": st.session_state.get("email", ""),
            "provider": st.session_state.get("auth_mode", "email"),
        }

    supabase_user = get_supabase_user()
    if supabase_user and supabase_user.get("user_id"):
        st.session_state.is_logged_in = True
        st.session_state.user_id = supabase_user["user_id"]
        st.session_state.email = supabase_user.get("email", "")
        st.session_state.auth_mode = "supabase"
        return {
            "user_id": st.session_state.user_id,
            "email": st.session_state.email,
            "provider": "supabase",
        }

    return None



def render_login_page():
    """Render email-code login and return True after login succeeds."""
    init_auth_state()
    if st.session_state.get("is_logged_in") and st.session_state.get("user_id"):
        return True

    st.title("基金定投辅助决策 Agent")
    st.caption("请使用邮箱验证码登录。当前支持网易邮箱和 Gmail。")

    with st.container(border=True):
        email = normalize_email(st.text_input("邮箱地址", value=st.session_state.get("auth_code_email", ""), placeholder="name@gmail.com"))
        send_col, status_col = st.columns([1, 2])

        now = time.time()
        cooldown_left = max(0, int(SEND_COOLDOWN_SECONDS - (now - st.session_state.auth_last_sent_at)))
        send_disabled = cooldown_left > 0

        with send_col:
            send_clicked = st.button(
                "发送验证码" if not send_disabled else f"{cooldown_left} 秒后可重发",
                disabled=send_disabled,
                use_container_width=True,
            )

        if send_clicked:
            is_valid, message, _ = validate_email(email)
            if not is_valid:
                st.warning(message)
            else:
                code = generate_code()
                salt = uuid4().hex
                ok, send_message = send_verification_email(email, code)
                if ok:
                    st.session_state.auth_code_hash = hash_code(code, salt)
                    st.session_state.auth_code_salt = salt
                    st.session_state.auth_code_email = email
                    st.session_state.auth_code_expires_at = time.time() + CODE_TTL_SECONDS
                    st.session_state.auth_last_sent_at = time.time()
                    st.success(send_message)
                    st.rerun()
                else:
                    st.error(send_message)

        with status_col:
            st.caption("验证码 5 分钟有效；同一会话 60 秒内不能重复发送。")

        code_input = st.text_input("验证码", max_chars=6, placeholder="请输入 6 位数字验证码")
        verify_clicked = st.button("登录", type="primary", use_container_width=True)

        if verify_clicked:
            if not st.session_state.auth_code_hash:
                st.warning("请先发送验证码。")
            elif normalize_email(email) != st.session_state.auth_code_email:
                st.warning("当前邮箱与接收验证码的邮箱不一致。")
            elif time.time() > st.session_state.auth_code_expires_at:
                st.warning("验证码已过期，请重新发送。")
            elif hash_code(code_input.strip(), st.session_state.auth_code_salt) != st.session_state.auth_code_hash:
                st.warning("验证码不正确。")
            else:
                st.session_state.is_logged_in = True
                st.session_state.email = st.session_state.auth_code_email
                st.session_state.user_id = generate_user_id(st.session_state.email)
                st.session_state.auth_mode = "email"
                st.session_state.auth_code_hash = ""
                st.session_state.auth_code_salt = ""
                st.success("登录成功，正在进入系统。")
                st.rerun()

    st.info(
        "SMTP 配置请使用 App Password，不要使用邮箱登录密码。"
        "Gmail 需要开启 2FA 后创建 App Password。"
    )
    return False


def logout():
    """Clear login state for current Streamlit session."""
    for key in [
        "is_logged_in",
        "email",
        "user_id",
        "auth_mode",
        "auth_code_hash",
        "auth_code_salt",
        "auth_code_email",
        "auth_code_expires_at",
        "auth_last_sent_at",
    ]:
        st.session_state.pop(key, None)



