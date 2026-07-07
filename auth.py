"""Supabase Auth helpers for the Streamlit app."""

import os

import streamlit as st

try:
    from supabase import create_client
except ImportError:
    create_client = None


def init_auth_state():
    """Initialize auth-related session state keys."""
    st.session_state.setdefault("is_logged_in", False)
    st.session_state.setdefault("email", "")
    st.session_state.setdefault("user_id", "")
    st.session_state.setdefault("auth_mode", "")
    st.session_state.setdefault("supabase_access_token", "")
    st.session_state.setdefault("supabase_refresh_token", "")


def get_supabase_config():
    """Read Supabase project settings from environment variables."""
    url = os.getenv("SUPABASE_URL", "").strip()
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not url or not anon_key:
        return "", "", "请先配置 SUPABASE_URL 和 SUPABASE_ANON_KEY 环境变量。"
    return url, anon_key, ""


@st.cache_resource(show_spinner=False)
def get_supabase_client(url, anon_key):
    """Create one cached Supabase client for the Streamlit process."""
    if create_client is None:
        return None
    return create_client(url, anon_key)


def read_attr(value, name, default=""):
    """Read a field from either an object or a dict returned by Supabase."""
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def normalize_supabase_user(user):
    """Convert Supabase user object into the app's auth shape."""
    user_id = read_attr(user, "id", "")
    email = read_attr(user, "email", "")
    if not user_id:
        return None
    return {"user_id": str(user_id), "email": email or "", "provider": "supabase"}


def save_supabase_session(session):
    """Persist Supabase session fields inside Streamlit session_state."""
    if not session:
        return
    st.session_state.supabase_access_token = read_attr(session, "access_token", "")
    st.session_state.supabase_refresh_token = read_attr(session, "refresh_token", "")


def get_supabase_user():
    """Return the current Supabase-authenticated user if available."""
    init_auth_state()
    if st.session_state.get("auth_mode") == "supabase" and st.session_state.get("user_id"):
        return {
            "user_id": st.session_state.user_id,
            "email": st.session_state.get("email", ""),
            "provider": "supabase",
        }
    return None


def get_authenticated_user():
    """Return the current authenticated Supabase user."""
    init_auth_state()
    return get_supabase_user()


def render_login_page():
    """Render Supabase email/password login and return True after login succeeds."""
    init_auth_state()
    if get_authenticated_user():
        return True

    st.title("基金定投辅助决策 Agent")
    st.caption("请使用 Supabase Auth 账号登录。登录后系统会使用 Supabase user.id 隔离保存你的数据。")

    url, anon_key, config_error = get_supabase_config()
    if create_client is None:
        st.error("当前环境未安装 supabase Python 包，请先安装依赖：pip install supabase")
        return False
    if config_error:
        st.error(config_error)
        return False

    try:
        supabase = get_supabase_client(url, anon_key)
        catch_error = None
    except Exception as error:
        supabase = None
        catch_error = error
    if supabase is None:
        if catch_error:
            st.error(f"Supabase 客户端初始化失败：{catch_error}")
        else:
            st.error("Supabase 客户端初始化失败，请检查依赖和环境变量。")
        return False

    with st.container(border=True):
        mode = st.radio("登录方式", ["登录", "注册"], horizontal=True)
        email = st.text_input("邮箱", placeholder="name@example.com")
        password = st.text_input("密码", type="password")

        if mode == "登录":
            submit_label = "登录"
            help_text = "使用 Supabase Auth 已注册账号登录。"
        else:
            submit_label = "注册并登录"
            help_text = "如果 Supabase 项目开启邮箱确认，请先到邮箱完成确认后再登录。"
        st.caption(help_text)

        if st.button(submit_label, type="primary", use_container_width=True):
            if not email.strip() or not password:
                st.warning("请填写邮箱和密码。")
                return False

            try:
                if mode == "登录":
                    response = supabase.auth.sign_in_with_password(
                        {"email": email.strip(), "password": password}
                    )
                else:
                    response = supabase.auth.sign_up(
                        {"email": email.strip(), "password": password}
                    )

                user = read_attr(response, "user")
                session = read_attr(response, "session")
                if not session:
                    st.warning("Supabase 没有返回登录会话。若刚注册账号，请先完成邮箱确认后再登录。")
                    return False

                normalized_user = normalize_supabase_user(user)
                if not normalized_user:
                    st.warning("Supabase 没有返回可用用户信息，请稍后重试。")
                    return False

                save_supabase_session(session)
                st.session_state.is_logged_in = True
                st.session_state.user_id = normalized_user["user_id"]
                st.session_state.email = normalized_user["email"]
                st.session_state.auth_mode = "supabase"
                st.success("登录成功，正在进入系统。")
                st.rerun()
            except Exception as error:
                st.error(f"Supabase 登录失败：{error}")
                return False

    st.info("Render 部署时请配置 SUPABASE_URL 和 SUPABASE_ANON_KEY。")
    return False


def logout():
    """Clear Supabase login state for current Streamlit session."""
    for key in [
        "is_logged_in",
        "email",
        "user_id",
        "auth_mode",
        "supabase_access_token",
        "supabase_refresh_token",
    ]:
        st.session_state.pop(key, None)



