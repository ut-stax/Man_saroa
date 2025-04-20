import streamlit as st
import requests
import json
from fpdf import FPDF
from io import BytesIO
import os
import re
import sqlite3
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
import hashlib
import shutil

st.set_page_config(page_title="Manasāroha: Your Mental Wellness Companion", page_icon="🧘", layout="wide")

API_KEY = "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"

# ---------- ✅ SQLite Deployment Fix START ----------
# Use /tmp for deployment on Streamlit Cloud to avoid database reset
ORIGINAL_DB_PATH = "manasaroha.db"
TEMP_DB_PATH = os.path.join("/tmp", "manasaroha.db")

if not os.path.exists(TEMP_DB_PATH):
    shutil.copy(ORIGINAL_DB_PATH, TEMP_DB_PATH)
# ---------- ✅ SQLite Deployment Fix END ----------

# SQLite Database Connection
def get_db_connection():
    conn = sqlite3.connect(TEMP_DB_PATH)
    return conn

# Add user_type column if it does not exist
def add_user_type_column():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN user_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # If the column already exists, do nothing
    conn.close()

# Create tables if they don't exist
def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(''' 
    CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        name TEXT,
        password TEXT,
        last_activity_date TEXT,
        streak INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        age INTEGER,
        user_type TEXT
    )''')

    cursor.execute(''' 
    CREATE TABLE IF NOT EXISTS mood_data (
        timestamp TEXT,
        name TEXT,
        age INTEGER,
        user_type TEXT,
        mood_text TEXT,
        mood_result TEXT,
        recommendation TEXT,
        mood_score INTEGER
    )''')

    conn.commit()
    conn.close()

# Initialize the database
add_user_type_column()
create_tables()


def extract_mood_score(mood_result):
    mood_map = {
        "happy": 5,
        "joy": 5,
        "content": 4,
        "neutral": 3,
        "anxious": 2,
        "sad": 1,
        "depressed": 1
    }
    for mood, score in mood_map.items():
        if mood in mood_result.lower():
            return score
    return 3

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed):
    return hash_password(password) == hashed

def signup(email, name, password, age, user_type):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email=?", (email,))
    if cursor.fetchone():
        return "User already exists!"

    hashed_pw = hash_password(password)
    cursor.execute("INSERT INTO users (email, name, password, age, user_type) VALUES (?, ?, ?, ?, ?)",
                   (email, name, hashed_pw, age, user_type))
    conn.commit()
    conn.close()
    return "Account created successfully!"

def login(email, password):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE email=?", (email,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password(password, user[2]):
        return {
            "name": user[1],
            "age": user[6],
            "user_type": user[7]
        }
    return None

def save_mood_to_db(name, age, user_type, mood_text, mood_result, recommendation):
    conn = get_db_connection()
    cursor = conn.cursor()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mood_score = extract_mood_score(mood_result)

    cursor.execute(''' 
        INSERT INTO mood_data (timestamp, name, age, user_type, mood_text, mood_result, recommendation, mood_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (timestamp, name, age, user_type, mood_text, mood_result, recommendation, mood_score))

    conn.commit()
    conn.close()

def update_xp_and_streak(email):
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT * FROM users WHERE email=?", (email,))
    user = cursor.fetchone()

    if user:
        last_date, streak, xp = user[3], user[4], user[5]
        if last_date != today:
            streak = streak + 1 if last_date == (datetime.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d") else 1
            xp += 10
            cursor.execute(''' 
                UPDATE users SET last_activity_date=?, streak=?, xp=? WHERE email=?''',
                (today, streak, xp, email))
            conn.commit()
    conn.close()

def load_mood_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM mood_data")
    data = cursor.fetchall()
    conn.close()
    return pd.DataFrame(data, columns=["Timestamp", "Name", "Age", "UserType", "MoodText", "MoodResult", "Recommendation", "MoodScore"])

@st.cache_resource
def get_mood_analysis(user_input):
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "model": "deepseek/deepseek-r1:free",
                "messages": [
                    {"role": "system", "content": "You are an AI assistant that detects user mood based on text input and provides recommendations."},
                    {"role": "user", "content": user_input}
                ]})
        )
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"API Error: {str(e)}"

@st.cache_resource
def get_mood_recommendation(user_input):
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps({
                "model": "deepseek/deepseek-r1:free",
                "messages": [
                    {"role": "system", "content": "You are a movie recommendation system. Based only on the user's mood, recommend one movie, one song, and one book."},
                    {"role": "user", "content": user_input}
                ]})
        )
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"API Error: {str(e)}"

def generate_pdf_report(name, age, user_type, mood, recommendation):
    pdf = FPDF()
    pdf.add_page()

    try:
        font_path_regular = "DejaVuSans.ttf"
        font_path_bold = "DejaVuSans-Bold.ttf"

        pdf.add_font("DejaVu", "", font_path_regular, uni=True)
        pdf.add_font("DejaVu", "B", font_path_bold, uni=True)

        pdf.set_font("DejaVu", "B", 16)
        pdf.cell(200, 10, txt="Manasāroha Mood Report", ln=True, align="C")

        pdf.set_font("DejaVu", size=12)
        pdf.ln(10)
        pdf.cell(200, 10, txt=f"Name: {name}", ln=True)
        pdf.cell(200, 10, txt=f"Age: {age}", ln=True)
        pdf.cell(200, 10, txt=f"User Type: {user_type}", ln=True)
        pdf.ln(5)

        mood_clean = re.sub(r'[^\x00-\x7F]+', '', mood)
        recommendation_clean = re.sub(r'[^\x00-\x7F]+', '', recommendation)

        pdf.multi_cell(0, 10, f"Mood Analysis:\n{mood_clean}")
        pdf.ln(5)
        pdf.multi_cell(0, 10, f"Recommendations:\n{recommendation_clean}")

        output_pdf = pdf.output(dest='S').encode('latin-1')
        buffer = BytesIO(output_pdf)
        buffer.seek(0)
        return buffer

    except Exception as e:
        st.error(f"PDF Generation Error: {e}")
        return BytesIO()

# New progress functions
def get_user_progress(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT xp, streak FROM users WHERE email=?", (email,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (0, 0)

def calculate_level_and_progress(xp):
    level = xp // 100
    current_xp = xp % 100
    return level, current_xp

def get_badges(xp, streak):
    badges = []
    if xp >= 100:
        badges.append(("💡 XP Beginner", "Earned 100+ XP"))
    if xp >= 500:
        badges.append(("🌟 XP Master", "Earned 500+ XP"))
    if streak >= 3:
        badges.append(("🔥 Streak Starter", "3-day streak"))
    if streak >= 7:
        badges.append(("🌈 Streak Champion", "7-day streak"))
    return badges

def show_progress_section(email):
    xp, streak = get_user_progress(email)
    level, current_xp = calculate_level_and_progress(xp)
    badges = get_badges(xp, streak)

    st.markdown(""" 
        <style>
            .progress-container {
                background: linear-gradient(145deg, #ffffff, #e6e6e6);
                border-radius: 15px;
                padding: 20px;
                margin-bottom: 20px;
                animation: fadeIn 1.2s ease-in-out;
            }
            @keyframes fadeIn {
                0% { opacity: 0; transform: translateY(20px); }
                100% { opacity: 1; transform: translateY(0); }
            }
            .xp-bar {
                height: 25px;
                border-radius: 10px;
                background: #dcdcdc;
                overflow: hidden;
                margin-top: 10px;
                margin-bottom: 10px;
            }
            .xp-fill {
                height: 100%;
                background: linear-gradient(to right, #5dade2, #3498db);
                text-align: right;
                color: white;
                padding-right: 10px;
                line-height: 25px;
                font-weight: bold;
                animation: fillBar 1.5s ease-out forwards;
                width: 0;
            }
            @keyframes fillBar {
                from { width: 0; }
                to { width: """ + str(current_xp) + """%; }
            }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="progress-container">
        <h3>Welcome back, <span style="color:#2c3e50">{st.session_state['user_name']} 👋</span></h3>
        <p><strong>Level:</strong> {level}</p>
        <p><strong>XP:</strong> {xp}/100</p>
        <div class="xp-bar">
            <div class="xp-fill" style="width:{current_xp}%;">{current_xp} XP</div>
        </div>
        <p><strong>Daily Streak:</strong> 🔥 {streak} days</p>
    """, unsafe_allow_html=True)

    if badges:
        st.markdown("<p><strong>🏅 Badges Earned:</strong></p>", unsafe_allow_html=True)
        for icon, desc in badges:
            st.markdown(f"<p>{icon} {desc}</p>", unsafe_allow_html=True)
    else:
        st.markdown("<p>No badges yet. Keep going! 🚀</p>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

# UI Styling
st.markdown("""
    <style>
        body { background-color: #eef2f7; }
        h1, h2, h3 { color: #2c3e50; }
        .stButton>button {
            background-color: #5dade2;
            color: white;
            padding: 10px 24px;
            border-radius: 10px;
            font-size: 18px;
        }
        .stTextInput>div>input {
            border-radius: 8px;
        }
    </style>
""", unsafe_allow_html=True)

# Sidebar Auth
st.sidebar.title("🔐 Authentication")
auth_mode = st.sidebar.radio("Login or Sign Up", ["Login", "Sign Up"])
email_input = st.sidebar.text_input("Email")
password_input = st.sidebar.text_input("Password", type="password")

if st.session_state.get("authenticated"):
    if st.sidebar.button("Log Out"):
        st.session_state.clear()
        st.rerun()
else:
    if auth_mode == "Sign Up":
        name_input = st.sidebar.text_input("Name")
        age_input = st.sidebar.number_input("Age", min_value=10, max_value=100, value=25)
        user_type_input = st.sidebar.selectbox("User Type", ["Student", "Working", "Other"])
        if st.sidebar.button("Create Account"):
            if email_input and password_input and name_input:
                msg = signup(email_input, name_input, password_input, age_input, user_type_input)
                if "success" in msg.lower():
                    st.sidebar.success(msg)
                else:
                    st.sidebar.error(msg)
            else:
                st.sidebar.warning("Fill all fields to sign up.")
    else:
        if st.sidebar.button("Log In"):
            user_data = login(email_input, password_input)
            if user_data:
                st.session_state["user_name"] = user_data["name"]
                st.session_state["user_age"] = user_data["age"]
                st.session_state["user_type"] = user_data["user_type"]
                st.session_state["authenticated"] = True
                st.sidebar.success(f"Welcome, {user_data['name']}!")
            else:
                st.sidebar.error("Invalid credentials.")

if st.session_state.get("authenticated"):
    st.title("🌿 **Manasāroha** — *Your Sacred Space for Mental Wellness*")
    st.subheader("💫 *Where your thoughts are heard, and your soul finds calm.*")

    user_name = st.session_state["user_name"]
    user_age = st.session_state["user_age"]
    user_type = st.session_state["user_type"]

    st.write("✨ Hello, **{}** 👋".format(user_name))
    st.markdown("**Take a deep breath...** inhale peace, exhale worry. 🌬️\n\nWhenever you're ready, share what's on your heart. 💌")

    user_input = st.text_area("🧠 **What's on your mind today?** Let your thoughts flow freely:")

    if st.button("🔍 **Analyze My Emotions**"):
        if user_input:
            mood_result = get_mood_analysis(user_input)
            recommendation = get_mood_recommendation(user_input)

            st.success("🪷 *Your emotions have been gently understood.*")
            st.markdown("### 🧘‍♀️ **Emotional Reflection**")
            st.info(f"💭 *{mood_result}*")

            st.markdown("### 🌈 **Personalized Soulful Guidance**")
            st.info(f"📌 *{recommendation}*")

            save_mood_to_db(user_name, user_age, user_type, user_input, mood_result, recommendation)
            update_xp_and_streak(email_input)

            st.markdown("---")
            st.markdown("### 📖 **Your Recent Mood Journals**")
            df = load_mood_data()
            safe_df = df[["Timestamp", "MoodResult", "Recommendation", "MoodScore"]]
            st.dataframe(safe_df.tail(), use_container_width=True)

            st.download_button("📥 **Download My Wellness Report (PDF)**",
                               generate_pdf_report(user_name, user_age, user_type, mood_result, recommendation),
                               file_name="mood_report.pdf",
                               mime="application/pdf")
        else:
            st.warning("🌱 *Aapka mann kya keh raha hai? Batayein...*")

    st.markdown("---")
    st.markdown("### 💖 Crafted with love, empathy, and a touch of serenity by **UKT**")
    st.markdown("*You are not alone. Manasāroha walks with you, one breath at a time.* 🌌")
