"""
PathPilot AI — Your Career Companion
Rebuilt from scratch for reliability: every external call (DB, GitHub API,
optional Gemini AI) is wrapped in try/except so a single failure shows a
friendly message instead of crashing the whole page.
"""
import streamlit as st
import requests
import random
import json
import sqlite3
import hashlib
import os
import tempfile
from datetime import date, datetime, timedelta

# ══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG (must be the first Streamlit call)
# ══════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="PathPilot AI — Your Career Companion", page_icon="🧭", layout="wide")

# ══════════════════════════════════════════════════════════════════════════
# OPTIONAL AI (Gemini) — never required, always fails safe.
# Tries a list of current model names in order, since Google periodically
# retires older ones (e.g. gemini-1.5-flash and gemini-2.0-flash are both
# already shut down as of mid-2026).
# ══════════════════════════════════════════════════════════════════════════
GEMINI_MODEL_CANDIDATES = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]


def get_gemini_client():
    try:
        key = st.secrets.get("GEMINI_API_KEY", None)
    except Exception:
        key = None
    if not key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        return genai
    except Exception:
        return None


AI_CLIENT = get_gemini_client()


def ai_generate(prompt: str, fallback: str) -> str:
    if AI_CLIENT is None:
        return fallback
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            model = AI_CLIENT.GenerativeModel(model_name)
            resp = model.generate_content(prompt)
            text = getattr(resp, "text", "") or ""
            if text.strip():
                return text.strip()
        except Exception:
            continue
    return fallback


AI_AVAILABLE = AI_CLIENT is not None

# ══════════════════════════════════════════════════════════════════════════
# AUTH — real accounts backed by SQLite. Passwords are salted + hashed
# (PBKDF2-SHA256), never stored in plain text. The DB path auto-falls-back
# to the system temp folder if the app folder isn't writable, and every
# DB call fails safe (returns an error message instead of crashing).
# ══════════════════════════════════════════════════════════════════════════
def _resolve_db_path():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "pathpilot_users.db"),
        os.path.join(tempfile.gettempdir(), "pathpilot_users.db"),
    ]
    for path in candidates:
        try:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, "
                "name TEXT, target_role TEXT, password_hash TEXT NOT NULL, salt TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            conn.commit()
            conn.close()
            return path
        except Exception:
            continue
    return candidates[-1]


DB_PATH = _resolve_db_path()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, "
        "name TEXT, target_role TEXT, password_hash TEXT NOT NULL, salt TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    return conn


def hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return pw_hash.hex(), salt.hex()


def create_account(username: str, email: str, name: str, password: str):
    username = (username or "").strip().lower()
    if not username or not password:
        return False, "Username and password are required."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    try:
        conn = get_db_connection()
    except Exception as e:
        return False, f"Account database unavailable ({e}). Try 'Continue as guest' instead."
    try:
        pw_hash, salt_hex = hash_password(password)
        conn.execute(
            "INSERT INTO users (username, email, name, target_role, password_hash, salt, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, (email or "").strip(), (name or "").strip(), "", pw_hash, salt_hex, datetime.now().isoformat()),
        )
        conn.commit()
        return True, "Account created — you're logged in."
    except sqlite3.IntegrityError:
        return False, "That username is already taken — try another."
    except Exception as e:
        return False, f"Couldn't create the account ({e}). Try 'Continue as guest' instead."
    finally:
        conn.close()


def verify_login(username: str, password: str):
    username = (username or "").strip().lower()
    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT username, email, name, target_role, password_hash, salt FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        check_hash, _ = hash_password(password, bytes.fromhex(row[5]))
    except Exception:
        return None
    if check_hash == row[4]:
        return {"username": row[0], "email": row[1], "name": row[2], "target_role": row[3]}
    return None


def sync_profile_to_db(username, name, email, target_role):
    if not username:
        return
    try:
        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET name=?, email=?, target_role=? WHERE username=?",
            (name, email, target_role, username),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════
# SESSION STATE DEFAULTS
# ══════════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "theme": "Light",
    "logged_in": False,
    "username": "",
    "guest_mode": False,
    "auth_error": "",
    "user_name": "",
    "user_email": "",
    "user_target_role": "",
    "xp": 0,
    "streak": 1,
    "last_active": str(date.today()),
    "badges": [],
    "roadmap": None,
    "roadmap_progress": {},
    "goals": [],
    "resume_data": {},
    "cover_letter": "",
    "portfolio_data": {},
    "projects": [],
    "study_plan": [],
    "chat_history": [],
    "mock_interview_qs": [],
    "mock_interview_idx": 0,
    "mock_interview_score": 0,
    "career_match_result": None,
    "github_username": "",
    "leetcode_solved": 0,
    "premium": False,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def add_xp(amount, badge=None):
    st.session_state.xp += amount
    if badge and badge not in st.session_state.badges:
        st.session_state.badges.append(badge)


def touch_streak():
    today = str(date.today())
    if st.session_state.last_active != today:
        yesterday = str(date.today() - timedelta(days=1))
        st.session_state.streak = st.session_state.streak + 1 if st.session_state.last_active == yesterday else 1
        st.session_state.last_active = today


touch_streak()

# ══════════════════════════════════════════════════════════════════════════
# THEME / CSS
# ══════════════════════════════════════════════════════════════════════════
if st.session_state.theme == "Dark":
    BG_A, BG_B, BG_C = "#0f0c29", "#1a1440", "#12102a"
    CARD, CARD_SOLID = "rgba(30,25,66,0.66)", "#1a1533"
    TEXT, SUBTEXT, BORDER = "#f1f0ff", "#aea9d6", "rgba(139,92,246,0.30)"
    BLOB1, BLOB2 = "rgba(124,58,237,0.35)", "rgba(6,182,212,0.22)"
else:
    BG_A, BG_B, BG_C = "#eef2ff", "#f5f3ff", "#e0f7fa"
    CARD, CARD_SOLID = "rgba(255,255,255,0.78)", "#ffffff"
    TEXT, SUBTEXT, BORDER = "#1e1b3a", "#5b5b7a", "rgba(124,58,237,0.16)"
    BLOB1, BLOB2 = "rgba(124,58,237,0.16)", "rgba(6,182,212,0.16)"

ACCENT1, ACCENT2, ACCENT3 = "#7c3aed", "#06b6d4", "#ec4899"

try:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
        * {{ transition: background-color .2s ease, border-color .2s ease, box-shadow .2s ease, transform .12s ease; }}
        .stApp {{
            background: linear-gradient(135deg, {BG_A} 0%, {BG_B} 45%, {BG_C} 100%);
            background-attachment: fixed;
        }}
        section[data-testid="stSidebar"] {{ background: {CARD_SOLID}; border-right: 1px solid {BORDER}; }}
        section[data-testid="stSidebar"] div[role="radiogroup"] label {{ border-radius: 10px; padding: 0.3rem 0.5rem; }}
        section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
            background: linear-gradient(90deg, rgba(124,58,237,0.12), rgba(6,182,212,0.10));
        }}
        .ppai-header {{ text-align:center; padding: 1rem 0 0.5rem; }}
        .ppai-header h1 {{
            font-size: clamp(1.6rem, 5vw, 2.6rem); font-weight: 800;
            background: linear-gradient(120deg, {ACCENT1} 0%, {ACCENT2} 50%, {ACCENT3} 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.25rem;
        }}
        .ppai-header p {{ color: {SUBTEXT}; font-size: 1rem; }}
        .ppai-card {{
            background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px;
            padding: 1.1rem 1.3rem; margin-bottom: 0.9rem; color: {TEXT};
            box-shadow: 0 2px 10px rgba(31,23,74,0.06);
        }}
        .ppai-card:hover {{ box-shadow: 0 10px 24px rgba(124,58,237,0.15); transform: translateY(-2px); }}
        .ppai-auth-card {{ max-width: 480px; margin: 0 auto; }}
        .ppai-badge {{
            display:inline-block; background: linear-gradient(135deg,{ACCENT1},{ACCENT3}); color:white;
            font-weight:600; font-size:0.75rem; padding:0.2rem 0.7rem; border-radius:20px; margin:2px;
        }}
        .ppai-tag {{
            display:inline-block; border:1px solid {BORDER}; color:{SUBTEXT};
            font-size:0.72rem; padding:0.15rem 0.55rem; border-radius:12px; margin:2px;
        }}
        .ppai-topic {{ font-size:1.05rem; font-weight:700; color:{TEXT}; margin-bottom:0.4rem; }}
        .ppai-sub {{ color:{SUBTEXT}; font-size:0.88rem; }}
        .stButton > button {{
            background: linear-gradient(135deg, {ACCENT1}, {ACCENT2}) !important; color: white !important;
            border: none !important; border-radius: 10px !important; padding: 0.55rem 1.4rem !important;
            font-weight: 600 !important; width: 100%; box-shadow: 0 2px 8px rgba(124,58,237,0.22);
        }}
        .stButton > button:hover {{ transform: translateY(-2px); box-shadow: 0 8px 18px rgba(124,58,237,0.32) !important; }}
        div[data-testid="stMetric"] {{ background:{CARD}; border:1px solid {BORDER}; border-radius:14px; padding:0.8rem; }}
        div[data-testid="stMetricLabel"], div[data-testid="stMetricValue"] {{ color: {TEXT} !important; }}
        div[data-testid="stForm"] {{ background:{CARD}; border:1px solid {BORDER}; border-radius:16px; padding:1.3rem; }}
        .stTabs [aria-selected="true"] {{ color: {ACCENT1} !important; font-weight: 700; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    pass  # a CSS failure should never block the app from rendering

# ══════════════════════════════════════════════════════════════════════════
# CAREER DOMAIN DATA
# ══════════════════════════════════════════════════════════════════════════
DOMAINS = {
    "Data Science": {
        "skills": ["Python", "Statistics", "SQL", "Pandas/NumPy", "Machine Learning", "Data Visualization"],
        "roles": ["Data Analyst", "Data Scientist", "ML Engineer"],
    },
    "Web Development": {
        "skills": ["HTML/CSS", "JavaScript", "React", "Node.js", "REST APIs", "Git"],
        "roles": ["Frontend Developer", "Backend Developer", "Full-Stack Developer"],
    },
    "Cybersecurity": {
        "skills": ["Networking", "Linux", "Security Fundamentals", "Penetration Testing", "Cryptography"],
        "roles": ["SOC Analyst", "Penetration Tester", "Security Engineer"],
    },
    "AI / Machine Learning": {
        "skills": ["Python", "Linear Algebra", "Deep Learning", "PyTorch/TensorFlow", "NLP or CV"],
        "roles": ["ML Engineer", "AI Researcher", "Applied Scientist"],
    },
    "Cloud / DevOps": {
        "skills": ["Linux", "Docker", "Kubernetes", "CI/CD", "AWS/Azure/GCP", "Terraform"],
        "roles": ["DevOps Engineer", "Cloud Engineer", "SRE"],
    },
    "Product Management": {
        "skills": ["Market Research", "Roadmapping", "SQL basics", "Communication", "Agile/Scrum"],
        "roles": ["Associate PM", "Product Manager"],
    },
}
LEVELS = ["Beginner", "Intermediate", "Advanced"]

BADGE_DEFS = {
    "first_test": "🧭 Explorer — took the Career Match Test",
    "first_roadmap": "🗺️ Planner — generated a roadmap",
    "first_goal": "🎯 Goal Setter — added a goal",
    "resume_built": "📄 Resume Ready",
    "streak_3": "🔥 3-Day Streak",
    "chat_started": "💬 Mentor's Favorite",
}


def award(key):
    label = BADGE_DEFS.get(key)
    if label:
        add_xp(15, label)

# ══════════════════════════════════════════════════════════════════════════
# AUTH GATE
# ══════════════════════════════════════════════════════════════════════════
if not st.session_state.logged_in and not st.session_state.guest_mode:
    st.markdown(
        """<div class="ppai-header"><h1>🧭 PathPilot AI</h1>
        <p>Your all-in-one career companion — plan, prepare, and track your journey to your first (or next) job.</p></div>""",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown('<div class="ppai-card ppai-auth-card">', unsafe_allow_html=True)
        tab_login, tab_signup = st.tabs(["🔑 Log in", "🆕 Sign up"])

        with tab_login:
            with st.form("login_form"):
                lu = st.text_input("Username")
                lp = st.text_input("Password", type="password")
                do_login = st.form_submit_button("Log in", use_container_width=True)
            if do_login:
                user = verify_login(lu, lp)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.username = user["username"]
                    st.session_state.user_name = user["name"] or user["username"]
                    st.session_state.user_email = user["email"]
                    st.session_state.user_target_role = user["target_role"]
                    st.session_state.auth_error = ""
                    st.rerun()
                else:
                    st.session_state.auth_error = "Incorrect username or password."

        with tab_signup:
            with st.form("signup_form"):
                su_name = st.text_input("Your name")
                su_username = st.text_input("Choose a username")
                su_email = st.text_input("Email (optional)")
                su_pw = st.text_input("Choose a password", type="password")
                su_pw2 = st.text_input("Confirm password", type="password")
                do_signup = st.form_submit_button("Create account", use_container_width=True)
            if do_signup:
                if su_pw != su_pw2:
                    st.session_state.auth_error = "Passwords don't match."
                else:
                    ok, msg = create_account(su_username, su_email, su_name, su_pw)
                    if ok:
                        st.session_state.logged_in = True
                        st.session_state.username = su_username.strip().lower()
                        st.session_state.user_name = su_name.strip() or su_username
                        st.session_state.user_email = su_email.strip()
                        st.session_state.auth_error = ""
                        st.rerun()
                    else:
                        st.session_state.auth_error = msg

        if st.session_state.auth_error:
            st.error(st.session_state.auth_error)

        st.divider()
        if st.button("Continue as guest →", use_container_width=True):
            st.session_state.guest_mode = True
            st.rerun()
        st.caption("Guest mode skips account creation, but your data won't be saved between visits.")
        st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
PAGES = [
    "🏠 Dashboard", "🧭 Career Match Test", "🗺️ AI Roadmap Generator", "💬 AI Mentor Chat",
    "📊 Skill Gap Analysis", "📅 Study Planner", "🎯 Goals & Progress", "🏆 Achievements",
    "📄 ATS Resume Builder", "✉️ Cover Letter Generator", "🌐 Portfolio Generator",
    "💡 AI Project Generator", "🎤 Mock Interview", "📈 Placement Readiness Score",
    "💰 Salary Predictor", "💼 Jobs & Internships", "🐙 GitHub & LeetCode Tracker",
    "🖼️ Project Showcase", "⚙️ Settings",
]

with st.sidebar:
    st.markdown("## 🧭 PathPilot AI")
    if st.session_state.logged_in:
        st.markdown(f"**Hi, {st.session_state.user_name or st.session_state.username}** 👋")
        st.caption(f"✅ Logged in as **{st.session_state.username}**")
    else:
        st.caption("👤 Browsing as guest — sign up in ⚙️ Settings to save your data.")
    level = st.session_state.xp // 100 + 1
    st.caption(f"Level {level} · {st.session_state.xp} XP")
    st.progress(min((st.session_state.xp % 100) / 100, 1.0))
    c1, c2 = st.columns(2)
    c1.metric("🔥 Streak", st.session_state.streak)
    c2.metric("🏆 Badges", len(st.session_state.badges))
    if not AI_AVAILABLE:
        st.caption("💡 AI chat/interview running in offline fallback mode (no Gemini key set).")
    st.divider()

    page = st.radio("Navigate", PAGES, label_visibility="collapsed")

    st.divider()
    st.session_state.theme = st.radio("Theme", ["Light", "Dark"], index=0 if st.session_state.theme == "Light" else 1, horizontal=True)
    if st.session_state.logged_in and st.button("🚪 Log out", use_container_width=True):
        for k in ["logged_in", "guest_mode", "username", "user_name", "user_email", "user_target_role"]:
            st.session_state[k] = DEFAULTS[k]
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    st.markdown(
        """<div class="ppai-header"><h1>🧭 PathPilot AI</h1>
        <p>Your all-in-one career companion — plan, prepare, and track your journey to your first (or next) job.</p></div>""",
        unsafe_allow_html=True,
    )
    if not st.session_state.user_name:
        st.info("👋 New here? Head to **⚙️ Settings** to set your name and target role — it personalizes everything below.")

    # ── Your progress (real, session-based numbers) ──
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Roadmap", "Started" if st.session_state.roadmap else "Not started")
    c2.metric("Goals set", len(st.session_state.goals))
    c3.metric("Resume", "Built" if st.session_state.resume_data else "Pending")
    c4.metric("Mock interviews", st.session_state.mock_interview_score)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Feature grid ──
    st.markdown("#### What you can do here")
    features = [
        ("🧭", "Career Match Test", "A 2-minute quiz that points you toward the domain that fits how you actually think."),
        ("🗺️", "AI Roadmap Generator", "Turns your target domain into a concrete 12-week, week-by-week study plan."),
        ("💬", "AI Mentor Chat", "Ask career questions any time and get grounded, practical answers back."),
        ("📄", "ATS Resume Builder", "Build a resume structured the way applicant-tracking systems actually parse."),
        ("🎤", "Mock Interview", "Practice real interview questions for your domain and get quick feedback."),
        ("📈", "Placement Readiness Score", "One number that shows how prepared you are, and exactly what's missing."),
    ]
    for row_start in range(0, len(features), 3):
        cols = st.columns(3)
        for col, (icon, title, desc) in zip(cols, features[row_start:row_start + 3]):
            with col:
                st.markdown(
                    f'<div class="ppai-card">{icon} <b>{title}</b><br><span class="ppai-sub">{desc}</span></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── How it works ──
    st.markdown("#### How it works")
    steps = [
        ("1", "Take the Career Match Test", "Two minutes of honest self-rating tells us where to point you."),
        ("2", "Get your roadmap", "A 12-week plan built around your domain and current level."),
        ("3", "Build & practice", "Resume, cover letter, projects, and mock interviews — all in one place."),
        ("4", "Track readiness", "Watch your Placement Readiness Score climb as you check things off."),
    ]
    scols = st.columns(4)
    for col, (num, title, desc) in zip(scols, steps):
        with col:
            st.markdown(
                f'<div class="ppai-card" style="text-align:center;">'
                f'<div class="ppai-badge" style="font-size:0.9rem;">{num}</div>'
                f'<div class="ppai-topic" style="margin-top:0.4rem;">{title}</div>'
                f'<span class="ppai-sub">{desc}</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Badges ──
    if st.session_state.badges:
        st.markdown("#### Your badges")
        st.markdown("".join(f'<span class="ppai-badge">{b}</span>' for b in st.session_state.badges), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

    # ── FAQ ──
    st.markdown("#### Frequently asked questions")
    faqs = [
        ("Is this actually free?", "Yes — every feature here runs in your session at no cost. No payment is ever required to use it."),
        ("Is my data saved?", "If you create an account, your profile is saved. Roadmap, goals, and resume progress live in your current session and reset if you close the app without an account, or if the app restarts."),
        ("Does the AI Mentor Chat use real AI?", "Yes, when a Gemini API key is configured. Otherwise it falls back to curated tips so the feature still works."),
        ("Can I edit my profile later?", "Yes, any time from ⚙️ Settings — changes save immediately to your account if you're logged in."),
    ]
    for q, a in faqs:
        with st.expander(q):
            st.write(a)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: CAREER MATCH TEST
# ══════════════════════════════════════════════════════════════════════════
elif page == "🧭 Career Match Test":
    st.markdown("### 🧭 Career Match Test")
    st.caption("Rate how much each statement sounds like you. Takes ~2 minutes.")
    statements = {
        "Data Science": "I enjoy finding patterns and stories hidden inside numbers and data.",
        "Web Development": "I like designing how a website or app looks and feels for users.",
        "AI / Machine Learning": "I'm fascinated by teaching computers to learn from examples.",
        "Cybersecurity": "I like thinking like an attacker to find and fix weaknesses in systems.",
        "Cloud / DevOps": "I enjoy automating repetitive tasks and keeping systems running smoothly.",
        "Product Management": "I like understanding what users need and coordinating a team to build it.",
    }
    scores = {}
    for domain, text in statements.items():
        scores[domain] = st.slider(text, 1, 5, 3, key=f"cm_{domain}")

    if st.button("See my match →"):
        best = max(scores, key=scores.get)
        st.session_state.career_match_result = best
        award("first_test")
        st.rerun()

    if st.session_state.career_match_result:
        best = st.session_state.career_match_result
        st.success(f"Your strongest match: **{best}**")
        st.markdown(f'<div class="ppai-card"><b>Typical roles:</b> {", ".join(DOMAINS[best]["roles"])}<br>'
                     f'<b>Core skills:</b> {", ".join(DOMAINS[best]["skills"])}</div>', unsafe_allow_html=True)
        st.caption("Head to 🗺️ AI Roadmap Generator to turn this into a study plan.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI ROADMAP GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "🗺️ AI Roadmap Generator":
    st.markdown("### 🗺️ AI Roadmap Generator")
    domain = st.selectbox("Target domain", list(DOMAINS.keys()),
                           index=list(DOMAINS.keys()).index(st.session_state.career_match_result) if st.session_state.career_match_result in DOMAINS else 0)
    level = st.selectbox("Current level", LEVELS)

    if st.button("Generate my 12-week roadmap"):
        skills = DOMAINS[domain]["skills"]
        weeks = []
        for i in range(12):
            skill = skills[i % len(skills)]
            weeks.append({
                "week": i + 1,
                "focus": skill,
                "task": f"Study core concepts of {skill} and complete 1 small hands-on exercise.",
            })
        st.session_state.roadmap = {"domain": domain, "level": level, "weeks": weeks}
        st.session_state.roadmap_progress = {}
        award("first_roadmap")
        st.rerun()

    if st.session_state.roadmap:
        rm = st.session_state.roadmap
        st.markdown(f"#### {rm['domain']} · {rm['level']}")
        done = sum(1 for w in rm["weeks"] if st.session_state.roadmap_progress.get(w["week"]))
        st.progress(done / len(rm["weeks"]))
        st.caption(f"{done}/{len(rm['weeks'])} weeks completed")
        for w in rm["weeks"]:
            checked = st.checkbox(f"Week {w['week']}: {w['focus']} — {w['task']}",
                                   value=st.session_state.roadmap_progress.get(w["week"], False),
                                   key=f"wk_{w['week']}")
            st.session_state.roadmap_progress[w["week"]] = checked

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI MENTOR CHAT
# ══════════════════════════════════════════════════════════════════════════
elif page == "💬 AI Mentor Chat":
    st.markdown("### 💬 AI Mentor Chat")
    if not AI_AVAILABLE:
        st.caption("Running in offline fallback mode — add a Gemini key in secrets for fully personalized replies.")

    for role, msg in st.session_state.chat_history:
        with st.chat_message(role):
            st.write(msg)

    user_msg = st.chat_input("Ask your mentor anything about your career path...")
    if user_msg:
        st.session_state.chat_history.append(("user", user_msg))
        fallback_tips = [
            "Break this into small weekly goals — consistency beats intensity.",
            "Build one real project around this and write about it — that's what recruiters remember.",
            "Try explaining this topic out loud to someone else; it reveals gaps fast.",
            "Look up 2-3 job postings for your target role and reverse-engineer the skills they list.",
        ]
        reply = ai_generate(
            f"You are a friendly, encouraging career mentor for a student. Answer briefly and practically: {user_msg}",
            random.choice(fallback_tips),
        )
        st.session_state.chat_history.append(("assistant", reply))
        award("chat_started")
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# PAGE: SKILL GAP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════
elif page == "📊 Skill Gap Analysis":
    st.markdown("### 📊 Skill Gap Analysis")
    domain = st.selectbox("Target domain", list(DOMAINS.keys()), key="sga_domain")
    have = st.multiselect("Skills you already have", DOMAINS[domain]["skills"])
    missing = [s for s in DOMAINS[domain]["skills"] if s not in have]
    st.progress(len(have) / max(len(DOMAINS[domain]["skills"]), 1))
    if missing:
        st.markdown("#### Gaps to close")
        for m in missing:
            st.markdown(f'<span class="ppai-tag">{m}</span>', unsafe_allow_html=True)
    else:
        st.success("You've covered every core skill for this domain — consider going deeper or exploring a specialization.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: STUDY PLANNER
# ══════════════════════════════════════════════════════════════════════════
elif page == "📅 Study Planner":
    st.markdown("### 📅 Study Planner")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    with st.form("study_plan_form"):
        day = st.selectbox("Day", days)
        topic = st.text_input("Topic")
        minutes = st.slider("Minutes", 15, 180, 60, step=15)
        add = st.form_submit_button("Add to plan")
    if add and topic:
        st.session_state.study_plan.append({"day": day, "topic": topic, "minutes": minutes})
        st.rerun()

    if st.session_state.study_plan:
        for d in days:
            items = [p for p in st.session_state.study_plan if p["day"] == d]
            if items:
                st.markdown(f"**{d}**")
                for it in items:
                    st.markdown(f'<div class="ppai-card">{it["topic"]} — {it["minutes"]} min</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: GOALS & PROGRESS
# ══════════════════════════════════════════════════════════════════════════
elif page == "🎯 Goals & Progress":
    st.markdown("### 🎯 Goals & Progress")
    with st.form("goal_form"):
        goal_text = st.text_input("New goal")
        due = st.date_input("Target date", value=date.today() + timedelta(days=7))
        add_goal = st.form_submit_button("Add goal")
    if add_goal and goal_text:
        st.session_state.goals.append({"text": goal_text, "due": str(due), "done": False})
        award("first_goal")
        st.rerun()

    for i, g in enumerate(st.session_state.goals):
        c1, c2 = st.columns([5, 1])
        done = c1.checkbox(f"{g['text']} (by {g['due']})", value=g["done"], key=f"goal_{i}")
        st.session_state.goals[i]["done"] = done
        if c2.button("🗑️", key=f"del_goal_{i}"):
            st.session_state.goals.pop(i)
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# PAGE: ACHIEVEMENTS
# ══════════════════════════════════════════════════════════════════════════
elif page == "🏆 Achievements":
    st.markdown("### 🏆 Achievements")
    st.metric("Total XP", st.session_state.xp)
    if st.session_state.badges:
        st.markdown("".join(f'<span class="ppai-badge">{b}</span>' for b in st.session_state.badges), unsafe_allow_html=True)
    else:
        st.info("No badges yet — use the app's features to start earning them.")
    st.markdown("#### All badges")
    for key, label in BADGE_DEFS.items():
        earned = label in st.session_state.badges
        st.markdown(f"{'✅' if earned else '⬜'} {label}")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: ATS RESUME BUILDER
# ══════════════════════════════════════════════════════════════════════════
elif page == "📄 ATS Resume Builder":
    st.markdown("### 📄 ATS Resume Builder")
    with st.form("resume_form"):
        name = st.text_input("Full name", value=st.session_state.user_name)
        target = st.text_input("Target role", value=st.session_state.user_target_role)
        summary = st.text_area("Professional summary", height=80)
        skills = st.text_area("Skills (comma-separated)")
        education = st.text_area("Education")
        experience = st.text_area("Experience / Projects", height=120)
        build = st.form_submit_button("Build resume")
    if build:
        st.session_state.resume_data = {
            "name": name, "target": target, "summary": summary,
            "skills": skills, "education": education, "experience": experience,
        }
        award("resume_built")
        st.rerun()

    if st.session_state.resume_data:
        d = st.session_state.resume_data
        resume_text = f"""{d['name']}
Target Role: {d['target']}

SUMMARY
{d['summary']}

SKILLS
{d['skills']}

EDUCATION
{d['education']}

EXPERIENCE / PROJECTS
{d['experience']}
"""
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.text(resume_text)
        st.markdown('</div>', unsafe_allow_html=True)
        st.download_button("⬇️ Download as .txt", resume_text, file_name="resume.txt")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: COVER LETTER GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "✉️ Cover Letter Generator":
    st.markdown("### ✉️ Cover Letter Generator")
    with st.form("cover_letter_form"):
        company = st.text_input("Company")
        role = st.text_input("Role", value=st.session_state.user_target_role)
        highlight = st.text_area("One thing you want to highlight about yourself")
        gen = st.form_submit_button("Generate cover letter")
    if gen:
        fallback = (
            f"Dear Hiring Manager,\n\nI'm excited to apply for the {role or '[Role]'} position at {company or '[Company]'}. "
            f"{highlight or 'I bring strong problem-solving skills and a genuine passion for this field.'} "
            f"I'd welcome the chance to bring that energy to your team.\n\nThank you for your consideration.\n\n"
            f"Sincerely,\n{st.session_state.user_name or '[Your Name]'}"
        )
        st.session_state.cover_letter = ai_generate(
            f"Write a concise, warm, ATS-friendly cover letter for a {role} role at {company}. "
            f"The applicant wants to highlight: {highlight}. Keep it under 200 words.",
            fallback,
        )
        st.rerun()

    if st.session_state.cover_letter:
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.text(st.session_state.cover_letter)
        st.markdown('</div>', unsafe_allow_html=True)
        st.download_button("⬇️ Download as .txt", st.session_state.cover_letter, file_name="cover_letter.txt")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PORTFOLIO GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "🌐 Portfolio Generator":
    st.markdown("### 🌐 Portfolio Generator")
    with st.form("portfolio_form"):
        headline = st.text_input("Headline", value=st.session_state.user_target_role)
        bio = st.text_area("Short bio")
        links = st.text_input("Links (comma-separated: GitHub, LinkedIn, etc.)")
        gen_p = st.form_submit_button("Generate portfolio page")
    if gen_p:
        st.session_state.portfolio_data = {"headline": headline, "bio": bio, "links": links}
        st.rerun()

    if st.session_state.portfolio_data:
        d = st.session_state.portfolio_data
        html = f"""<!DOCTYPE html><html><head><title>{st.session_state.user_name or 'My Portfolio'}</title></head>
<body style="font-family:sans-serif;max-width:700px;margin:40px auto;">
<h1>{st.session_state.user_name or 'Your Name'}</h1>
<h3>{d['headline']}</h3>
<p>{d['bio']}</p>
<p>{d['links']}</p>
</body></html>"""
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.markdown(f"**{d['headline']}**")
        st.write(d["bio"])
        st.caption(d["links"])
        st.markdown('</div>', unsafe_allow_html=True)
        st.download_button("⬇️ Download as .html", html, file_name="portfolio.html")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI PROJECT GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "💡 AI Project Generator":
    st.markdown("### 💡 AI Project Generator")
    domain = st.selectbox("Domain", list(DOMAINS.keys()), key="proj_domain")
    level = st.selectbox("Level", LEVELS, key="proj_level")
    if st.button("Generate project ideas"):
        fallback_ideas = {
            "Beginner": [f"Build a small {domain.lower()} tool that solves a problem you personally have.",
                         "Recreate a simple version of a tool you already use, focused on one core feature."],
            "Intermediate": [f"Build an end-to-end {domain.lower()} project with a public demo and a written case study.",
                              "Contribute a real feature to an open-source project in this domain."],
            "Advanced": [f"Design a {domain.lower()} system that handles a real constraint (scale, latency, or accuracy) and document trade-offs.",
                         "Publish a small research write-up comparing two approaches to a problem in this domain."],
        }
        ideas = ai_generate(
            f"Suggest 3 specific, portfolio-worthy project ideas for a {level} learner in {domain}. One line each.",
            "\n".join(f"- {i}" for i in fallback_ideas[level]),
        )
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.write(ideas)
        st.markdown('</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: MOCK INTERVIEW
# ══════════════════════════════════════════════════════════════════════════
elif page == "🎤 Mock Interview":
    st.markdown("### 🎤 Mock Interview")
    domain = st.selectbox("Interview domain", list(DOMAINS.keys()), key="mi_domain")
    question_bank = {
        "Data Science": ["Walk me through a data project you're proud of.", "How do you handle missing data?", "Explain overfitting in simple terms."],
        "Web Development": ["Explain how the browser renders a webpage.", "What's the difference between REST and GraphQL?", "How would you optimize a slow page load?"],
        "Cybersecurity": ["What's the difference between symmetric and asymmetric encryption?", "Walk me through how you'd respond to a phishing incident.", "What is the principle of least privilege?"],
        "AI / Machine Learning": ["Explain the bias-variance tradeoff.", "How would you evaluate a classification model?", "What's the difference between supervised and unsupervised learning?"],
        "Cloud / DevOps": ["What's the difference between a container and a VM?", "Explain a CI/CD pipeline you've built or used.", "How do you approach zero-downtime deployments?"],
        "Product Management": ["How do you prioritize a product backlog?", "Tell me about a time you used data to make a decision.", "How would you launch a new feature?"],
    }
    if st.button("Start / restart mock interview"):
        st.session_state.mock_interview_qs = random.sample(question_bank[domain], k=min(3, len(question_bank[domain])))
        st.session_state.mock_interview_idx = 0
        st.rerun()

    qs = st.session_state.mock_interview_qs
    idx = st.session_state.mock_interview_idx
    if qs and idx < len(qs):
        st.markdown(f'<div class="ppai-card"><b>Q{idx+1}.</b> {qs[idx]}</div>', unsafe_allow_html=True)
        answer = st.text_area("Your answer", key=f"mi_ans_{idx}")
        if st.button("Submit answer"):
            feedback = ai_generate(
                f"Give brief, constructive interview feedback (2-3 sentences) on this answer to '{qs[idx]}': {answer}",
                "Solid attempt — try adding a concrete example or number to make your answer more memorable.",
            )
            st.info(feedback)
            st.session_state.mock_interview_idx += 1
            st.session_state.mock_interview_score += 1
            st.rerun()
    elif qs:
        st.success(f"Mock interview complete — you answered {len(qs)} questions.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PLACEMENT READINESS SCORE
# ══════════════════════════════════════════════════════════════════════════
elif page == "📈 Placement Readiness Score":
    st.markdown("### 📈 Placement Readiness Score")
    checklist = {
        "Have a career match / target role": bool(st.session_state.career_match_result or st.session_state.user_target_role),
        "Have a roadmap in progress": bool(st.session_state.roadmap),
        "Have at least one goal set": len(st.session_state.goals) > 0,
        "Have a resume built": bool(st.session_state.resume_data),
        "Have a cover letter ready": bool(st.session_state.cover_letter),
        "Have practiced a mock interview": st.session_state.mock_interview_score > 0,
        "Have at least one project listed": len(st.session_state.projects) > 0,
    }
    score = sum(checklist.values())
    pct = int(score / len(checklist) * 100)
    st.metric("Readiness score", f"{pct}%")
    st.progress(pct / 100)
    for label, done in checklist.items():
        st.markdown(f"{'✅' if done else '⬜'} {label}")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: SALARY PREDICTOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "💰 Salary Predictor":
    st.markdown("### 💰 Salary Predictor")
    st.caption("A rough, static estimate for orientation only — not real market data.")
    domain = st.selectbox("Domain", list(DOMAINS.keys()), key="sal_domain")
    exp = st.slider("Years of experience", 0, 15, 1)
    location_tier = st.selectbox("Location tier", ["Tier-1 city", "Tier-2 city", "Remote / Global"])
    base = {"Data Science": 6, "Web Development": 5, "Cybersecurity": 6.5, "AI / Machine Learning": 7.5,
            "Cloud / DevOps": 6.5, "Product Management": 7}[domain]
    tier_mult = {"Tier-1 city": 1.2, "Tier-2 city": 1.0, "Remote / Global": 1.4}[location_tier]
    low = round(base * tier_mult * (1 + exp * 0.12), 1)
    high = round(low * 1.6, 1)
    st.metric("Estimated annual range (LPA, illustrative)", f"{low} – {high}")
    st.caption("This is a simplified illustrative formula, not a market survey — treat it as a rough starting point only.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: JOBS & INTERNSHIPS
# ══════════════════════════════════════════════════════════════════════════
elif page == "💼 Jobs & Internships":
    st.markdown("### 💼 Jobs & Internships")
    st.caption("Static sample listings — connect a job-board API for live data.")
    sample_jobs = [
        {"title": "Junior Data Analyst", "company": "Nimbus Analytics", "type": "Internship"},
        {"title": "Frontend Developer", "company": "Brightloop", "type": "Full-time"},
        {"title": "SOC Analyst Trainee", "company": "SecureNest", "type": "Internship"},
        {"title": "ML Engineer I", "company": "Vantage AI", "type": "Full-time"},
    ]
    for j in sample_jobs:
        st.markdown(f'<div class="ppai-card"><b>{j["title"]}</b> — {j["company"]} <span class="ppai-tag">{j["type"]}</span></div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: GITHUB & LEETCODE TRACKER
# ══════════════════════════════════════════════════════════════════════════
elif page == "🐙 GitHub & LeetCode Tracker":
    st.markdown("### 🐙 GitHub & LeetCode Tracker")
    gh_user = st.text_input("GitHub username", value=st.session_state.github_username)
    if st.button("Fetch GitHub profile") and gh_user:
        st.session_state.github_username = gh_user
        try:
            resp = requests.get(f"https://api.github.com/users/{gh_user}", timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                c1, c2, c3 = st.columns(3)
                c1.metric("Public repos", data.get("public_repos", 0))
                c2.metric("Followers", data.get("followers", 0))
                c3.metric("Following", data.get("following", 0))
                st.write(data.get("bio") or "")
            else:
                st.warning("GitHub user not found.")
        except Exception:
            st.warning("Couldn't reach GitHub right now — try again shortly.")

    st.divider()
    st.session_state.leetcode_solved = st.number_input("LeetCode problems solved (manual entry)", min_value=0, value=st.session_state.leetcode_solved)
    st.caption("LeetCode has no public API, so this is tracked manually.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PROJECT SHOWCASE
# ══════════════════════════════════════════════════════════════════════════
elif page == "🖼️ Project Showcase":
    st.markdown("### 🖼️ Project Showcase")
    with st.form("project_form"):
        title = st.text_input("Project title")
        desc = st.text_area("Description")
        link = st.text_input("Link (GitHub/demo, optional)")
        add_proj = st.form_submit_button("Add project")
    if add_proj and title:
        st.session_state.projects.append({"title": title, "desc": desc, "link": link})
        st.rerun()

    for p in st.session_state.projects:
        link_html = f'<a href="{p["link"]}" target="_blank">{p["link"]}</a>' if p["link"] else ""
        st.markdown(
            f'<div class="ppai-card"><b>{p["title"]}</b><br>{p["desc"]}<br>{link_html}</div>',
            unsafe_allow_html=True,
        )

# ══════════════════════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ══════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Settings":
    st.markdown("### ⚙️ Profile & Settings")
    if st.session_state.logged_in:
        st.caption(f"Logged in as **{st.session_state.username}** — profile changes are saved to your account.")
    else:
        st.caption("You're browsing as a guest, so nothing here is saved between visits. Create an account below to keep your data.")

    with st.form("profile_form"):
        name = st.text_input("Name", value=st.session_state.user_name)
        email = st.text_input("Email", value=st.session_state.user_email)
        target_role = st.text_input("Target role", value=st.session_state.user_target_role)
        save = st.form_submit_button("Save profile")
    if save:
        st.session_state.user_name = name
        st.session_state.user_email = email
        st.session_state.user_target_role = target_role
        if st.session_state.logged_in:
            sync_profile_to_db(st.session_state.username, name, email, target_role)
            st.success("Profile saved to your account.")
        else:
            st.success("Profile saved for this guest session.")

    if st.session_state.guest_mode and not st.session_state.logged_in:
        st.divider()
        st.markdown("#### 🆕 Create an account to save your progress")
        with st.form("upgrade_signup_form"):
            up_username = st.text_input("Choose a username", key="up_username")
            up_pw = st.text_input("Choose a password", type="password", key="up_pw")
            up_pw2 = st.text_input("Confirm password", type="password", key="up_pw2")
            do_upgrade = st.form_submit_button("Create account")
        if do_upgrade:
            if up_pw != up_pw2:
                st.error("Passwords don't match.")
            else:
                ok, msg = create_account(up_username, st.session_state.user_email, st.session_state.user_name, up_pw)
                if ok:
                    st.session_state.logged_in = True
                    st.session_state.guest_mode = False
                    st.session_state.username = up_username.strip().lower()
                    sync_profile_to_db(st.session_state.username, st.session_state.user_name, st.session_state.user_email, st.session_state.user_target_role)
                    st.success("Account created — your current data is now saved.")
                    st.rerun()
                else:
                    st.error(msg)
