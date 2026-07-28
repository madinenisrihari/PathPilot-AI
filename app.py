"""
PathPilot AI — Your Career Companion
A Streamlit app for career planning, prep, and progress tracking.

NOTE ON SCOPE (read this before extending):
This is a single-file demo app. Session state stands in for a real database,
so a user's data resets when their browser session ends / the app restarts.
Features that would need a real backend in production are marked "(Demo)"
in the UI and explained inline:
  - Login/Signup: no password hashing or persistent accounts (add a DB + auth
    provider like Supabase/Firebase/Auth0 for real accounts).
  - Jobs/Internships: static sample listings (swap in a job-board API).
  - LeetCode tracker: manual entry (LeetCode has no public official API).
  - Admin/Analytics dashboards: shows only the CURRENT session's demo data.
  - Premium: a toggle, not a real payment flow (add Stripe/Razorpay for that).
  - AI Mentor Chat / Mock Interview: use Gemini if a GEMINI_API_KEY is set in
    st.secrets, otherwise fall back to curated, rule-based responses so the
    app still works with zero API keys.
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

# ── Optional Gemini (AI Mentor / Mock Interview) ─────────────────────────────
try:
    import google.generativeai as genai
    GENAI_IMPORTED = True
except ImportError:
    GENAI_IMPORTED = False


def get_gemini_model():
    """Return a configured Gemini model, or None if no key is set."""
    if not GENAI_IMPORTED:
        return None
    try:
        api_key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        api_key = None
    if not api_key:
        return None
    try:
        genai.configure(api_key=api_key)
        return genai.GenerativeModel("gemini-1.5-flash")
    except Exception:
        return None


AI_AVAILABLE = get_gemini_model() is not None

# ══════════════════════════════════════════════════════════════════════════
# AUTH — real accounts backed by SQLite (passwords are salted + hashed,
# never stored in plain text). NOTE: on free Streamlit Community Cloud
# hosting, this .db file lives on the app's local disk, so it persists
# across normal usage/sleep but is wiped on a fresh redeploy/reboot. For
# accounts that survive redeploys, swap this for a hosted DB (e.g. Supabase).
# ══════════════════════════════════════════════════════════════════════════
def _resolve_db_path():
    """Find a location we can actually write to. App folders on some hosts
    (like Streamlit Community Cloud) are read-only, so fall back to the
    system temp dir rather than crashing the whole app."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "pathpilot_users.db"),
        os.path.join(tempfile.gettempdir(), "pathpilot_users.db"),
    ]
    for path in candidates:
        try:
            test_conn = sqlite3.connect(path, check_same_thread=False)
            test_conn.execute(
                "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, email TEXT, "
                "name TEXT, target_role TEXT, password_hash TEXT NOT NULL, salt TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            test_conn.commit()
            test_conn.close()
            return path
        except Exception:
            continue
    return candidates[-1]


DB_PATH = _resolve_db_path()
DB_READY = True


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            target_role TEXT,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return pw_hash.hex(), salt.hex()


def create_account(username: str, email: str, name: str, password: str):
    username = username.strip().lower()
    if not username or not password:
        return False, "Username and password are required."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    try:
        conn = get_db_connection()
    except Exception as e:
        return False, f"Couldn't reach the account database right now ({e}). Try 'Continue as guest' instead."
    try:
        pw_hash, salt_hex = hash_password(password)
        conn.execute(
            "INSERT INTO users (username, email, name, target_role, password_hash, salt, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, email.strip(), name.strip(), "", pw_hash, salt_hex, datetime.now().isoformat()),
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
    username = username.strip().lower()
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
    check_hash, _ = hash_password(password, bytes.fromhex(row[5]))
    if check_hash == row[4]:
        return {"username": row[0], "email": row[1], "name": row[2], "target_role": row[3]}
    return None


def sync_profile_to_db(username: str, name: str, email: str, target_role: str):
    if not username:
        return
    try:
        conn = get_db_connection()
        conn.execute(
            "UPDATE users SET name = ?, email = ?, target_role = ? WHERE username = ?",
            (name, email, target_role, username),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="PathPilot AI — Your Career Companion",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="auto",
)

# ══════════════════════════════════════════════════════════════════════════
# SESSION STATE DEFAULTS
# ══════════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "theme": "Light",
    "language": "English",
    "is_premium": False,
    "user_name": "",
    "user_email": "",
    "user_target_role": "",
    "logged_in": False,
    "username": "",
    "guest_mode": False,
    "auth_error": "",
    "xp": 0,
    "badges": [],
    "streak_count": 0,
    "last_active_date": None,
    "roadmap": None,
    "roadmap_domain_key": None,
    "quiz_result": None,
    "current_skills": [],
    "goals": [],
    "planner_tasks": [],
    "resume_data": {},
    "cover_letter_text": "",
    "projects": [],
    "applications": [],
    "certificates": [],
    "bookmarks": [],
    "chat_history": [],
    "mock_interview_log": [],
    "github_username": "",
    "leetcode_stats": {"easy": 0, "medium": 0, "hard": 0},
    "notifications": [],
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val


def add_xp(amount: int, reason: str = ""):
    st.session_state.xp += amount
    if reason:
        st.session_state.notifications.insert(0, f"+{amount} XP — {reason}")
        st.session_state.notifications = st.session_state.notifications[:15]


def update_streak():
    today = date.today().isoformat()
    last = st.session_state.last_active_date
    if last == today:
        return
    if last == (date.today() - timedelta(days=1)).isoformat():
        st.session_state.streak_count += 1
    else:
        st.session_state.streak_count = 1
    st.session_state.last_active_date = today


update_streak()

BADGE_DEFS = {
    "first_roadmap": ("🗺️", "Roadmap Ready", "Generated your first learning roadmap"),
    "quiz_taker": ("🧭", "Self-Aware", "Completed the Career Match Test"),
    "planner_pro": ("📅", "Planner Pro", "Added 5+ tasks to your study planner"),
    "resume_built": ("📄", "Resume Ready", "Built your ATS resume"),
    "cover_letter": ("✉️", "Well Said", "Generated a cover letter"),
    "interview_ready": ("🎤", "Interview Ready", "Completed a mock interview"),
    "streak_3": ("🔥", "3-Day Streak", "Active 3 days in a row"),
    "streak_7": ("🔥🔥", "7-Day Streak", "Active 7 days in a row"),
    "goal_setter": ("🎯", "Goal Setter", "Set your first goal"),
    "project_builder": ("🛠️", "Project Builder", "Added a project to your showcase"),
}


def award_badge(key: str):
    if key not in st.session_state.badges:
        st.session_state.badges.append(key)
        emoji, title, _ = BADGE_DEFS[key]
        st.toast(f"Badge unlocked: {emoji} {title}", icon="🏆")


if st.session_state.streak_count >= 3:
    award_badge("streak_3")
if st.session_state.streak_count >= 7:
    award_badge("streak_7")

# ══════════════════════════════════════════════════════════════════════════
# THEME / CSS
# ══════════════════════════════════════════════════════════════════════════
if st.session_state.theme == "Dark":
    BG_A, BG_B, BG_C = "#0f0c29", "#1a1440", "#12102a"
    CARD, CARD_SOLID = "rgba(30,25,66,0.66)", "#1a1533"
    TEXT, SUBTEXT, BORDER = "#f1f0ff", "#aea9d6", "rgba(139,92,246,0.30)"
    BLOB1, BLOB2 = "rgba(124,58,237,0.35)", "rgba(6,182,212,0.22)"
    INPUT_BG = "rgba(255,255,255,0.05)"
else:
    BG_A, BG_B, BG_C = "#eef2ff", "#f3e8ff", "#e0f7fa"
    CARD, CARD_SOLID = "rgba(255,255,255,0.78)", "#ffffff"
    TEXT, SUBTEXT, BORDER = "#1e1b3a", "#5b5b7a", "rgba(124,58,237,0.16)"
    BLOB1, BLOB2 = "rgba(124,58,237,0.18)", "rgba(6,182,212,0.18)"
    INPUT_BG = "rgba(255,255,255,0.9)"

ACCENT1, ACCENT2, ACCENT3 = "#7c3aed", "#06b6d4", "#ec4899"
EASE = "cubic-bezier(.4,0,.2,1)"

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}

    * {{ transition: background-color .16s {EASE}, border-color .16s {EASE}, box-shadow .18s {EASE}, transform .14s {EASE}, opacity .16s {EASE}, color .16s {EASE}; }}

    .stApp {{
        background: linear-gradient(120deg, {BG_A} 0%, {BG_B} 45%, {BG_C} 100%);
        background-size: 220% 220%;
        background-attachment: fixed;
        animation: ppaiBgDrift 18s ease-in-out infinite;
        position: relative;
    }}
    @keyframes ppaiBgDrift {{
        0% {{ background-position: 0% 30%; }}
        50% {{ background-position: 100% 70%; }}
        100% {{ background-position: 0% 30%; }}
    }}
    .stApp::before, .stApp::after {{
        content: ""; position: fixed; border-radius: 50%; filter: blur(70px);
        z-index: 0; pointer-events: none; animation: ppaiBlobFloat 12s ease-in-out infinite;
    }}
    .stApp::before {{ width: 420px; height: 420px; top: -120px; right: -100px; background: {BLOB1}; }}
    .stApp::after {{ width: 380px; height: 380px; bottom: -140px; left: -100px; background: {BLOB2}; animation-delay: -6s; }}
    @keyframes ppaiBlobFloat {{
        0%, 100% {{ transform: translate(0,0) scale(1); }}
        50% {{ transform: translate(-24px,18px) scale(1.06); }}
    }}
    .main .block-container {{ position: relative; z-index: 1; animation: ppaiFadeIn .35s {EASE}; }}

    @keyframes ppaiFadeIn {{
        from {{ opacity: 0; transform: translateY(10px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}

    section[data-testid="stSidebar"] {{ background: {CARD_SOLID}; border-right: 1px solid {BORDER}; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        border-radius: 10px; padding: 0.4rem 0.55rem; margin-bottom: 2px;
        transition: background .16s {EASE}, transform .14s {EASE} !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
        background: linear-gradient(90deg, rgba(124,58,237,0.14), rgba(6,182,212,0.12));
        transform: translateX(4px);
    }}

    .ppai-header {{ text-align:center; padding: 1rem 0 0.5rem; }}
    .ppai-header h1 {{
        font-size: clamp(1.6rem, 5vw, 2.6rem); font-weight: 800;
        background: linear-gradient(120deg, {ACCENT1} 0%, {ACCENT2} 50%, {ACCENT3} 100%);
        background-size: 200% auto;
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        margin-bottom: 0.25rem; animation: ppaiGradientShift 6s ease infinite;
    }}
    @keyframes ppaiGradientShift {{
        0% {{ background-position: 0% center; }}
        50% {{ background-position: 100% center; }}
        100% {{ background-position: 0% center; }}
    }}
    .ppai-header p {{ color: {SUBTEXT}; font-size: 1rem; }}

    .ppai-card {{
        background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px;
        padding: 1.1rem 1.3rem; margin-bottom: 0.9rem;
        box-shadow: 0 2px 10px rgba(31,23,74,0.06); color: {TEXT};
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        animation: ppaiFadeIn .3s {EASE};
    }}
    .ppai-card:hover {{ box-shadow: 0 14px 30px rgba(124,58,237,0.20); transform: translateY(-3px); }}
    .ppai-auth-card {{ max-width: 460px; margin: 0 auto; animation: ppaiPopIn .35s {EASE}; }}
    @keyframes ppaiPopIn {{
        from {{ opacity: 0; transform: translateY(14px) scale(.98); }}
        to {{ opacity: 1; transform: translateY(0) scale(1); }}
    }}
    .ppai-badge {{
        display:inline-block; background: linear-gradient(135deg,{ACCENT1},{ACCENT3});
        color:white; font-weight:600; font-size:0.75rem; padding:0.2rem 0.7rem;
        border-radius:20px; margin-bottom:0.4rem;
    }}
    .ppai-tag {{
        display:inline-block; background: transparent; border:1px solid {BORDER}; color:{SUBTEXT};
        font-size:0.72rem; padding:0.15rem 0.55rem; border-radius:12px; margin:2px;
    }}
    .ppai-premium {{
        display:inline-block; background: linear-gradient(135deg,#f59e0b,#f97316);
        color:white; font-size:0.68rem; font-weight:700; padding:0.15rem 0.5rem;
        border-radius:10px; margin-left:0.4rem; vertical-align:middle;
    }}
    .ppai-topic {{ font-size:1.05rem; font-weight:700; color:{TEXT}; margin-bottom:0.5rem; }}
    .ppai-sub {{ color:{SUBTEXT}; font-size:0.88rem; }}

    .stButton > button {{
        background: linear-gradient(135deg, {ACCENT1}, {ACCENT2}) !important;
        background-size: 160% auto !important;
        color: white !important; border: none !important; border-radius: 10px !important;
        padding: 0.55rem 1.4rem !important; font-weight: 600 !important;
        width: 100%; box-shadow: 0 2px 8px rgba(124,58,237,0.25);
        transition: transform .12s {EASE}, box-shadow .16s {EASE}, background-position .3s {EASE} !important;
    }}
    .stButton > button:hover {{
        transform: translateY(-2px); box-shadow: 0 10px 22px rgba(124,58,237,0.38) !important;
        background-position: right center !important;
    }}
    .stButton > button:active {{ transform: translateY(0px) scale(0.97); transition-duration: .05s !important; }}
    .stButton > button:focus-visible {{ outline: 2px solid {ACCENT3} !important; outline-offset: 2px; }}

    div[data-testid="stMetric"] {{
        background:{CARD}; border:1px solid {BORDER}; border-radius:14px; padding:0.8rem;
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    }}
    div[data-testid="stMetric"]:hover {{ transform: translateY(-2px); box-shadow: 0 8px 18px rgba(124,58,237,0.14); }}
    div[data-testid="stMetricLabel"], div[data-testid="stMetricValue"] {{ color: {TEXT} !important; }}
    div[data-testid="stForm"] {{
        background:{CARD}; border:1px solid {BORDER}; border-radius:16px; padding:1.3rem;
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    }}
    div.stTextInput input, div.stTextArea textarea, div.stNumberInput input,
    div.stSelectbox div[data-baseweb="select"] > div {{
        border-radius: 9px !important; background: {INPUT_BG} !important;
        transition: border-color .14s {EASE}, box-shadow .14s {EASE} !important;
    }}
    div.stTextInput input:hover, div.stTextArea textarea:hover {{
        border-color: {ACCENT2} !important;
    }}
    div.stTextInput input:focus, div.stTextArea textarea:focus {{
        border-color: {ACCENT1} !important; box-shadow: 0 0 0 3px rgba(124,58,237,0.16) !important;
        transform: translateY(-1px);
    }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 8px 8px 0 0; transition: color .14s {EASE}, background .14s {EASE} !important;
    }}
    .stTabs [data-baseweb="tab"]:hover {{ background: rgba(124,58,237,0.08); }}
    .stTabs [aria-selected="true"] {{ color: {ACCENT1} !important; font-weight: 700; }}
    .stTabs [data-baseweb="tab-highlight"] {{
        background: linear-gradient(90deg,{ACCENT1},{ACCENT2}) !important; transition: left .22s {EASE} !important;
    }}
    ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
    ::-webkit-scrollbar-thumb {{ background: linear-gradient(180deg,{ACCENT1},{ACCENT2}); border-radius: 10px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}

    @media (max-width: 640px) {{
        .ppai-header h1 {{ font-size: 1.6rem; }}
        .ppai-card {{ padding: 0.9rem 1rem; }}
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════════════════
# CAREER DOMAIN DATA
# ══════════════════════════════════════════════════════════════════════════
CAREER_DOMAINS = {
    "data science": {
        "topics": [
            "Python Fundamentals & Jupyter Setup", "NumPy & Pandas for Data Manipulation",
            "Data Cleaning & Exploratory Data Analysis", "Data Visualization with Matplotlib & Seaborn",
            "Statistics & Probability Foundations", "Machine Learning Basics — Supervised Learning",
            "Regression & Classification Models", "Unsupervised Learning & Clustering",
            "Feature Engineering & Model Evaluation", "Introduction to Deep Learning with TensorFlow",
            "Natural Language Processing Basics", "Capstone: End-to-End Data Science Project",
        ],
        "resources": [
            ["Kaggle Learn — Python", "https://www.kaggle.com/learn/python"],
            ["freeCodeCamp — Scientific Computing with Python", "https://www.freecodecamp.org/learn/scientific-computing-with-python/"],
            ["Kaggle Learn — Pandas", "https://www.kaggle.com/learn/pandas"],
            ["Kaggle Learn — Data Visualization", "https://www.kaggle.com/learn/data-visualization"],
            ["StatQuest YouTube Channel", "https://www.youtube.com/c/joshstarmer"],
            ["Google ML Crash Course", "https://developers.google.com/machine-learning/crash-course"],
            ["scikit-learn User Guide", "https://scikit-learn.org/stable/user_guide.html"],
            ["fast.ai — Practical Deep Learning", "https://course.fast.ai/"],
            ["TensorFlow Tutorials", "https://www.tensorflow.org/tutorials"],
            ["Hugging Face NLP Course", "https://huggingface.co/learn/nlp-course/chapter1/1"],
            ["fast.ai NLP", "https://course.fast.ai/"],
            ["Kaggle Competitions", "https://www.kaggle.com/competitions"],
        ],
        "projects": [
            "Analyze a CSV dataset and write a summary report", "Build a data cleaning pipeline for messy data",
            "Create an interactive EDA dashboard with Plotly", "Visualize trends with Matplotlib & Seaborn",
            "Run A/B test analysis on sample e-commerce data", "Predict house prices with linear regression",
            "Build a spam email classifier with scikit-learn", "Customer segmentation using K-Means",
            "Feature engineering challenge on the Titanic dataset", "Train a neural network on MNIST digits",
            "Sentiment analysis on movie reviews", "Full portfolio project: predict churn for a SaaS company",
        ],
        "skills": ["Python", "Pandas & NumPy", "Data Visualization", "Statistics", "SQL",
                   "Machine Learning", "Deep Learning Basics", "Data Storytelling", "A/B Testing", "MLOps Basics"],
    },
    "web development": {
        "topics": [
            "HTML5 & Semantic Web Structure", "CSS3, Flexbox & Responsive Design",
            "JavaScript Fundamentals & DOM Manipulation", "Git, GitHub & Developer Workflow",
            "Advanced JavaScript — ES6+ & Async/Await", "React.js — Components, Props & State",
            "React Hooks, Routing & State Management", "Node.js & Express — Building REST APIs",
            "Databases — SQL Basics & MongoDB", "Authentication, Security & Deployment",
            "Full-Stack Integration & Testing", "Capstone: Deploy a Full-Stack Web Application",
        ],
        "resources": [
            ["MDN Web Docs — HTML", "https://developer.mozilla.org/en-US/docs/Web/HTML"],
            ["freeCodeCamp — Responsive Web Design", "https://www.freecodecamp.org/learn/2022/responsive-web-design/"],
            ["JavaScript.info", "https://javascript.info/"],
            ["GitHub Skills", "https://skills.github.com/"],
            ["freeCodeCamp — JS Algorithms", "https://www.freecodecamp.org/learn/javascript-algorithms-and-data-structures/"],
            ["React Official Docs", "https://react.dev/learn"],
            ["React Router Docs", "https://reactrouter.com/en/main"],
            ["Node.js Getting Started", "https://nodejs.org/en/learn/getting-started/introduction-to-nodejs"],
            ["SQLBolt — Interactive SQL Tutorial", "https://sqlbolt.com/"],
            ["OWASP Web Security Basics", "https://owasp.org/www-project-web-security-testing-guide/"],
            ["Jest Testing Framework Docs", "https://jestjs.io/docs/getting-started"],
            ["Vercel Deployment Guide", "https://vercel.com/docs"],
        ],
        "projects": [
            "Build a personal portfolio landing page", "Create a responsive photo gallery with CSS Grid",
            "Todo app with local storage persistence", "Contribute your first pull request on GitHub",
            "Weather app using a free public API", "Component library with reusable React components",
            "Blog app with React Router and markdown support", "REST API for a book library with Express",
            "CRUD app connected to a SQLite database", "Add JWT authentication to your API",
            "Write unit tests for your React components", "Deploy a full-stack MERN app to the cloud",
        ],
        "skills": ["HTML/CSS", "JavaScript", "Git & GitHub", "React", "Node.js/Express",
                   "REST APIs", "SQL/NoSQL Databases", "Auth & Security", "Testing", "Deployment"],
    },
    "machine learning": {
        "topics": [
            "Python & Math Refresher for ML", "Linear Algebra & Calculus Essentials",
            "Data Preprocessing & Feature Scaling", "Supervised Learning — Linear Models",
            "Decision Trees & Ensemble Methods", "Model Selection & Cross-Validation",
            "Unsupervised Learning — PCA & Clustering", "Neural Networks from Scratch",
            "Deep Learning with PyTorch", "Computer Vision Fundamentals",
            "MLOps — Model Deployment & Monitoring", "Capstone: Production-Ready ML Pipeline",
        ],
        "resources": [
            ["3Blue1Brown — Linear Algebra", "https://www.youtube.com/playlist?list=PLZHQObOWTQDPD3MizzM2xVFitgF8hE_ab"],
            ["Khan Academy — Calculus", "https://www.khanacademy.org/math/calculus-1"],
            ["scikit-learn Preprocessing Guide", "https://scikit-learn.org/stable/modules/preprocessing.html"],
            ["Andrew Ng — ML Specialization (audit free)", "https://www.coursera.org/specializations/machine-learning-introduction"],
            ["StatQuest — Random Forests", "https://www.youtube.com/watch?v=J4Wdy0nx_c8"],
            ["scikit-learn Model Selection", "https://scikit-learn.org/stable/model_selection.html"],
            ["Google ML Clustering Course", "https://developers.google.com/machine-learning/clustering"],
            ["Neural Networks and Deep Learning (book)", "http://neuralnetworksanddeeplearning.com/"],
            ["PyTorch Official Tutorials", "https://pytorch.org/tutorials/"],
            ["fast.ai — Practical Deep Learning", "https://course.fast.ai/"],
            ["Made With ML — MLOps Guide", "https://madewithml.com/"],
            ["Hugging Face Model Hub", "https://huggingface.co/models"],
        ],
        "projects": [
            "Implement gradient descent from scratch in NumPy", "Predict student grades with linear regression",
            "Build a random forest classifier for iris flowers", "Hyperparameter tuning with GridSearchCV",
            "Customer segmentation with K-Means on retail data", "Build a 2-layer neural network without frameworks",
            "Image classifier on CIFAR-10 with PyTorch", "Object detection demo with a pre-trained model",
            "Deploy a model as a FastAPI microservice", "Build an end-to-end ML pipeline with MLflow",
            "Create a model monitoring dashboard", "Capstone: train, deploy, and monitor a real-world model",
        ],
        "skills": ["Python", "Linear Algebra & Calculus", "Statistics", "scikit-learn",
                   "Neural Networks", "PyTorch/TensorFlow", "Model Evaluation", "MLOps", "CV or NLP", "Research Reading"],
    },
    "cybersecurity": {
        "topics": [
            "Networking Fundamentals & OSI Model", "Linux Command Line & Shell Scripting",
            "Cryptography Basics & Hashing", "Web Application Security & OWASP Top 10",
            "Network Scanning & Reconnaissance", "Vulnerability Assessment & Penetration Testing",
            "Security Operations & SIEM Tools", "Cloud Security Fundamentals",
            "Incident Response & Forensics", "Identity & Access Management",
            "Security Automation & Scripting", "Capstone: Security Audit & Report",
        ],
        "resources": [
            ["Professor Messer — Network+", "https://www.professormesser.com/network-plus/n10-008/n10-008-video-playlist/"],
            ["OverTheWire — Bandit Linux Wargame", "https://overthewire.org/wargames/bandit/"],
            ["Crypto101 — Free Book", "https://www.crypto101.io/"],
            ["OWASP WebGoat", "https://owasp.org/www-project-webgoat/"],
            ["TryHackMe — Free Rooms", "https://tryhackme.com/"],
            ["HackTheBox — Starting Point", "https://www.hackthebox.com/"],
            ["Splunk Boss of the SOC", "https://www.splunk.com/en_us/blog/security/boss-of-the-soc.html"],
            ["AWS Cloud Security Fundamentals", "https://aws.amazon.com/training/digital/aws-cloud-security-fundamentals/"],
            ["SANS Digital Forensics Poster", "https://www.sans.org/posters/digital-forensics-and-incident-response/"],
            ["IAM Best Practices — AWS", "https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html"],
            ["Automate the Boring Stuff — Python", "https://automatetheboringstuff.com/"],
            ["NIST Cybersecurity Framework", "https://www.nist.gov/cyberframework"],
        ],
        "projects": [
            "Set up a home lab with VirtualBox & Kali Linux", "Write a Bash script to audit open ports",
            "Implement Caesar cipher & SHA-256 hasher in Python", "Find and fix XSS vulnerabilities in WebGoat",
            "Perform a network scan report with Nmap", "Complete 5 TryHackMe beginner rooms",
            "Build a simple SIEM alert dashboard", "Configure AWS IAM roles with least privilege",
            "Analyze a PCAP file for suspicious traffic", "Design an RBAC policy for a small company",
            "Automate vulnerability scanning with Python", "Write a full security audit report for a web app",
        ],
        "skills": ["Networking", "Linux", "Cryptography Basics", "Web App Security (OWASP)",
                   "Penetration Testing", "SIEM Tools", "Cloud Security", "Incident Response", "IAM", "Scripting/Automation"],
    },
    "cloud computing": {
        "topics": [
            "Cloud Concepts & Service Models (IaaS/PaaS/SaaS)", "AWS Core Services — EC2, S3, IAM",
            "Networking — VPC, Subnets & Security Groups", "Databases in the Cloud — RDS & DynamoDB",
            "Containers & Docker Fundamentals", "Kubernetes Basics & Orchestration",
            "Serverless Computing with AWS Lambda", "Infrastructure as Code — Terraform",
            "CI/CD Pipelines & DevOps Practices", "Cloud Monitoring, Logging & Cost Optimization",
            "Multi-Cloud & Hybrid Cloud Strategies", "Capstone: Deploy a Scalable Cloud Architecture",
        ],
        "resources": [
            ["AWS Cloud Practitioner Essentials (free)", "https://explore.skillbuilder.aws/learn/course/external/view/elearning/134/aws-cloud-practitioner-essentials"],
            ["AWS EC2 Getting Started", "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html"],
            ["AWS VPC Documentation", "https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html"],
            ["AWS RDS User Guide", "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Welcome.html"],
            ["Docker Getting Started", "https://docs.docker.com/get-started/"],
            ["Kubernetes Basics — Interactive Tutorial", "https://kubernetes.io/docs/tutorials/kubernetes-basics/"],
            ["AWS Lambda Developer Guide", "https://docs.aws.amazon.com/lambda/latest/dg/welcome.html"],
            ["Terraform Learn — AWS", "https://developer.hashicorp.com/terraform/tutorials/aws-get-started"],
            ["GitHub Actions Docs", "https://docs.github.com/en/actions"],
            ["AWS CloudWatch Docs", "https://docs.aws.amazon.com/cloudwatch/"],
            ["Google Cloud Skills Boost — Free Tier", "https://www.cloudskillsboost.google/"],
            ["AWS Well-Architected Framework", "https://aws.amazon.com/architecture/well-architected/"],
        ],
        "projects": [
            "Launch and secure your first EC2 instance", "Build a static website hosted on S3",
            "Design a VPC with public & private subnets", "Deploy a PostgreSQL database on RDS",
            "Containerize a Python app with Docker", "Deploy a microservice to a Kubernetes cluster",
            "Build a serverless REST API with API Gateway + Lambda", "Provision AWS infrastructure with Terraform",
            "Set up a CI/CD pipeline with GitHub Actions", "Create CloudWatch alarms and a cost budget",
            "Compare AWS vs Azure pricing for a sample workload", "Design and document a 3-tier cloud architecture",
        ],
        "skills": ["Cloud Fundamentals", "AWS/Azure/GCP Core Services", "Networking (VPC)", "Databases in Cloud",
                   "Docker", "Kubernetes", "Serverless", "Terraform/IaC", "CI/CD", "Monitoring & Cost Optimization"],
    },
}

DEFAULT_DOMAIN = {
    "topics": [
        "Goal Setting & Learning Mindset", "Core Concepts & Terminology", "Essential Tools & Environment Setup",
        "Foundational Skills — Part 1", "Foundational Skills — Part 2", "Intermediate Concepts & Patterns",
        "Hands-On Practice & Problem Solving", "Advanced Techniques & Best Practices",
        "Industry Standards & Frameworks", "Soft Skills & Professional Development",
        "Portfolio Building & Networking", "Capstone Project & Career Next Steps",
    ],
    "resources": [
        ["Coursera — Free Audit Courses", "https://www.coursera.org/"],
        ["edX — Free Courses", "https://www.edx.org/"],
        ["YouTube — freeCodeCamp", "https://www.youtube.com/c/Freecodecamp"],
        ["Khan Academy", "https://www.khanacademy.org/"],
        ["MIT OpenCourseWare", "https://ocw.mit.edu/"],
        ["Udemy Free Courses", "https://www.udemy.com/courses/free/"],
        ["LinkedIn Learning", "https://www.linkedin.com/learning/"],
        ["GitHub Learning Lab", "https://lab.github.com/"],
        ["Medium — Free Articles", "https://medium.com/"],
        ["Reddit Learning Communities", "https://www.reddit.com/r/learnprogramming/"],
        ["Dev.to — Developer Blog", "https://dev.to/"],
        ["Portfolio Inspiration — Behance", "https://www.behance.net/"],
    ],
    "projects": [
        "Write a personal learning manifesto and 12-week plan", "Create flashcards for 50 key terms in your field",
        "Set up your development/study environment", "Complete 10 practice exercises on core concepts",
        "Build a cheat sheet document for quick reference", "Solve 5 real-world scenario problems",
        "Complete a 48-hour mini hackathon project", "Write a technical blog post explaining a concept",
        "Contribute to an open-source project (good first issue)", "Mock interview practice with a peer or AI",
        "Build a portfolio website showcasing your work", "Present your capstone project in a demo video",
    ],
    "skills": ["Communication", "Problem Solving", "Core Fundamentals", "Tools & Setup",
               "Portfolio Building", "Professional Networking", "Resume & Interview Skills",
               "Project Management Basics", "Time Management", "Continuous Learning"],
}

SKILL_MODIFIERS = {
    "Beginner": {"prefix": "Foundations: ", "hours_factor": 1.0, "note": "Take your time — focus on understanding core concepts before moving on."},
    "Intermediate": {"prefix": "", "hours_factor": 0.85, "note": "Build on your existing knowledge and push into practical applications."},
    "Advanced": {"prefix": "Advanced: ", "hours_factor": 0.7, "note": "Challenge yourself with complex projects and industry-level problems."},
}

DOMAIN_LABELS = {
    "data science": "Data Science", "web development": "Web Development", "machine learning": "Machine Learning",
    "cybersecurity": "Cybersecurity", "cloud computing": "Cloud Computing", "general": "General / Other",
}


def detect_domain_key(career_goal: str) -> str:
    goal_lower = career_goal.lower()
    for keyword in CAREER_DOMAINS:
        if keyword in goal_lower:
            return keyword
    for keyword in CAREER_DOMAINS:
        if any(word in goal_lower for word in keyword.split()):
            return keyword
    return "general"


def get_domain(key: str) -> dict:
    return CAREER_DOMAINS.get(key, DEFAULT_DOMAIN)


def generate_roadmap(career_goal: str, skill_level: str, hours_per_day: float) -> list:
    domain_key = detect_domain_key(career_goal)
    domain = get_domain(domain_key)
    modifier = SKILL_MODIFIERS[skill_level]
    weekly_hours = round(hours_per_day * 7 * modifier["hours_factor"], 1)
    weeks = []
    for i in range(12):
        topic = domain["topics"][i]
        if skill_level == "Beginner" and i < 4:
            topic = modifier["prefix"] + topic
        elif skill_level == "Advanced" and i >= 8:
            topic = modifier["prefix"] + topic
        resource_name, resource_url = domain["resources"][i]
        weeks.append({
            "week": i + 1, "topic": topic, "resource_name": resource_name,
            "resource_url": resource_url, "project": domain["projects"][i],
            "hours": weekly_hours, "done": False,
        })
    return weeks, domain_key


# ══════════════════════════════════════════════════════════════════════════
# QUIZ / SKILL GAP / INTERVIEW / SALARY DATA
# ══════════════════════════════════════════════════════════════════════════
CAREER_QUIZ = [
    ("I enjoy finding patterns and stories hidden inside numbers and data.", "data science"),
    ("I like designing how a website or app looks and feels for users.", "web development"),
    ("I'm fascinated by teaching computers to learn from examples.", "machine learning"),
    ("I like thinking like an attacker to find and fix weaknesses in systems.", "cybersecurity"),
    ("I enjoy building and scaling the infrastructure behind big applications.", "cloud computing"),
    ("I'd rather write a SQL query than design a UI.", "data science"),
    ("I get excited about pixel-perfect layouts and smooth animations.", "web development"),
    ("Reading about neural networks or LLMs sounds like fun, not homework.", "machine learning"),
    ("I like the idea of a 'capture the flag' hacking challenge.", "cybersecurity"),
    ("I'm curious how apps stay up when millions of people use them at once.", "cloud computing"),
]

SALARY_DATA_INR = {  # indicative annual package in INR lakhs, by experience band
    "data science": {"Fresher (0-1 yr)": (4, 8), "Junior (1-3 yr)": (7, 14), "Mid (3-6 yr)": (14, 28), "Senior (6+ yr)": (28, 55)},
    "web development": {"Fresher (0-1 yr)": (3.5, 7), "Junior (1-3 yr)": (6, 12), "Mid (3-6 yr)": (12, 24), "Senior (6+ yr)": (24, 45)},
    "machine learning": {"Fresher (0-1 yr)": (5, 10), "Junior (1-3 yr)": (9, 18), "Mid (3-6 yr)": (18, 35), "Senior (6+ yr)": (35, 65)},
    "cybersecurity": {"Fresher (0-1 yr)": (4, 8), "Junior (1-3 yr)": (7, 15), "Mid (3-6 yr)": (15, 30), "Senior (6+ yr)": (30, 55)},
    "cloud computing": {"Fresher (0-1 yr)": (4, 9), "Junior (1-3 yr)": (8, 16), "Mid (3-6 yr)": (16, 32), "Senior (6+ yr)": (32, 60)},
    "general": {"Fresher (0-1 yr)": (3, 6), "Junior (1-3 yr)": (5, 10), "Mid (3-6 yr)": (10, 20), "Senior (6+ yr)": (20, 40)},
}

INTERVIEW_BANK = {
    "General / Behavioral": [
        "Tell me about yourself.", "Why do you want to work here?",
        "Describe a challenge you faced in a project and how you solved it.",
        "Where do you see yourself in 5 years?", "Tell me about a time you worked in a team.",
    ],
    "data science": [
        "Explain the bias-variance tradeoff.", "What is the difference between supervised and unsupervised learning?",
        "How would you handle missing data in a dataset?", "Explain a p-value in simple terms.",
        "Walk me through how you'd approach a new dataset.",
    ],
    "web development": [
        "Explain the virtual DOM and why React uses it.", "What is the difference between let, const, and var?",
        "How does the browser rendering pipeline work?", "What is CORS and why does it matter?",
        "How would you optimize a slow-loading web page?",
    ],
    "machine learning": [
        "Explain overfitting and how to prevent it.", "What is gradient descent?",
        "Compare precision and recall — when would you optimize for one over the other?",
        "What is transfer learning?", "Explain how a convolutional neural network works.",
    ],
    "cybersecurity": [
        "Explain the CIA triad.", "What is the difference between symmetric and asymmetric encryption?",
        "How would you respond to a suspected data breach?", "What is a SQL injection attack, and how do you prevent it?",
        "Explain the principle of least privilege.",
    ],
    "cloud computing": [
        "Explain the difference between IaaS, PaaS, and SaaS.", "What is autoscaling and why is it useful?",
        "How does a load balancer work?", "Explain the shared responsibility model in cloud security.",
        "What are the tradeoffs of serverless vs containers?",
    ],
    "Company: Product-based (e.g. Google/Amazon-style)": [
        "Design a URL shortener.", "How would you improve one of our products?",
        "Tell me about a time you disagreed with a teammate — what happened?",
        "How do you prioritize when everything feels urgent?",
    ],
    "Company: Service-based (e.g. TCS/Infosys-style)": [
        "Why do you want to join a service-based company?",
        "Are you comfortable relocating or working in shifts?",
        "Describe your final year academic project.",
        "How do you keep your technical skills updated?",
    ],
}

COURSES_BY_DOMAIN = {k: v["resources"] for k, v in CAREER_DOMAINS.items()}
COURSES_BY_DOMAIN["general"] = DEFAULT_DOMAIN["resources"]

SAMPLE_JOBS = [
    {"title": "Junior Data Analyst", "company": "Nimbus Analytics", "location": "Bengaluru (Hybrid)", "type": "Full-time", "domain": "data science"},
    {"title": "Frontend Developer Intern", "company": "Brightloop Labs", "location": "Remote", "type": "Internship", "domain": "web development"},
    {"title": "ML Engineer I", "company": "Vertex Cognition", "location": "Hyderabad", "type": "Full-time", "domain": "machine learning"},
    {"title": "SOC Analyst Intern", "company": "Cipherwall Security", "location": "Pune", "type": "Internship", "domain": "cybersecurity"},
    {"title": "Cloud Support Engineer", "company": "SkyStack Cloud", "location": "Remote", "type": "Full-time", "domain": "cloud computing"},
    {"title": "Associate Software Engineer", "company": "Generic Tech Services", "location": "Chennai", "type": "Full-time", "domain": "general"},
]

MENTOR_FALLBACK_TIPS = [
    "Break your goal into weekly milestones — momentum beats motivation.",
    "Ship small projects publicly; a finished small project beats an unfinished big one.",
    "Practice explaining your projects out loud — that's what interviewers actually probe.",
    "Consistency compounds: 1 focused hour daily beats 7 hours once a week.",
    "When stuck, explain the problem in writing first — it often reveals the fix.",
    "Tailor your resume bullets to the job description's exact keywords for ATS systems.",
    "Mock interviews feel awkward at first — that discomfort is the practice working.",
]

# ══════════════════════════════════════════════════════════════════════════
# GEMINI HELPERS (with graceful fallback)
# ══════════════════════════════════════════════════════════════════════════

def ai_chat_reply(user_msg: str, history: list) -> str:
    model = get_gemini_model()
    if model:
        try:
            context = "\n".join(f"{h['role']}: {h['content']}" for h in history[-6:])
            prompt = (
                "You are a warm, practical career mentor for students and early-career "
                "professionals. Keep replies concise (under 150 words), concrete, and encouraging.\n\n"
                f"Conversation so far:\n{context}\n\nStudent: {user_msg}\nMentor:"
            )
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception:
            pass
    # Fallback: keyword-based canned mentor response
    lower = user_msg.lower()
    if "resume" in lower:
        return "For your resume: lead each bullet with an action verb, quantify impact where you can, and mirror keywords from the job description so it passes ATS screening. Check the ATS Resume Builder page to generate one."
    if "interview" in lower:
        return "For interview prep: practice the STAR method (Situation, Task, Action, Result) for behavioral questions, and rehearse 2-3 project deep-dives out loud. Try the Mock Interview page to practice with a question bank."
    if "salary" in lower or "pay" in lower:
        return "Salary depends heavily on role, location, and experience. Check the Salary Predictor page for indicative ranges, and always research the specific company on sites like Glassdoor/AmbitionBox before negotiating."
    if any(w in lower for w in ["stuck", "confused", "overwhelmed", "lost"]):
        return "That's normal — most career paths feel foggy in the middle. Try picking ONE concrete next step (not the whole plan) and finish it this week. The Study Planner page can help you break things down."
    return random.choice(MENTOR_FALLBACK_TIPS) + " (Connect a Gemini API key in secrets for fully personalized AI chat.)"


def ai_mock_interview_question(domain_key: str, asked: list) -> str:
    model = get_gemini_model()
    bank = INTERVIEW_BANK.get(domain_key, INTERVIEW_BANK["General / Behavioral"])
    if model:
        try:
            prompt = (
                f"Ask ONE realistic technical/behavioral interview question for a {DOMAIN_LABELS.get(domain_key, 'general tech')} "
                f"role, different from these already asked: {asked}. Return ONLY the question, no preamble."
            )
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception:
            pass
    remaining = [q for q in bank if q not in asked] or bank
    return random.choice(remaining)


def ai_mock_interview_feedback(question: str, answer: str) -> str:
    model = get_gemini_model()
    if model:
        try:
            prompt = (
                f"Interview question: {question}\nCandidate answer: {answer}\n\n"
                "Give brief, constructive feedback (under 100 words): one strength, one improvement, "
                "and a 1-line model-answer tip."
            )
            resp = model.generate_content(prompt)
            return resp.text.strip()
        except Exception:
            pass
    word_count = len(answer.split())
    if word_count < 15:
        return "Your answer is quite short — interviewers want to hear your reasoning, not just a conclusion. Try structuring it as Situation → Action → Result, aiming for 4-6 sentences."
    return "Good attempt. To strengthen it: add a concrete number or outcome, and close with what you learned. (Connect a Gemini API key for detailed, personalized feedback.)"

# ══════════════════════════════════════════════════════════════════════════
# AUTH GATE — shown until the visitor logs in, signs up, or continues as guest
# ══════════════════════════════════════════════════════════════════════════
if not st.session_state.logged_in and not st.session_state.guest_mode:
    st.markdown(
        """<div class="ppai-header"><h1>🧭 PathPilot AI</h1>
        <p>Your all-in-one career companion — plan, prepare, and track your journey to your first (or next) job.</p></div>""",
        unsafe_allow_html=True,
    )

    hero_col, form_col = st.columns([1, 1.05], gap="large")

    with hero_col:
        st.markdown(
            f"""
            <div class="ppai-card" style="height:100%; display:flex; flex-direction:column; justify-content:center;">
                <div style="font-size:0.95rem; font-weight:700; color:{ACCENT1}; letter-spacing:.03em; text-transform:uppercase; margin-bottom:0.6rem;">
                    Free account · takes 15 seconds
                </div>
                <h2 style="margin:0 0 0.6rem; font-size:1.5rem; color:{TEXT};">
                    Everything you need for your next role, in one place
                </h2>
                <p class="ppai-sub" style="margin-bottom:1.1rem;">
                    Create an account and PathPilot remembers your roadmap, streaks, and resume
                    every time you come back — no re-entering anything.
                </p>
                <div style="display:flex; flex-direction:column; gap:0.65rem;">
                    <div style="display:flex; align-items:center; gap:0.6rem;">
                        <span class="ppai-badge" style="margin:0;">🗺️</span>
                        <span class="ppai-sub" style="color:{TEXT};">Personalized 12-week roadmaps that adapt to your pace</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.6rem;">
                        <span class="ppai-badge" style="margin:0;">🎯</span>
                        <span class="ppai-sub" style="color:{TEXT};">Goal tracking, streaks & badges that keep you consistent</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.6rem;">
                        <span class="ppai-badge" style="margin:0;">📄</span>
                        <span class="ppai-sub" style="color:{TEXT};">ATS resume builder, cover letters & mock interviews</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:0.6rem;">
                        <span class="ppai-badge" style="margin:0;">🔒</span>
                        <span class="ppai-sub" style="color:{TEXT};">Password never stored in plain text — salted &amp; hashed</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with form_col:
        st.markdown('<div class="ppai-card ppai-auth-card" style="margin:0;">', unsafe_allow_html=True)
        tab_login, tab_signup = st.tabs(["🔑 Log in", "🆕 Create account"])

        with tab_login:
            with st.form("login_form"):
                lu = st.text_input("Username", placeholder="your_username")
                lp = st.text_input("Password", type="password", placeholder="••••••••")
                do_login = st.form_submit_button("Log in →", use_container_width=True)
            if do_login:
                if not lu or not lp:
                    st.session_state.auth_error = "Enter both your username and password."
                else:
                    with st.spinner("Signing you in…"):
                        user = verify_login(lu, lp)
                    if user:
                        st.session_state.logged_in = True
                        st.session_state.username = user["username"]
                        st.session_state.user_name = user["name"] or user["username"]
                        st.session_state.user_email = user["email"]
                        st.session_state.user_target_role = user["target_role"]
                        st.session_state.auth_error = ""
                        st.toast(f"Welcome back, {user['name'] or user['username']} 👋", icon="🎉")
                        st.rerun()
                    else:
                        st.session_state.auth_error = "Incorrect username or password."

        with tab_signup:
            st.caption("Real account · saved securely · takes under a minute")
            with st.form("signup_form"):
                su_name = st.text_input("Your name", placeholder="e.g. Priya Sharma")
                su_username = st.text_input("Choose a username", placeholder="lowercase, no spaces")
                su_email = st.text_input("Email (optional)", placeholder="you@example.com")
                su_pw = st.text_input("Choose a password", type="password", placeholder="min. 6 characters")
                su_pw2 = st.text_input("Confirm password", type="password", placeholder="re-enter password")
                do_signup = st.form_submit_button("Create my account →", use_container_width=True)
            if do_signup:
                if not su_username.strip() or not su_pw:
                    st.session_state.auth_error = "Username and password are required."
                elif su_pw != su_pw2:
                    st.session_state.auth_error = "Passwords don't match."
                elif len(su_pw) < 6:
                    st.session_state.auth_error = "Password must be at least 6 characters."
                else:
                    with st.spinner("Creating your account…"):
                        ok, msg = create_account(su_username, su_email, su_name, su_pw)
                    if ok:
                        st.session_state.logged_in = True
                        st.session_state.username = su_username.strip().lower()
                        st.session_state.user_name = su_name.strip() or su_username
                        st.session_state.user_email = su_email.strip()
                        st.session_state.user_target_role = ""
                        st.session_state.auth_error = ""
                        st.toast(f"Account created — welcome, {su_name.strip() or su_username} 🎉", icon="✅")
                        st.rerun()
                    else:
                        st.session_state.auth_error = msg

        if st.session_state.auth_error:
            st.error(st.session_state.auth_error, icon="⚠️")

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
    "💡 AI Project Generator", "🏢 Interview Preparation", "🎤 Mock Interview",
    "📈 Placement Readiness Score", "💰 Salary Predictor", "📚 Learning Hub",
    "💼 Jobs & Internships", "🐙 GitHub & LeetCode Tracker", "🖼️ Project Showcase",
    "⚙️ Settings", "🛠️ Admin & Analytics (Demo)",
]

with st.sidebar:
    st.markdown("## 🧭 PathPilot AI")
    if st.session_state.logged_in:
        st.markdown(f"**Hi, {st.session_state.user_name or st.session_state.username}** 👋")
        st.caption(f"✅ Logged in as **{st.session_state.username}**")
    elif st.session_state.guest_mode:
        st.caption("👤 Browsing as guest — sign up anytime in ⚙️ Settings to save your data.")
    if not st.session_state.user_name and not st.session_state.logged_in:
        st.caption("Set your name in ⚙️ Settings to personalize your dashboard.")
    if st.session_state.logged_in and st.button("🚪 Log out", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.guest_mode = False
        st.session_state.username = ""
        st.session_state.user_name = ""
        st.session_state.user_email = ""
        st.session_state.user_target_role = ""
        st.rerun()
    level = st.session_state.xp // 100 + 1
    st.progress(min((st.session_state.xp % 100) / 100, 1.0), text=f"Level {level} · {st.session_state.xp} XP")
    c1, c2 = st.columns(2)
    c1.metric("🔥 Streak", st.session_state.streak_count)
    c2.metric("🏆 Badges", len(st.session_state.badges))
    if not AI_AVAILABLE:
        st.caption("💡 AI chat/interview running in offline fallback mode (no Gemini key set).")
    st.divider()
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.divider()
    st.session_state.theme = st.radio("Theme", ["Light", "Dark"], index=["Light", "Dark"].index(st.session_state.theme), horizontal=True)
    st.session_state.language = st.selectbox("Language (labels)", ["English", "Hindi", "Spanish"], index=["English", "Hindi", "Spanish"].index(st.session_state.language))
    st.session_state.is_premium = st.toggle("✨ Premium mode (demo)", value=st.session_state.is_premium)

# ══════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════
GREETING = {"English": "Your all-in-one career companion", "Hindi": "आपका करियर साथी", "Spanish": "Tu compañero de carrera"}
st.markdown(
    f"""<div class="ppai-header"><h1>🧭 PathPilot AI</h1>
    <p>{GREETING.get(st.session_state.language, GREETING['English'])} — plan, prepare, and track your journey to your first (or next) job.</p></div>""",
    unsafe_allow_html=True,
)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════
if page == "🏠 Dashboard":
    if not st.session_state.user_name:
        with st.container():
            st.info("👋 New here? Head to **⚙️ Settings** to set your name and target role — it personalizes everything below.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Roadmap", "Active" if st.session_state.roadmap else "Not started")
    c2.metric("Goals set", len(st.session_state.goals))
    c3.metric("Resume", "Built ✅" if st.session_state.resume_data else "Pending")
    c4.metric("Mock interviews", len(st.session_state.mock_interview_log))

    st.markdown("### 📈 Your progress")
    cols = st.columns(3)
    with cols[0]:
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.markdown("**🗺️ Roadmap**")
        if st.session_state.roadmap:
            done = sum(1 for w in st.session_state.roadmap if w["done"])
            st.progress(done / 12, text=f"{done}/12 weeks complete")
        else:
            st.caption("Generate one in AI Roadmap Generator.")
        st.markdown("</div>", unsafe_allow_html=True)
    with cols[1]:
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.markdown("**📅 Study planner**")
        total_tasks = len(st.session_state.planner_tasks)
        done_tasks = sum(1 for t in st.session_state.planner_tasks if t["done"])
        if total_tasks:
            st.progress(done_tasks / total_tasks, text=f"{done_tasks}/{total_tasks} tasks done")
        else:
            st.caption("Add tasks in Study Planner.")
        st.markdown("</div>", unsafe_allow_html=True)
    with cols[2]:
        st.markdown('<div class="ppai-card">', unsafe_allow_html=True)
        st.markdown("**🎯 Goals**")
        gtotal = len(st.session_state.goals)
        gdone = sum(1 for g in st.session_state.goals if g["done"])
        if gtotal:
            st.progress(gdone / gtotal, text=f"{gdone}/{gtotal} goals achieved")
        else:
            st.caption("Set goals in Goals & Progress.")
        st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.notifications:
        st.markdown("### 🔔 Recent activity")
        for n in st.session_state.notifications[:6]:
            st.markdown(f"<div class='ppai-sub'>• {n}</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: CAREER MATCH TEST
# ══════════════════════════════════════════════════════════════════════════
elif page == "🧭 Career Match Test":
    st.markdown("### 🧭 Career Match Test")
    st.caption("Rate how much each statement sounds like you. Takes ~2 minutes.")
    with st.form("career_quiz"):
        scores = {k: 0 for k in CAREER_DOMAINS}
        answers = []
        for i, (statement, domain) in enumerate(CAREER_QUIZ):
            val = st.slider(statement, 1, 5, 3, key=f"quiz_{i}")
            answers.append((domain, val))
        submitted = st.form_submit_button("See my match")
    if submitted:
        for domain, val in answers:
            scores[domain] += val
        best = max(scores, key=scores.get)
        st.session_state.quiz_result = {"scores": scores, "best": best}
        award_badge("quiz_taker")
        add_xp(20, "Completed Career Match Test")

    if st.session_state.quiz_result:
        result = st.session_state.quiz_result
        best_label = DOMAIN_LABELS.get(result["best"], result["best"].title())
        st.success(f"🎯 Your strongest match: **{best_label}**")
        st.bar_chart({DOMAIN_LABELS.get(k, k): v for k, v in result["scores"].items()})
        if st.button("Generate a roadmap for this match"):
            st.session_state["_prefill_goal"] = best_label
            st.info("Head to 🗺️ AI Roadmap Generator — your match is pre-filled there.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI ROADMAP GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "🗺️ AI Roadmap Generator":
    st.markdown("### 🗺️ AI Career Roadmap Generator")
    col_form, col_info = st.columns([1, 1], gap="large")
    with col_form:
        with st.form("roadmap_form"):
            career_goal = st.text_input("Career goal", value=st.session_state.get("_prefill_goal", ""), placeholder="e.g. Data Science, Web Development")
            skill_level = st.selectbox("Current skill level", ["Beginner", "Intermediate", "Advanced"])
            hours_per_day = st.number_input("Study hours per day", min_value=0.5, max_value=12.0, value=2.0, step=0.5)
            gen = st.form_submit_button("Generate roadmap")
        with col_info:
            st.markdown("**How it works**")
            st.markdown("1. Tell us your goal & skill level\n2. Set your daily study hours\n3. Get a structured 12-week plan with resources, projects, and time estimates")
            st.info("Supported paths: Data Science, Web Development, Machine Learning, Cybersecurity, Cloud Computing. Other goals get a general roadmap.")
    if gen:
        if not career_goal.strip():
            st.warning("Please enter a career goal.")
        else:
            roadmap, domain_key = generate_roadmap(career_goal.strip(), skill_level, hours_per_day)
            st.session_state.roadmap = roadmap
            st.session_state.roadmap_domain_key = domain_key
            st.session_state.user_target_role = career_goal.strip()
            if "first_roadmap" not in st.session_state.badges:
                add_xp(25, "Generated your first roadmap")
            award_badge("first_roadmap")

    if st.session_state.roadmap:
        roadmap = st.session_state.roadmap
        total_hours = sum(w["hours"] for w in roadmap)
        done = sum(1 for w in roadmap if w["done"])
        st.markdown("---")
        st.markdown(f"#### Your 12-week roadmap for **{st.session_state.user_target_role or 'your goal'}**")
        st.progress(done / 12, text=f"{done}/12 weeks marked complete")
        left, right = st.columns(2, gap="medium")
        for idx, week in enumerate(roadmap):
            col = left if idx % 2 == 0 else right
            with col:
                st.markdown(
                    f"""<div class="ppai-card">
                    <span class="ppai-badge">Week {week['week']}</span>
                    <div class="ppai-topic">{week['topic']}</div>
                    <div class="ppai-sub">📚 <a href="{week['resource_url']}" target="_blank">{week['resource_name']}</a></div>
                    <div class="ppai-sub">🛠️ {week['project']}</div>
                    <div class="ppai-sub">⏱️ ~{week['hours']} hrs this week</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                checked = st.checkbox("Mark week done", value=week["done"], key=f"week_done_{idx}")
                if checked != week["done"]:
                    st.session_state.roadmap[idx]["done"] = checked
                    if checked:
                        add_xp(10, f"Completed Week {week['week']}")
                    st.rerun()
        st.download_button(
            "📥 Download roadmap as text",
            data="\n\n".join(
                f"Week {w['week']}: {w['topic']}\n  Resource: {w['resource_name']} ({w['resource_url']})\n  Project: {w['project']}\n  Hours: ~{w['hours']}"
                for w in roadmap
            ),
            file_name="pathpilot_roadmap.txt",
        )

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI MENTOR CHAT
# ══════════════════════════════════════════════════════════════════════════
elif page == "💬 AI Mentor Chat":
    st.markdown("### 💬 AI Career Mentor Chat")
    if not AI_AVAILABLE:
        st.caption("Running in offline fallback mode — add `GEMINI_API_KEY` to `st.secrets` for fully personalized AI replies.")
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    user_msg = st.chat_input("Ask about resumes, interviews, learning paths, career doubts...")
    if user_msg:
        st.session_state.chat_history.append({"role": "user", "content": user_msg})
        reply = ai_chat_reply(user_msg, st.session_state.chat_history)
        st.session_state.chat_history.append({"role": "assistant", "content": reply})
        add_xp(5, "Chatted with your mentor")
        st.rerun()
    if st.session_state.is_premium:
        st.caption("✨ Premium: unlimited chats enabled. (Free tier would normally cap daily messages.)")
    else:
        st.caption(f"Free tier: {len(st.session_state.chat_history)//2} messages used today (demo — not actually capped).")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: SKILL GAP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════
elif page == "📊 Skill Gap Analysis":
    st.markdown("### 📊 Skill Gap Analysis")
    domain_key = st.selectbox("Target role", list(CAREER_DOMAINS.keys()) + ["general"], format_func=lambda k: DOMAIN_LABELS.get(k, k.title()))
    required = get_domain(domain_key)["skills"]
    st.caption("Check the skills you already have:")
    current = []
    cols = st.columns(2)
    for i, skill in enumerate(required):
        with cols[i % 2]:
            if st.checkbox(skill, value=skill in st.session_state.current_skills, key=f"skill_{domain_key}_{i}"):
                current.append(skill)
    if st.button("Analyze gap"):
        st.session_state.current_skills = current
        gap = [s for s in required if s not in current]
        pct = round(100 * (len(required) - len(gap)) / len(required))
        st.progress(pct / 100, text=f"{pct}% skill match for {DOMAIN_LABELS.get(domain_key, domain_key)}")
        if gap:
            st.markdown("#### Skills to focus on next")
            for s in gap:
                st.markdown(f"- 🔲 {s}")
        else:
            st.success("You've checked off every core skill for this role! Time to build proof — projects and mock interviews.")
        add_xp(10, "Ran a skill gap analysis")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: STUDY PLANNER
# ══════════════════════════════════════════════════════════════════════════
elif page == "📅 Study Planner":
    st.markdown("### 📅 Daily & Weekly Study Planner")
    with st.form("add_task", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 1, 1])
        task_text = c1.text_input("Task", placeholder="e.g. Finish Pandas exercises")
        due = c2.date_input("Due date", value=date.today())
        priority = c3.selectbox("Priority", ["Low", "Medium", "High"])
        add = st.form_submit_button("Add task")
    if add and task_text.strip():
        st.session_state.planner_tasks.append({"text": task_text.strip(), "due": due.isoformat(), "priority": priority, "done": False})
        if len(st.session_state.planner_tasks) >= 5:
            award_badge("planner_pro")
        add_xp(5, "Added a planner task")

    if st.session_state.planner_tasks:
        st.markdown("#### This week")
        for i, t in enumerate(sorted(st.session_state.planner_tasks, key=lambda x: x["due"])):
            real_idx = st.session_state.planner_tasks.index(t)
            c1, c2, c3 = st.columns([5, 1, 1])
            checked = c1.checkbox(f"{t['text']} — due {t['due']} · {t['priority']} priority", value=t["done"], key=f"task_{real_idx}")
            if checked != t["done"]:
                st.session_state.planner_tasks[real_idx]["done"] = checked
                if checked:
                    add_xp(5, "Completed a planner task")
                st.rerun()
            if c2.button("🗑️", key=f"del_task_{real_idx}"):
                st.session_state.planner_tasks.pop(real_idx)
                st.rerun()
    else:
        st.caption("No tasks yet — add your first one above.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: GOALS & PROGRESS
# ══════════════════════════════════════════════════════════════════════════
elif page == "🎯 Goals & Progress":
    st.markdown("### 🎯 Goal Tracking & Progress Dashboard")
    with st.form("add_goal", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        goal_text = c1.text_input("New goal", placeholder="e.g. Finish 3 LeetCode mediums this week")
        target_date = c2.date_input("Target date", value=date.today() + timedelta(days=7))
        add_goal = st.form_submit_button("Add goal")
    if add_goal and goal_text.strip():
        st.session_state.goals.append({"text": goal_text.strip(), "target": target_date.isoformat(), "done": False})
        award_badge("goal_setter")
        add_xp(10, "Set a new goal")

    for i, g in enumerate(st.session_state.goals):
        c1, c2 = st.columns([5, 1])
        checked = c1.checkbox(f"{g['text']} (by {g['target']})", value=g["done"], key=f"goal_{i}")
        if checked != g["done"]:
            st.session_state.goals[i]["done"] = checked
            if checked:
                add_xp(15, "Achieved a goal")
            st.rerun()
        if c2.button("🗑️", key=f"del_goal_{i}"):
            st.session_state.goals.pop(i)
            st.rerun()

    st.markdown("#### Overview")
    weekly = [random.randint(1, 4) for _ in range(7)]  # illustrative activity pattern for the chart shape
    if st.session_state.planner_tasks or st.session_state.goals:
        done_tasks = sum(1 for t in st.session_state.planner_tasks if t["done"])
        done_goals = sum(1 for g in st.session_state.goals if g["done"])
        st.bar_chart({"Tasks done": [done_tasks], "Goals achieved": [done_goals]})

# ══════════════════════════════════════════════════════════════════════════
# PAGE: ACHIEVEMENTS
# ══════════════════════════════════════════════════════════════════════════
elif page == "🏆 Achievements":
    st.markdown("### 🏆 Achievement Badges & Streaks")
    c1, c2, c3 = st.columns(3)
    c1.metric("XP", st.session_state.xp)
    c2.metric("Level", st.session_state.xp // 100 + 1)
    c3.metric("🔥 Streak (days)", st.session_state.streak_count)
    st.markdown("#### Your badges")
    cols = st.columns(3)
    for i, (key, (emoji, title, desc)) in enumerate(BADGE_DEFS.items()):
        earned = key in st.session_state.badges
        with cols[i % 3]:
            style = "" if earned else "opacity:0.35;"
            st.markdown(
                f"""<div class="ppai-card" style="{style} text-align:center;">
                <div style="font-size:2rem;">{emoji}</div>
                <div style="font-weight:700;">{title}</div>
                <div class="ppai-sub">{desc}</div>
                </div>""",
                unsafe_allow_html=True,
            )

# ══════════════════════════════════════════════════════════════════════════
# PAGE: ATS RESUME BUILDER
# ══════════════════════════════════════════════════════════════════════════
elif page == "📄 ATS Resume Builder":
    st.markdown("### 📄 ATS-Friendly Resume Builder")
    with st.form("resume_form"):
        name = st.text_input("Full name", value=st.session_state.user_name)
        contact = st.text_input("Email / phone / LinkedIn", placeholder="you@email.com · linkedin.com/in/you")
        summary = st.text_area("Professional summary (2-3 sentences)", placeholder="Aspiring data analyst with hands-on project experience in Python and SQL...")
        skills = st.text_input("Key skills (comma-separated)", placeholder="Python, SQL, Pandas, Git")
        experience = st.text_area("Experience / internships (one per line)", placeholder="Data Analyst Intern, XYZ Corp (Jun-Aug 2025): built dashboards that cut reporting time 30%")
        education = st.text_area("Education (one per line)", placeholder="B.Tech Computer Science, ABC University, 2023-2027")
        projects = st.text_area("Projects (one per line)", placeholder="Sales Forecasting Dashboard: Python, Pandas, Plotly — forecast accuracy improved 15%")
        submit_resume = st.form_submit_button("Build resume")
    if submit_resume:
        st.session_state.resume_data = {
            "name": name, "contact": contact, "summary": summary, "skills": skills,
            "experience": experience, "education": education, "projects": projects,
        }
        award_badge("resume_built")
        add_xp(25, "Built your ATS resume")

    if st.session_state.resume_data:
        d = st.session_state.resume_data
        resume_text = f"""{d['name']}
{d['contact']}

PROFESSIONAL SUMMARY
{d['summary']}

SKILLS
{d['skills']}

EXPERIENCE
{d['experience']}

PROJECTS
{d['projects']}

EDUCATION
{d['education']}
"""
        st.markdown("#### Preview")
        st.code(resume_text, language=None)
        st.download_button("📥 Download resume (.txt)", data=resume_text, file_name="resume.txt")
        st.markdown("#### ✅ ATS tips")
        st.markdown("- Use standard section headers (Experience, Education, Skills) — avoid tables/graphics.\n- Mirror exact keywords from the job description.\n- Quantify impact wherever possible (%, ₹, time saved).\n- Save/export as a simple, text-selectable PDF, not an image.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: COVER LETTER GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "✉️ Cover Letter Generator":
    st.markdown("### ✉️ Cover Letter Generator")
    with st.form("cover_letter_form"):
        company = st.text_input("Company name")
        role = st.text_input("Role you're applying for", value=st.session_state.user_target_role)
        highlight = st.text_area("One achievement or project to highlight")
        tone = st.selectbox("Tone", ["Professional", "Enthusiastic", "Concise"])
        gen_letter = st.form_submit_button("Generate cover letter")
    if gen_letter:
        model = get_gemini_model()
        letter = None
        if model:
            try:
                prompt = (
                    f"Write a {tone.lower()} cover letter (under 250 words) for a {role or 'the'} role at "
                    f"{company or 'the company'}. Highlight this achievement: {highlight}. "
                    f"Applicant name: {st.session_state.user_name or 'the applicant'}."
                )
                letter = model.generate_content(prompt).text.strip()
            except Exception:
                letter = None
        if not letter:
            letter = f"""Dear Hiring Manager,

I'm excited to apply for the {role or '[Role]'} position at {company or '[Company]'}. {highlight or 'My recent project work has sharpened my practical skills and my drive to contribute from day one.'}

I'm particularly drawn to {company or 'your team'} because of the opportunity to grow while doing meaningful, hands-on work. I'd welcome the chance to bring my curiosity and consistency to your team.

Thank you for considering my application — I'd love to discuss how I can contribute.

Sincerely,
{st.session_state.user_name or '[Your name]'}"""
        st.session_state.cover_letter_text = letter
        award_badge("cover_letter")
        add_xp(15, "Generated a cover letter")

    if st.session_state.cover_letter_text:
        st.markdown("#### Preview")
        st.text_area("Cover letter", value=st.session_state.cover_letter_text, height=280, label_visibility="collapsed")
        st.download_button("📥 Download cover letter (.txt)", data=st.session_state.cover_letter_text, file_name="cover_letter.txt")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PORTFOLIO GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "🌐 Portfolio Generator":
    st.markdown("### 🌐 Portfolio Website Generator")
    st.caption("Generates a single-file HTML site you can host free on GitHub Pages / Netlify.")
    with st.form("portfolio_form"):
        p_name = st.text_input("Name", value=st.session_state.user_name)
        p_tagline = st.text_input("Tagline", placeholder="Aspiring Data Scientist | Python · SQL · ML")
        p_about = st.text_area("About you", placeholder="A few sentences about your background and interests.")
        p_links = st.text_input("Links (comma-separated)", placeholder="github.com/you, linkedin.com/in/you")
        gen_site = st.form_submit_button("Generate portfolio site")
    if gen_site:
        proj_html = "".join(
            f"<div class='card'><h3>{pr['title']}</h3><p>{pr['description']}</p>"
            f"{'<a href=\"' + pr['link'] + '\" target=\"_blank\">View project</a>' if pr.get('link') else ''}</div>"
            for pr in st.session_state.projects
        ) or "<p>Add projects on the Project Showcase page — they'll appear here.</p>"
        links_html = " · ".join(f"<a href='https://{l.strip()}' target='_blank'>{l.strip()}</a>" for l in p_links.split(",") if l.strip())
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{p_name} — Portfolio</title>
<style>
body{{font-family:'Inter',sans-serif;background:#0f1117;color:#f1f5f9;margin:0;padding:2rem;}}
.container{{max-width:800px;margin:0 auto;}}
h1{{background:linear-gradient(135deg,#6366f1,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
.card{{background:#1a1d29;border:1px solid #2d3148;border-radius:12px;padding:1rem 1.3rem;margin-bottom:1rem;}}
a{{color:#a855f7;}}
</style></head><body><div class="container">
<h1>{p_name}</h1><p>{p_tagline}</p><p>{p_about}</p><p>{links_html}</p>
<h2>Projects</h2>{proj_html}
</div></body></html>"""
        st.download_button("📥 Download portfolio (index.html)", data=html, file_name="index.html", mime="text/html")
        st.components.v1.html(html, height=500, scrolling=True)
        add_xp(20, "Generated a portfolio site")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: AI PROJECT GENERATOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "💡 AI Project Generator":
    st.markdown("### 💡 AI Project Idea Generator")
    domain_key = st.selectbox("Domain", list(CAREER_DOMAINS.keys()), format_func=lambda k: DOMAIN_LABELS.get(k, k.title()))
    level = st.selectbox("Level", ["Beginner", "Intermediate", "Advanced"])
    if st.button("Generate project ideas"):
        model = get_gemini_model()
        ideas = None
        if model:
            try:
                prompt = f"Suggest 5 distinct, resume-worthy {level.lower()} {DOMAIN_LABELS[domain_key]} project ideas, one line each, no numbering explanation, just the idea and one-line scope."
                ideas = model.generate_content(prompt).text.strip().split("\n")
            except Exception:
                ideas = None
        if not ideas:
            pool = get_domain(domain_key)["projects"]
            ideas = random.sample(pool, k=min(5, len(pool)))
        for idea in ideas:
            if idea.strip():
                st.markdown(f"<div class='ppai-card'>💡 {idea.strip()}</div>", unsafe_allow_html=True)
        add_xp(10, "Generated project ideas")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: INTERVIEW PREPARATION
# ══════════════════════════════════════════════════════════════════════════
elif page == "🏢 Interview Preparation":
    st.markdown("### 🏢 Company-wise & Domain Interview Preparation")
    category = st.selectbox("Choose a track", list(INTERVIEW_BANK.keys()), format_func=lambda k: DOMAIN_LABELS.get(k, k))
    for q in INTERVIEW_BANK[category]:
        with st.expander(q):
            st.caption("Write your own answer, then check the Mock Interview page for AI feedback on similar questions.")
            st.text_area("Your practice answer", key=f"prep_{category}_{q}", label_visibility="collapsed")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: MOCK INTERVIEW
# ══════════════════════════════════════════════════════════════════════════
elif page == "🎤 Mock Interview":
    st.markdown("### 🎤 AI Mock Interview")
    st.caption("Text-based mock interview. (Voice mode is a planned premium feature — needs browser mic capture + speech-to-text, not available in this demo.)")
    domain_key = st.selectbox("Interview domain", list(CAREER_DOMAINS.keys()), format_func=lambda k: DOMAIN_LABELS.get(k, k.title()))
    if "current_mock_q" not in st.session_state or st.button("🔀 New question"):
        asked = [m["question"] for m in st.session_state.mock_interview_log]
        st.session_state.current_mock_q = ai_mock_interview_question(domain_key, asked)
    st.markdown(f"**Q: {st.session_state.current_mock_q}**")
    answer = st.text_area("Your answer", key="mock_answer")
    if st.button("Submit answer for feedback"):
        if answer.strip():
            feedback = ai_mock_interview_feedback(st.session_state.current_mock_q, answer)
            st.session_state.mock_interview_log.append({"question": st.session_state.current_mock_q, "answer": answer, "feedback": feedback})
            award_badge("interview_ready")
            add_xp(20, "Completed a mock interview question")
            st.success(feedback)
        else:
            st.warning("Write an answer first.")
    if st.session_state.mock_interview_log:
        with st.expander(f"📜 History ({len(st.session_state.mock_interview_log)} answered)"):
            for entry in reversed(st.session_state.mock_interview_log[-10:]):
                st.markdown(f"**Q:** {entry['question']}")
                st.markdown(f"*Feedback:* {entry['feedback']}")
                st.divider()

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PLACEMENT READINESS SCORE
# ══════════════════════════════════════════════════════════════════════════
elif page == "📈 Placement Readiness Score":
    st.markdown("### 📈 Placement Readiness Score")
    roadmap_score = (sum(1 for w in st.session_state.roadmap if w["done"]) / 12 * 25) if st.session_state.roadmap else 0
    quiz_score = 15 if st.session_state.quiz_result else 0
    resume_score = 20 if st.session_state.resume_data else 0
    interview_score = min(len(st.session_state.mock_interview_log) * 5, 25)
    project_score = min(len(st.session_state.projects) * 5, 15)
    total = round(roadmap_score + quiz_score + resume_score + interview_score + project_score)
    st.metric("Overall readiness", f"{total} / 100")
    st.progress(total / 100)
    breakdown = {
        "Roadmap progress (25%)": round(roadmap_score, 1),
        "Career clarity (15%)": quiz_score,
        "Resume ready (20%)": resume_score,
        "Mock interviews (25%)": interview_score,
        "Project portfolio (15%)": project_score,
    }
    st.markdown("#### Breakdown")
    for k, v in breakdown.items():
        st.markdown(f"- **{k}:** {v}")
    if total < 40:
        st.info("Early days — start with the AI Roadmap Generator and Career Match Test to build your foundation.")
    elif total < 75:
        st.info("Good progress — focus on mock interviews and building 1-2 more portfolio projects.")
    else:
        st.success("You're in strong shape for applications — keep practicing interviews and start applying!")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: SALARY PREDICTOR
# ══════════════════════════════════════════════════════════════════════════
elif page == "💰 Salary Predictor":
    st.markdown("### 💰 Salary Predictor")
    st.caption("Indicative ranges only, based on common Indian market bands — always cross-check with Glassdoor/AmbitionBox/Levels.fyi for a specific company & city.")
    domain_key = st.selectbox("Domain", list(SALARY_DATA_INR.keys()), format_func=lambda k: DOMAIN_LABELS.get(k, k.title()))
    band = st.selectbox("Experience", list(SALARY_DATA_INR[domain_key].keys()))
    low, high = SALARY_DATA_INR[domain_key][band]
    st.metric(f"Estimated CTC ({band})", f"₹{low}–{high} LPA")
    st.caption("LPA = Lakhs Per Annum. This is a static, illustrative estimate — not live market data.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: LEARNING HUB
# ══════════════════════════════════════════════════════════════════════════
elif page == "📚 Learning Hub":
    st.markdown("### 📚 Learning Hub")
    tabs = st.tabs(["Courses", "Practice Quiz", "Notes & Bookmarks", "Certificate Tracker"])
    with tabs[0]:
        domain_key = st.selectbox("Domain", list(COURSES_BY_DOMAIN.keys()), format_func=lambda k: DOMAIN_LABELS.get(k, k.title()), key="learning_domain")
        for name, url in COURSES_BY_DOMAIN[domain_key]:
            st.markdown(f"- [{name}]({url})")
    with tabs[1]:
        st.caption("Quick knowledge check — pulled from the interview question bank.")
        domain_key2 = st.selectbox("Quiz domain", [k for k in CAREER_DOMAINS], format_func=lambda k: DOMAIN_LABELS.get(k, k.title()), key="quiz_domain")
        q = random.choice(INTERVIEW_BANK[domain_key2])
        st.markdown(f"**{q}**")
        st.text_area("Your answer", key="learning_quiz_answer")
        if st.button("Check with mentor"):
            st.info(ai_chat_reply(f"Give brief feedback on this answer to '{q}': {st.session_state.get('learning_quiz_answer','')}", []))
    with tabs[2]:
        note = st.text_area("Add a note")
        if st.button("Save note") and note.strip():
            st.session_state.bookmarks.append({"type": "note", "text": note.strip(), "date": date.today().isoformat()})
        for b in reversed(st.session_state.bookmarks):
            st.markdown(f"- 🗒️ {b['text']} ({b['date']})")
    with tabs[3]:
        with st.form("cert_form", clear_on_submit=True):
            cname = st.text_input("Certificate name")
            cissuer = st.text_input("Issued by")
            cdate = st.date_input("Date earned", value=date.today())
            clink = st.text_input("Credential link (optional)")
            add_cert = st.form_submit_button("Add certificate")
        if add_cert and cname.strip():
            st.session_state.certificates.append({"name": cname, "issuer": cissuer, "date": cdate.isoformat(), "link": clink})
            add_xp(10, "Added a certificate")
        for c in st.session_state.certificates:
            st.markdown(f"- 🎓 **{c['name']}** — {c['issuer']} ({c['date']})" + (f" — [view]({c['link']})" if c["link"] else ""))

# ══════════════════════════════════════════════════════════════════════════
# PAGE: JOBS & INTERNSHIPS
# ══════════════════════════════════════════════════════════════════════════
elif page == "💼 Jobs & Internships":
    st.markdown("### 💼 Jobs & Internships")
    st.caption("Sample listings for demo purposes — connect a job-board API (e.g. Internshala, LinkedIn Jobs) for live results.")
    tabs = st.tabs(["Browse listings", "Application Tracker", "Job Alerts", "Company Profiles"])
    with tabs[0]:
        filter_domain = st.selectbox("Filter by domain", ["All"] + list(CAREER_DOMAINS.keys()) + ["general"], format_func=lambda k: DOMAIN_LABELS.get(k, k.title()) if k != "All" else "All")
        for job in SAMPLE_JOBS:
            if filter_domain != "All" and job["domain"] != filter_domain:
                continue
            st.markdown(
                f"""<div class="ppai-card"><div class="ppai-topic">{job['title']}</div>
                <div class="ppai-sub">{job['company']} · {job['location']} · {job['type']}</div></div>""",
                unsafe_allow_html=True,
            )
            if st.button(f"Track application: {job['title']}", key=f"track_{job['title']}"):
                st.session_state.applications.append({"title": job["title"], "company": job["company"], "status": "Applied", "date": date.today().isoformat()})
                add_xp(10, "Tracked a job application")
    with tabs[1]:
        for i, app in enumerate(st.session_state.applications):
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"**{app['title']}** at {app['company']} — applied {app['date']}")
            new_status = c2.selectbox("Status", ["Applied", "Interview", "Offer", "Rejected"], index=["Applied", "Interview", "Offer", "Rejected"].index(app["status"]), key=f"status_{i}")
            st.session_state.applications[i]["status"] = new_status
        if not st.session_state.applications:
            st.caption("No applications tracked yet — track one from the Browse listings tab.")
    with tabs[2]:
        alert_kw = st.text_input("Alert me about roles matching keyword")
        alert_email = st.text_input("Email for alerts (demo only — not actually sent)")
        if st.button("Save alert") and alert_kw:
            st.success(f"Demo alert saved for '{alert_kw}'. (Wire this to an email service for real alerts.)")
    with tabs[3]:
        for job in {j["company"] for j in SAMPLE_JOBS}:
            st.markdown(f"**{job}** — sample company profile. Add a real company-data API/source to enrich this.")

# ══════════════════════════════════════════════════════════════════════════
# PAGE: GITHUB & LEETCODE TRACKER
# ══════════════════════════════════════════════════════════════════════════
elif page == "🐙 GitHub & LeetCode Tracker":
    st.markdown("### 🐙 GitHub Integration & LeetCode Tracker")
    st.markdown("#### GitHub")
    username = st.text_input("GitHub username", value=st.session_state.github_username)
    if st.button("Fetch GitHub profile"):
        st.session_state.github_username = username
        try:
            r = requests.get(f"https://api.github.com/users/{username}", timeout=6)
            if r.status_code == 200:
                data = r.json()
                c1, c2, c3 = st.columns(3)
                c1.metric("Public repos", data.get("public_repos", 0))
                c2.metric("Followers", data.get("followers", 0))
                c3.metric("Following", data.get("following", 0))
                st.markdown(f"[View GitHub profile ↗]({data.get('html_url', '')})")
                add_xp(10, "Synced GitHub profile")
            else:
                st.warning("Couldn't find that GitHub username.")
        except Exception:
            st.warning("Couldn't reach GitHub right now — this needs internet access at runtime.")

    st.markdown("#### LeetCode (manual entry)")
    st.caption("LeetCode has no official public API, so this is a manual tracker for now.")
    c1, c2, c3 = st.columns(3)
    easy = c1.number_input("Easy solved", min_value=0, value=st.session_state.leetcode_stats["easy"])
    medium = c2.number_input("Medium solved", min_value=0, value=st.session_state.leetcode_stats["medium"])
    hard = c3.number_input("Hard solved", min_value=0, value=st.session_state.leetcode_stats["hard"])
    if st.button("Save LeetCode progress"):
        st.session_state.leetcode_stats = {"easy": easy, "medium": medium, "hard": hard}
        add_xp(5, "Updated coding progress")
    st.bar_chart(st.session_state.leetcode_stats)

# ══════════════════════════════════════════════════════════════════════════
# PAGE: PROJECT SHOWCASE
# ══════════════════════════════════════════════════════════════════════════
elif page == "🖼️ Project Showcase":
    st.markdown("### 🖼️ Project Showcase")
    with st.form("project_form", clear_on_submit=True):
        title = st.text_input("Project title")
        description = st.text_area("Short description")
        link = st.text_input("Link (GitHub/live demo)")
        tags = st.text_input("Tags (comma-separated)")
        add_proj = st.form_submit_button("Add project")
    if add_proj and title.strip():
        st.session_state.projects.append({"title": title, "description": description, "link": link, "tags": tags})
        award_badge("project_builder")
        add_xp(15, "Added a project to your showcase")

    if st.session_state.projects:
        cols = st.columns(2)
        for i, p in enumerate(st.session_state.projects):
            with cols[i % 2]:
                tag_html = "".join(f"<span class='ppai-tag'>{t.strip()}</span>" for t in p["tags"].split(",") if t.strip())
                link_html = f"<a href='{p['link']}' target='_blank'>View project ↗</a>" if p["link"] else ""
                st.markdown(
                    f"""<div class="ppai-card"><div class="ppai-topic">{p['title']}</div>
                    <div class="ppai-sub">{p['description']}</div><div>{tag_html}</div><div class="ppai-sub">{link_html}</div></div>""",
                    unsafe_allow_html=True,
                )
    else:
        st.caption("No projects yet — add your first above.")

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
            st.success("Profile saved for this guest session (create an account below to keep it).")

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
    st.divider()
    st.markdown("#### Notifications")
    if st.session_state.notifications:
        for n in st.session_state.notifications:
            st.markdown(f"- {n}")
    else:
        st.caption("Nothing yet — activity across the app will show up here.")
    st.divider()
    if st.button("🔄 Reset all my data (this session)"):
        for key, val in DEFAULTS.items():
            st.session_state[key] = val
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# PAGE: ADMIN & ANALYTICS (DEMO)
# ══════════════════════════════════════════════════════════════════════════
elif page == "🛠️ Admin & Analytics (Demo)":
    st.markdown("### 🛠️ Admin & Analytics Dashboard")
    st.warning("Demo only: this shows data from **your current browser session**, not real multi-user data. A production version needs a database and real authentication to aggregate across users.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Session XP", st.session_state.xp)
    c2.metric("Roadmaps generated", 1 if st.session_state.roadmap else 0)
    c3.metric("Resumes built", 1 if st.session_state.resume_data else 0)
    c4.metric("Applications tracked", len(st.session_state.applications))
    st.markdown("#### Feature usage (this session)")
    usage = {
        "Chat messages": len(st.session_state.chat_history) // 2,
        "Mock interviews": len(st.session_state.mock_interview_log),
        "Planner tasks": len(st.session_state.planner_tasks),
        "Goals": len(st.session_state.goals),
        "Projects": len(st.session_state.projects),
        "Certificates": len(st.session_state.certificates),
    }
    st.bar_chart(usage)

st.markdown("---")
st.caption("PathPilot AI · Built with Streamlit · This is a demo product — see the top of app.py for production notes.")
