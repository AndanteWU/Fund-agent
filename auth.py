"""Supabase Email OTP auth helpers for the Streamlit app.

This module intentionally keeps login state simple and stable:
the app enters the main page only when the current Streamlit session has
a Supabase user_id. It does not run browser-token restore logic on startup.
"""

import time

import streamlit as st

from supabase_client import get_supabase_client


OTP_TYPE = "email"
LOGIN_SEND_MAX_RETRIES = 2
LOGIN_RETRY_DELAY_SECONDS = 0.8


def init_auth_state():
    """Initialize auth-related session state keys."""
    st.session_state.setdefault("is_logged_in", False)
    st.session_state.setdefault("email", "")
    st.session_state.setdefault("user_id", "")
    st.session_state.setdefault("otp_email", "")
    st.session_state.setdefault("otp_sent", False)
    st.session_state.setdefault("auth_error", "")
    st.session_state.setdefault("access_token", "")
    st.session_state.setdefault("refresh_token", "")
    st.session_state.setdefault("expires_at", "")


def read_attr(value, name, default=""):
    """Read a field from either an object or a dict returned by Supabase."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def normalize_email(email):
    """Normalize email input before calling Supabase Auth."""
    return (email or "").strip().lower()


def normalize_supabase_user(user):
    """Convert Supabase user object into the app's auth shape."""
    user_id = read_attr(user, "id", "")
    email = read_attr(user, "email", "")
    if not user_id:
        return None
    return {"user_id": str(user_id), "email": email or "", "provider": "supabase"}


def get_response_user(response):
    """Extract a user from Supabase auth responses."""
    user = read_attr(response, "user")
    session = read_attr(response, "session")
    if user is None and session is not None:
        user = read_attr(session, "user")
    return user


def get_response_session(response):
    """Extract a Supabase session object from auth responses."""
    if response is None:
        return None
    session = read_attr(response, "session")
    if session is not None:
        return session
    return response if read_attr(response, "access_token") else None


def extract_session_tokens(session):
    """Return access token, refresh token, and expiry from a Supabase session."""
    if session is None:
        return "", "", ""

    access_token = read_attr(session, "access_token", "")
    refresh_token = read_attr(session, "refresh_token", "")
    expires_at = read_attr(session, "expires_at", "")
    expires_in = read_attr(session, "expires_in", "")

    if not expires_at and expires_in:
        try:
            expires_at = str(int(time.time()) + int(expires_in))
        except (TypeError, ValueError):
            expires_at = ""

    return access_token or "", refresh_token or "", str(expires_at or "")


def is_timeout_error(error):
    """Return True when an auth error looks like a network timeout."""
    text = str(error).lower()
    return isinstance(error, TimeoutError) or "timed out" in text or "timeout" in text


def friendly_auth_error(error, prefix):
    """Convert low-level auth exceptions into user-facing text."""
    if is_timeout_error(error):
        return "登录服务响应较慢，请稍后重试。"
    return f"{prefix}：{error}"


def send_login_code(email):
    """Ask Supabase Auth to send a 6-digit Email OTP."""
    normalized_email = normalize_email(email)
    if not normalized_email:
        raise ValueError("请输入邮箱地址。")

    last_error = None
    for attempt in range(LOGIN_SEND_MAX_RETRIES + 1):
        try:
            supabase = get_supabase_client()
            return supabase.auth.sign_in_with_otp({"email": normalized_email})
        except Exception as error:
            last_error = error
            if attempt < LOGIN_SEND_MAX_RETRIES:
                time.sleep(LOGIN_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            if is_timeout_error(error):
                raise TimeoutError("登录服务响应较慢，请稍后重试。") from error
            raise

    raise last_error


def verify_login_code(email, code):
    """Verify the Email OTP with Supabase Auth."""
    normalized_email = normalize_email(email)
    normalized_code = (code or "").strip()
    if not normalized_email:
        raise ValueError("请输入邮箱地址。")
    if not normalized_code:
        raise ValueError("请输入验证码。")

    supabase = get_supabase_client()
    return supabase.auth.verify_otp(
        {"email": normalized_email, "token": normalized_code, "type": OTP_TYPE}
    )


def get_current_supabase_session_user():
    """Return the current Supabase session user when available."""
    supabase = get_supabase_client()
    session_response = supabase.auth.get_session()
    session = read_attr(session_response, "session", session_response)
    return read_attr(session, "user")


def save_auth_state(user, session=None, fallback_email=""):
    """Save the verified Supabase user into Streamlit session_state."""
    normalized_user = normalize_supabase_user(user)
    if not normalized_user:
        raise RuntimeError("Supabase 没有返回可用用户信息，请重新发送验证码后再试。")

    access_token, refresh_token, expires_at = extract_session_tokens(session)
    st.session_state.is_logged_in = True
    st.session_state.email = normalized_user["email"] or fallback_email
    st.session_state.user_id = normalized_user["user_id"]
    st.session_state.access_token = access_token
    st.session_state.refresh_token = refresh_token
    st.session_state.expires_at = expires_at
    st.session_state.otp_email = ""
    st.session_state.otp_sent = False
    st.session_state.auth_error = ""
    return normalized_user


def get_authenticated_user():
    """Return the current logged-in user from Streamlit session_state only."""
    init_auth_state()
    if not st.session_state.get("is_logged_in"):
        return None
    if not st.session_state.get("user_id"):
        return None

    return {
        "user_id": st.session_state.user_id,
        "email": st.session_state.get("email", ""),
        "provider": "supabase",
    }


def complete_login_from_response(response, fallback_email):
    """Persist Supabase verify_otp response into session_state."""
    session = get_response_session(response)
    user = get_response_user(response)

    if user is None and session is not None:
        user = read_attr(session, "user")
    if user is None:
        user = get_current_supabase_session_user()

    save_auth_state(user, session, fallback_email)


def render_login_page():
    """Render the Email OTP login page."""
    init_auth_state()
    if get_authenticated_user():
        return True

    st.title("基金投资情绪管理 Agent")
    st.caption(
        "请输入邮箱并填写收到的 6 位验证码。登录后，系统会根据 Supabase user_id "
        "隔离保存你的情绪记录。"
    )

    with st.container(border=True):
        if not st.session_state.otp_sent:
            email = normalize_email(
                st.text_input(
                    "邮箱",
                    value=st.session_state.get("otp_email", ""),
                    placeholder="name@example.com",
                )
            )
            if st.button("发送验证码", use_container_width=True, type="primary"):
                try:
                    send_login_code(email)
                    st.session_state.otp_email = email
                    st.session_state.otp_sent = True
                    st.session_state.auth_error = ""
                    st.success("验证码已发送，请查收邮箱。")
                    st.rerun()
                except Exception as error:
                    st.session_state.auth_error = friendly_auth_error(
                        error, "验证码发送失败"
                    )
                    st.error(st.session_state.auth_error)
        else:
            st.write(f"验证码已发送至：{st.session_state.otp_email}")
            code = st.text_input("验证码", placeholder="请输入邮箱中的 6 位验证码")

            if st.button("登录", type="primary", use_container_width=True):
                try:
                    response = verify_login_code(st.session_state.otp_email, code)
                    complete_login_from_response(response, st.session_state.otp_email)
                    st.success("登录成功，正在进入系统。")
                    st.rerun()
                except Exception as error:
                    st.session_state.auth_error = f"验证码验证失败：{error}"
                    st.error(st.session_state.auth_error)

            if st.button("重新发送验证码", use_container_width=True):
                try:
                    send_login_code(st.session_state.otp_email)
                    st.session_state.auth_error = ""
                    st.success("验证码已重新发送，请查收邮箱。")
                except Exception as error:
                    st.session_state.auth_error = friendly_auth_error(
                        error, "验证码重新发送失败"
                    )
                    st.error(st.session_state.auth_error)

            if st.button("更换邮箱", use_container_width=True):
                st.session_state.otp_email = ""
                st.session_state.otp_sent = False
                st.session_state.auth_error = ""
                st.rerun()

    if st.session_state.get("auth_error"):
        st.caption(st.session_state.auth_error)

    st.info(
        "请在 Supabase Auth 中启用 Email provider，并确认邮件模板包含 6 位 OTP token，"
        "而不是 Magic Link URL。"
    )
    return False


def logout():
    """Sign out from Supabase and clear the current Streamlit auth state."""
    try:
        get_supabase_client().auth.sign_out()
    except Exception:
        pass

    for key in [
        "is_logged_in",
        "email",
        "user_id",
        "otp_email",
        "otp_sent",
        "auth_error",
        "access_token",
        "refresh_token",
        "expires_at",
    ]:
        st.session_state.pop(key, None)
