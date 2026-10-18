import os
import json
import random
import re
from datetime import datetime, timedelta, time, date

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# =========================================================
# CONFIGURATION
# =========================================================
st.set_page_config(page_title="AI TNPSC Exam Preparation System", layout="wide")
st.title("📚 AI-Based TNPSC Government Exam Preparation System")

USERS_FILE = "users.json"
CURRENT_USER_FILE = "current_user.json"
PROGRESS_FILE = "progress.json"

# Timings: schedule topic-test slot is 15 minutes; interactive topic-test timer
# remains 10 minutes, matching the existing app. Daily test is 30 minutes.
TOPIC_TEST_TIMER_SECONDS = 10 * 60
DAILY_TEST_TIMER_SECONDS = 30 * 60

# =========================================================
# JSON HELPERS
# =========================================================
def read_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_users():
    return read_json(USERS_FILE, {})


def save_users(users):
    write_json(USERS_FILE, users)


def load_current_user():
    return read_json(CURRENT_USER_FILE, None)


def save_current_user(user):
    write_json(CURRENT_USER_FILE, user)


def load_progress_for_user(email):
    return read_json(PROGRESS_FILE, {}).get(str(email).lower(), [])


def save_progress_for_user(email, progress):
    all_progress = read_json(PROGRESS_FILE, {})
    all_progress[str(email).lower()] = progress
    write_json(PROGRESS_FILE, all_progress)


# =========================================================
# SESSION STATE DEFAULTS + LOGIN RESTORE
# =========================================================
defaults = {
    "logged_in": False, "user": None, "page": "study",
    "selected_topic": None, "selected_subject": None,
    "selected_exam": "TNPSC", "selected_group": None,
    "selected_plan": [], "schedule": [], "study_started": False,
    "current_questions": None, "test_answers": {},
    "test_submitted": False, "test_start_time": None,
    "test_result": None, "progress": [], "test_attempt_id": 0,
    "daily_questions": None, "daily_answers": {}, "daily_start_time": None,
    "daily_submitted": False, "daily_result": None, "daily_attempt_id": 0,
    "daily_generation_method": None, "daily_generation_message": None,
    "topic_test_seen_questions": [],
    "notification_log": [], "notification_history": [],
    "voice_enabled": False, "login_checked": False,
    "progress_owner": None, "schedule_date": None,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value.copy() if isinstance(value, (list, dict)) else value

if not st.session_state.login_checked:
    saved_user = load_current_user()
    if isinstance(saved_user, dict) and saved_user.get("email"):
        st.session_state.user = saved_user
        st.session_state.logged_in = True
        st.session_state.progress = load_progress_for_user(saved_user["email"])
        st.session_state.progress_owner = saved_user["email"].lower()
    st.session_state.login_checked = True

# =========================================================
# LOGIN / CREATE ACCOUNT (NAME + EMAIL, NO PASSWORD)
# =========================================================
if not st.session_state.logged_in:
    st.title("🔐 Welcome")
    login_col, create_col = st.columns(2)
    with login_col:
        st.subheader("Login")
        login_name = st.text_input("Enter Name", key="login_name")
        login_email = st.text_input("Enter Email", key="login_email")
        if st.button("Login", use_container_width=True):
            name = login_name.strip()
            email = login_email.strip().lower()
            if not name or not email or "@" not in email:
                st.warning("Please enter a valid name and email address.")
            else:
                users = load_users()
                # Name + email login as requested; existing accounts are retained.
                user_data = users.get(email, {"name": name, "email": email})
                user_data["name"] = name
                user_data["email"] = email
                users[email] = user_data
                save_users(users)
                save_current_user(user_data)
                st.session_state.user = user_data
                st.session_state.logged_in = True
                st.session_state.progress = load_progress_for_user(email)
                st.session_state.progress_owner = email
                st.rerun()
    with create_col:
        st.subheader("Create Account")
        new_name = st.text_input("User Name", key="new_name")
        new_email = st.text_input("Email ID", key="new_email")
        if st.button("Create Account", key="create_account_button", use_container_width=True):
            name = new_name.strip()
            email = new_email.strip().lower()
            users = load_users()
            if not name or not email:
                st.warning("Please enter both your name and email.")
            elif "@" not in email or "." not in email.split("@")[-1]:
                st.warning("Please enter a valid email address.")
            elif email in users:
                st.error("This email already has an account. Please log in.")
            else:
                user_data = {"name": name, "email": email}
                users[email] = user_data
                save_users(users)
                save_current_user(user_data)
                save_progress_for_user(email, [])
                st.session_state.user = user_data
                st.session_state.logged_in = True
                st.session_state.progress = []
                st.session_state.progress_owner = email
                st.session_state.page = "study"
                st.rerun()
    st.stop()

# =========================================================
# LOAD TOPICS AND QUESTIONS
# =========================================================
def load_questions_csv(path):
    """Load questions even when a question sentence contains unquoted commas.

    Expected columns: exam, group, subject, topic, question, option1..option4, answer.
    If a row has extra comma-separated fields, the first four fields are metadata,
    the last five fields are options/answer, and the middle fields are rejoined as
    the question text. Also remove Markdown ** markers from options/answers.
    """
    import csv

    expected = ["exam", "group", "subject", "topic", "question",
                "option1", "option2", "option3", "option4", "answer"]
    rows = []
    malformed_rows = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return pd.DataFrame(columns=expected), 0
        header = [item.strip().lower() for item in header]
        # Map a standard header by name, but keep a fallback for the expected order.
        if all(name in header for name in expected):
            indices = [header.index(name) for name in expected]
            for raw in reader:
                if not raw or not any(str(v).strip() for v in raw):
                    continue
                if len(raw) == len(header):
                    values = [raw[i] for i in indices]
                elif len(raw) > len(header) and len(header) == 10:
                    # Common malformed case: commas in the question were not quoted.
                    values = raw[:4] + [",".join(raw[4:-5])] + raw[-5:]
                    malformed_rows += 1
                else:
                    malformed_rows += 1
                    continue
                if len(values) != 10:
                    malformed_rows += 1
                    continue
                rows.append(dict(zip(expected, values)))
        else:
            # Expected column order fallback for a CSV with a slightly different header.
            for raw in reader:
                if not raw or not any(str(v).strip() for v in raw):
                    continue
                if len(raw) == 10:
                    values = raw
                elif len(raw) > 10:
                    values = raw[:4] + [",".join(raw[4:-5])] + raw[-5:]
                    malformed_rows += 1
                else:
                    malformed_rows += 1
                    continue
                rows.append(dict(zip(expected, values)))
    result = pd.DataFrame(rows, columns=expected)
    for col in expected:
        result[col] = result[col].fillna("").astype(str).str.strip()
    # Remove bold Markdown markers used in the pasted dataset (e.g. **600).
    for col in ["option1", "option2", "option3", "option4", "answer"]:
        result[col] = result[col].str.replace("**", "", regex=False).str.strip()
    result["question"] = result["question"].str.replace("**", "", regex=False).str.strip()
    return result, malformed_rows


try:
    df = pd.read_csv("data/topics.csv", encoding="utf-8-sig")
    q_df, repaired_question_rows = load_questions_csv("data/questions.csv")
except FileNotFoundError as exc:
    st.error(f"Required file not found: {exc.filename}. Please check the data folder.")
    st.stop()
except Exception as exc:
    st.error(f"Could not load CSV files: {exc}")
    st.stop()

df.columns = df.columns.str.strip()
q_df.columns = q_df.columns.str.strip().str.lower()
required_topic_columns = {"exam", "group", "Subject", "Topic", "file_name"}
required_question_columns = {"exam", "group", "subject", "topic", "question", "option1", "option2", "option3", "option4", "answer"}
missing_topic_columns = required_topic_columns - set(df.columns)
missing_question_columns = required_question_columns - set(q_df.columns)
if missing_topic_columns:
    st.error("Missing columns in data/topics.csv: " + ", ".join(sorted(missing_topic_columns)))
    st.stop()
if missing_question_columns:
    st.error("Missing columns in data/questions.csv: " + ", ".join(sorted(missing_question_columns)))
    st.stop()

for col in ["exam", "group", "Subject", "Topic", "file_name"]:
    df[col] = df[col].fillna("").astype(str).str.strip()
for col in required_question_columns:
    q_df[col] = q_df[col].fillna("").astype(str).str.strip()

# This version is intentionally limited to TNPSC, as requested.
df = df[df["exam"].str.casefold() == "tnpsc"].copy()
q_df = q_df[q_df["exam"].str.casefold() == "tnpsc"].copy()
if df.empty:
    st.error("No TNPSC topics were found in data/topics.csv. Check the exam column spelling.")
    st.stop()

user_email = st.session_state.user.get("email", "").strip().lower()
if st.session_state.progress_owner != user_email:
    st.session_state.progress = load_progress_for_user(user_email)
    st.session_state.progress_owner = user_email


# =========================================================
# WEAK-AREA ANALYSIS (Decision Tree ML + safe cold-start fallback)
# =========================================================
def _key_text(value):
    return " ".join(str(value if value is not None else "").split()).casefold()


def _record_date_iso(record):
    value = str(record.get("date_iso", "")).strip()
    if value:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    label = str(record.get("date", "")).strip()
    for fmt in ("%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(label, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


_WEAK_ML_FEATURE_NAMES = [
    "latest_accuracy",
    "mean_last_3_accuracy",
    "previous_accuracy",
    "accuracy_trend",
    "attempt_count",
    "current_weak_streak",
    "last_3_volatility",
]


def _weak_area_features(history, threshold=60.0):
    """Build features using only assessment results available up to this point."""
    import statistics

    values = [float(item.get("percentage", 0.0)) for item in history]
    if not values:
        values = [0.0]
    latest = values[-1]
    previous = values[-2] if len(values) >= 2 else latest
    last_three = values[-3:]
    weak_streak = 0
    for value in reversed(values):
        if value < threshold:
            weak_streak += 1
        else:
            break
    return {
        "latest_accuracy": latest,
        "mean_last_3_accuracy": sum(last_three) / len(last_three),
        "previous_accuracy": previous,
        "accuracy_trend": latest - previous,
        "attempt_count": min(len(values), 100),
        "current_weak_streak": min(weak_streak, 20),
        "last_3_volatility": statistics.pstdev(last_three) if len(last_three) > 1 else 0.0,
    }


def _collect_topic_assessments(progress_records, through_date_iso, group_filter=None):
    """Aggregate daily Topic Test and topic-wise Daily Test results by group/subject/topic."""
    buckets = {}
    requested_group = _key_text(group_filter) if group_filter else ""

    def add_score(day_iso, subject, topic, score, total, record_group=""):
        subject = str(subject or "").strip()
        topic = str(topic or "").strip()
        if not subject or not topic or not day_iso or day_iso > through_date_iso:
            return

        normalized_group = _key_text(record_group or group_filter or "")
        if requested_group and normalized_group and normalized_group != requested_group:
            return
        try:
            score_value = max(0, int(float(score)))
            total_value = max(0, int(float(total)))
        except (TypeError, ValueError):
            return
        if total_value <= 0:
            return

        key = (day_iso, normalized_group, _key_text(subject), _key_text(topic))
        if key not in buckets:
            buckets[key] = {
                "date_iso": day_iso,
                "group": record_group or group_filter or "",
                "subject": subject,
                "topic": topic,
                "score": 0,
                "total": 0,
            }
        buckets[key]["score"] += min(score_value, total_value)
        buckets[key]["total"] += total_value

    for record in progress_records or []:
        record_day = _record_date_iso(record)
        if not record_day or record_day > through_date_iso:
            continue

        record_type = _key_text(record.get("test_type", "Topic Test"))
        record_group = str(record.get("group", group_filter or ""))
        if record_type == "topic test":
            add_score(
                record_day,
                record.get("subject"),
                record.get("topic"),
                record.get("score"),
                record.get("total"),
                record_group,
            )
        elif record_type == "daily test":
            # Current format stores topic-wise scores in topic_results. The
            # alternate topic_breakdown key supports older saved test records.
            topic_rows = record.get("topic_results") or record.get("topic_breakdown") or []
            for topic_result in topic_rows:
                add_score(
                    record_day,
                    topic_result.get("subject"),
                    topic_result.get("topic"),
                    topic_result.get("score", topic_result.get("correct")),
                    topic_result.get("total"),
                    topic_result.get("group", record_group),
                )

    histories = {}
    for (day_iso, group_key, subject_key, topic_key), item in buckets.items():
        item["percentage"] = round(item["score"] / item["total"] * 100, 2) if item["total"] else 0.0
        history_key = (group_key, subject_key, topic_key)
        histories.setdefault(history_key, []).append(item)

    for entries in histories.values():
        entries.sort(key=lambda item: item["date_iso"])
    return histories


def _train_weak_area_model(topic_histories, threshold=60.0):
    """Train a Decision Tree to predict whether the next assessment will be weak.

    Each training example uses a topic's previous history as features; the label
    is whether its next recorded assessment was below the threshold. With too
    little historical data or only one label class, the caller uses the safe
    rule-based fallback instead of pretending a reliable ML model exists.
    """
    feature_rows = []
    labels = []
    for history in topic_histories.values():
        history = sorted(history, key=lambda item: item["date_iso"])
        # The first assessment has no previous performance features, so training
        # starts at the second recorded assessment for each topic.
        for next_index in range(1, len(history)):
            previous_history = history[:next_index]
            feature_rows.append(_weak_area_features(previous_history, threshold))
            labels.append(1 if float(history[next_index].get("percentage", 0.0)) < threshold else 0)

    if len(feature_rows) < 8 or len(set(labels)) < 2:
        return None, "fallback_insufficient_history", len(feature_rows)

    try:
        from sklearn.tree import DecisionTreeClassifier

        model = DecisionTreeClassifier(
            max_depth=3,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
        )
        training_frame = pd.DataFrame(feature_rows, columns=_WEAK_ML_FEATURE_NAMES)
        model.fit(training_frame, labels)
        return model, "decision_tree_ml", len(feature_rows)
    except ImportError:
        return None, "fallback_scikit_learn_missing", len(feature_rows)
    except Exception:
        # A model failure must not break schedule generation or Daily Test output.
        return None, "fallback_model_error", len(feature_rows)


def get_weak_area_recommendations(progress_records, target_date_iso, group_filter=None, threshold=60.0):
    """Recommend topics using Decision Tree ML when possible and a rule fallback.

    A topic is recommended if its observed accuracy on target_date is below the
    threshold OR the Decision Tree predicts the next assessment is likely to be
    weak. Recommendations are scoped to the current user's records and selected
    group. Existing low-scoring recommendations remain intact for reliability.
    """
    histories = _collect_topic_assessments(progress_records, target_date_iso, group_filter)
    model, model_status, training_samples = _train_weak_area_model(histories, threshold)

    weak = []
    for key, history in histories.items():
        if not history:
            continue
        latest = history[-1]
        # Only recommend topics that have a recorded result for the requested day.
        if latest.get("date_iso") != target_date_iso:
            continue

        current_is_weak = float(latest.get("percentage", 0.0)) < threshold
        ml_probability = None
        ml_predicts_weak = False
        if model is not None:
            features = _weak_area_features(history, threshold)
            feature_frame = pd.DataFrame([features], columns=_WEAK_ML_FEATURE_NAMES)
            try:
                prediction = int(model.predict(feature_frame)[0])
                class_positions = list(model.classes_)
                ml_probability = (
                    float(model.predict_proba(feature_frame)[0][class_positions.index(1)])
                    if 1 in class_positions else 0.0
                )
                ml_predicts_weak = prediction == 1 and ml_probability >= 0.5
            except Exception:
                ml_probability = None
                ml_predicts_weak = False

        # Keep topics with low observed accuracy; ML may additionally flag a topic
        # whose recent history suggests that the next assessment could be weak.
        if not current_is_weak and not ml_predicts_weak:
            continue

        if current_is_weak and ml_predicts_weak:
            reason = "Current accuracy is below 60%; the ML model also predicts continued difficulty."
            method = "Rule + Decision Tree ML"
        elif current_is_weak:
            reason = "Current accuracy is below 60%."
            method = "Rule-based current-score check"
        else:
            reason = "Decision Tree ML predicts risk of a weak next assessment."
            method = "Decision Tree ML prediction"

        item = {
            "group": latest.get("group", group_filter or ""),
            "subject": latest["subject"],
            "topic": latest["topic"],
            "correct": int(latest["score"]),
            "total": int(latest["total"]),
            "percentage": float(latest["percentage"]),
            "status": "Weak" if current_is_weak else "ML-predicted Weak",
            "recommendation_reason": reason,
            "recommendation_method": method,
            "ml_probability": round(ml_probability * 100, 2) if ml_probability is not None else None,
            "ml_model_status": model_status,
            "ml_training_samples": training_samples,
        }
        weak.append(item)

    weak.sort(
        key=lambda item: (
            0 if item["status"] == "Weak" else 1,
            item["percentage"],
            -item["total"],
            _key_text(item["subject"]),
            _key_text(item["topic"]),
        )
    )

    # This status is used only to explain the weak-area engine on its existing page.
    st.session_state.weak_area_ml_status = model_status
    st.session_state.weak_area_ml_training_samples = training_samples
    return weak


# =========================================================
# NOTIFICATION + VOICE ALERT HELPERS
# =========================================================
def queue_alert(alert_id, message):
    """Queue an alert only once per generated schedule/session event."""
    logged = st.session_state.notification_log
    if alert_id not in logged:
        logged.append(alert_id)
        st.session_state.notification_log = logged[-1000:]
        st.session_state.notification_history.append({
            "time": datetime.now().strftime("%I:%M:%S %p"),
            "message": message,
        })
        st.session_state.notification_history = st.session_state.notification_history[-100:]
        st.session_state.pending_voice_messages = st.session_state.get("pending_voice_messages", []) + [message]


def schedule_datetime(value):
    """Read stored ISO timestamps or legacy 12-hour schedule strings."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        try:
            return datetime.combine(date.today(), datetime.strptime(str(value), "%I:%M %p").time())
        except (ValueError, TypeError):
            return None


def send_voice_alerts(messages, show_notification=True):
    if not messages:
        return
    # Browser notification and speech. Browser may require voice to be enabled
    # by the user once due to autoplay restrictions.
    payload = json.dumps(messages, ensure_ascii=False)
    notifications_enabled = "true" if show_notification else "false"
    html = f"""
    <script>
    const messages = {payload};
    try {{
      if ({notifications_enabled} && ('Notification' in window && Notification.permission === 'granted')) {{
        messages.forEach(msg => new Notification('TNPSC Study Reminder', {{ body: msg }}));
      }}
      if ('speechSynthesis' in window) {{
        window.speechSynthesis.cancel();
        let delay = 0;
        messages.forEach(msg => {{
          setTimeout(() => {{
            const utterance = new SpeechSynthesisUtterance(msg);
            utterance.lang = 'en-IN';
            utterance.rate = 0.95;
            window.speechSynthesis.speak(utterance);
          }}, delay);
          delay += 3500;
        }});
      }}
    }} catch (e) {{ console.log(e); }}
    </script>
    """
    components.html(html, height=0, width=0)


def process_schedule_alerts():
    if not st.session_state.schedule:
        return
    now = datetime.now()
    for item in st.session_state.schedule:
        start_dt = schedule_datetime(item.get("start_iso", item.get("start")))
        end_dt = schedule_datetime(item.get("end_iso", item.get("end")))
        if not start_dt or not end_dt:
            continue
        # Ignore old schedule entries from previous days.
        if start_dt.date() != now.date():
            continue
        label = item.get("label", "Scheduled activity")
        kind = item.get("kind", "")
        base = item.get("id", f"{start_dt.isoformat()}_{kind}_{label}")
        if start_dt <= now < start_dt + timedelta(seconds=12):
            if kind == "study":
                queue_alert(f"{base}_start", f"Your {label.replace(' (Study)', '')} study session has started.")
            elif kind == "topic_test":
                queue_alert(f"{base}_start", f"Your {label.replace(' - Topic Test (15 Minutes)', '')} topic test is scheduled to start now.")
            elif kind == "daily_test":
                queue_alert(f"{base}_start", "Your daily test is starting now. All the best!")
            elif kind == "break":
                msg = "It is time for your break."
                if "Breakfast" in label:
                    msg = "It is time for your breakfast break."
                elif "Lunch" in label:
                    msg = "It is time for your lunch break."
                elif "Snacks" in label:
                    msg = "It is time for your snacks break."
                elif "Before Daily Test" in label:
                    msg = "It is time for your break before the daily test."
                queue_alert(f"{base}_start", msg)
        if kind == "study":
            # Speak/remind the student 10 minutes before the first auto-added weak-area session.
            if item.get("is_first_weak_area"):
                weak_names = st.session_state.get("scheduled_weak_topic_names", [])
                reminder_dt = start_dt - timedelta(minutes=10)
                if weak_names and reminder_dt <= now < reminder_dt + timedelta(seconds=12):
                    names_text = ", ".join(weak_names)
                    queue_alert(
                        f"{base}_weak_area_reminder",
                        f"Yesterday's weak areas were: {names_text}. Today, revise these topics first. They are added to the beginning of your schedule."
                    )
            reminder_dt = end_dt - timedelta(minutes=10)
            if reminder_dt <= now < reminder_dt + timedelta(seconds=12):
                topic_label = label.replace(" (Study)", "")
                queue_alert(f"{base}_10min", f"You have 10 minutes left to complete {topic_label} study.")
        if end_dt <= now < end_dt + timedelta(seconds=12):
            if kind == "study":
                queue_alert(f"{base}_end", f"Your {label.replace(' (Study)', '')} study session is completed. Get ready for your topic test.")
            elif kind == "topic_test":
                queue_alert(f"{base}_end", "Your scheduled topic-test slot is completed. Please continue to the next topic.")
            elif kind == "break":
                queue_alert(f"{base}_end", "Your break is over. Please get ready for the next scheduled activity.")
            elif kind == "daily_test":
                queue_alert(f"{base}_end", "Your daily test time is over.")

# Run event checks on every app rerun; 10-second refresh keeps schedule reminders timely.
st_autorefresh(interval=10_000, key="schedule_automation_refresh")
process_schedule_alerts()

# =========================================================
# SIDEBAR NAVIGATION
# =========================================================
st.sidebar.success(f"Welcome {st.session_state.user.get('name', 'User')}")
st.sidebar.header("Navigation")
if st.sidebar.button("🏠 Study / Preparation Page", use_container_width=True):
    st.session_state.page = "study"
    st.rerun()
if st.sidebar.button("📊 View Progress Report", use_container_width=True):
    st.session_state.page = "progress"
    st.rerun()
if st.sidebar.button("🚪 Logout", use_container_width=True):
    st.session_state.logged_in = False
    st.session_state.user = None
    st.session_state.page = "study"
    if os.path.exists(CURRENT_USER_FILE):
        os.remove(CURRENT_USER_FILE)
    st.rerun()
st.sidebar.markdown("---")

exam = "TNPSC"
st.sidebar.info("Selected Exam: TNPSC")
group_values = sorted(df["group"].dropna().unique().tolist())
if not group_values:
    st.error("No TNPSC groups found in data/topics.csv.")
    st.stop()
current_group = st.session_state.selected_group
if current_group not in group_values:
    current_group = group_values[0]
group = st.sidebar.selectbox("Select Group", group_values, index=group_values.index(current_group), key="group_select")
if group != st.session_state.selected_group:
    st.session_state.selected_group = group
    st.session_state.selected_topic = None
    st.session_state.selected_subject = None
    st.session_state.selected_plan = []
    st.session_state.schedule = []

topic_rows = df[df["group"] == group].copy()
subjects = sorted(topic_rows["Subject"].dropna().unique().tolist())
if not subjects:
    st.error("No subjects found for this TNPSC group.")
    st.stop()

# Yesterday's low-scoring topics are automatically placed at the top of today's plan.
yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
yesterday_weak_areas = get_weak_area_recommendations(
    st.session_state.progress, yesterday_iso, group_filter=group, threshold=60.0
)
weak_plan = [(item["subject"], item["topic"]) for item in yesterday_weak_areas]
weak_keys = {(_key_text(sub), _key_text(top)) for sub, top in weak_plan}
st.session_state.yesterday_weak_areas = yesterday_weak_areas

if weak_plan:
    st.sidebar.warning("🔁 Yesterday's weak topics will be added automatically as the first study sessions.")
    for weak_item in yesterday_weak_areas:
        st.sidebar.caption(f"• {weak_item['subject']} — {weak_item['topic']} ({weak_item['percentage']}%)")
else:
    st.sidebar.caption("No weak topics were recorded yesterday for this group.")

# Show which weak-area engine is currently active. This does not change the schedule logic.
weak_ml_status = st.session_state.get("weak_area_ml_status", "fallback_insufficient_history")
weak_ml_samples = int(st.session_state.get("weak_area_ml_training_samples", 0) or 0)
if weak_ml_status == "decision_tree_ml":
    st.sidebar.caption(f"Weak-area engine: Decision Tree ML ({weak_ml_samples} historical training examples).")
elif weak_ml_status == "fallback_scikit_learn_missing":
    st.sidebar.caption("Weak-area engine is using the safe score-rule fallback. Install scikit-learn to enable Decision Tree ML.")
else:
    st.sidebar.caption("Weak-area engine uses the score-rule fallback until enough historical results are available for ML.")

num_topics = int(st.sidebar.number_input(
    "Number of Additional Topics", min_value=1, max_value=10, value=3, step=1,
    key="number_additional_topics",
    help="These are extra topics you choose. Yesterday's weak topics are inserted before them automatically."
))
manual_plan = []
for i in range(num_topics):
    subject_key = f"subject_select_{i}"
    topic_key = f"topic_select_{i}"
    old_subject = st.session_state.get(subject_key)
    subject_index = subjects.index(old_subject) if old_subject in subjects else 0
    sub = st.sidebar.selectbox(f"Additional Subject {i + 1}", subjects, index=subject_index, key=subject_key)
    topic_options_all = sorted(topic_rows.loc[topic_rows["Subject"] == sub, "Topic"].dropna().unique().tolist())
    topic_options = [top for top in topic_options_all if (_key_text(sub), _key_text(top)) not in weak_keys]
    if not topic_options:
        topic_options = topic_options_all
    if not topic_options:
        continue
    old_topic = st.session_state.get(topic_key)
    topic_index = topic_options.index(old_topic) if old_topic in topic_options else 0
    top = st.sidebar.selectbox(f"Additional Topic {i + 1}", topic_options, index=topic_index, key=topic_key)
    if (_key_text(sub), _key_text(top)) not in weak_keys:
        manual_plan.append((sub, top))

# Put weak-area revision topics first; then append the new topics selected by the user.
selected_plan = []
seen_plan_keys = set()
for sub, top in weak_plan + manual_plan:
    item_key = (_key_text(sub), _key_text(top))
    if item_key not in seen_plan_keys:
        selected_plan.append((sub, top))
        seen_plan_keys.add(item_key)
st.session_state.selected_plan = selected_plan
end_time = st.sidebar.time_input("End Time", value=time(21, 0), key="end_time")

# Voice is user-activated once to satisfy browser autoplay rules.
if st.sidebar.button("🔊 Enable Voice Alerts", use_container_width=True):
    st.session_state.voice_enabled = True
    components.html("""<script>if ('Notification' in window && Notification.permission === 'default') { Notification.requestPermission(); } if ('speechSynthesis' in window) { const u = new SpeechSynthesisUtterance('Voice alerts are enabled.'); u.lang='en-IN'; speechSynthesis.speak(u); }</script>""", height=0)
    st.sidebar.success("Voice alerts enabled for this browser session.")

# Render any fresh alerts and send speech once.
pending = st.session_state.get("pending_voice_messages", [])
if pending:
    for message in pending:
        st.toast("🔔 " + message)
    if st.session_state.voice_enabled:
        send_voice_alerts(pending)
    st.session_state.pending_voice_messages = []

if st.session_state.notification_history:
    with st.sidebar.expander("🔔 Recent Notifications", expanded=False):
        for alert in reversed(st.session_state.notification_history[-8:]):
            st.caption(f"{alert['time']} — {alert['message']}")

# =========================================================
# SCHEDULE GENERATION
# =========================================================
if st.sidebar.button("📅 Generate Schedule", use_container_width=True):
    st.session_state.page = "study"
    st.session_state.schedule = []
    st.session_state.notification_log = []
    st.session_state.notification_history = []
    st.session_state.pending_voice_messages = []
    start_dt = datetime.combine(date.today(), time(5, 30))
    end_dt = datetime.combine(date.today(), end_time)
    topic_count = len(selected_plan)
    topic_test_minutes = 15
    between_break_minutes = 15
    pre_daily_break_minutes = 30
    daily_test_minutes = 30
    if end_dt <= start_dt:
        st.error("End Time must be later than 05:30 AM.")
    elif topic_count == 0:
        st.warning("Please select at least one topic.")
    else:
        daily_test_start = end_dt - timedelta(minutes=daily_test_minutes)
        daily_break_start = daily_test_start - timedelta(minutes=pre_daily_break_minutes)
        total_topic_test_minutes = topic_count * topic_test_minutes
        total_between_break_minutes = max(0, topic_count - 1) * between_break_minutes
        study_window_minutes = (daily_break_start - start_dt).total_seconds() / 60
        study_minutes_available = study_window_minutes - total_topic_test_minutes - total_between_break_minutes
        if study_minutes_available < topic_count * 15:
            st.error("Not enough time for selected topics, topic-test slots, breaks, and Daily Test. Choose fewer topics or a later End Time.")
        else:
            per_topic_minutes = study_minutes_available / topic_count
            current = start_dt
            schedule_rows = []
            weak_names = [f"{sub} - {top}" for sub, top in weak_plan]
            st.session_state.scheduled_weak_topic_names = weak_names
            for i, (sub, top) in enumerate(selected_plan):
                is_weak = (_key_text(sub), _key_text(top)) in weak_keys
                study_end = current + timedelta(minutes=per_topic_minutes)
                schedule_rows.append({
                    "start_dt": current, "end_dt": study_end,
                    "label": f"{sub} - {top} (Study)", "kind": "study",
                    "is_weak_area": is_weak,
                    "is_first_weak_area": bool(is_weak and (i == 0 or not any((_key_text(a), _key_text(b)) in weak_keys for a, b in selected_plan[:i])))
                })
                current = study_end
                test_end = current + timedelta(minutes=topic_test_minutes)
                schedule_rows.append({"start_dt": current, "end_dt": test_end, "label": f"{top} - Topic Test (15 Minutes)", "kind": "topic_test"})
                current = test_end
                if i < topic_count - 1:
                    break_end = current + timedelta(minutes=between_break_minutes)
                    schedule_rows.append({"start_dt": current, "end_dt": break_end, "label": "Break Time (15 Minutes)", "kind": "break"})
                    current = break_end
            # Fixed pre-daily-test break and daily test, ending exactly at selected End Time.
            schedule_rows.append({"start_dt": daily_break_start, "end_dt": daily_test_start, "label": "Break Before Daily Test (30 Minutes)", "kind": "break"})
            schedule_rows.append({"start_dt": daily_test_start, "end_dt": end_dt, "label": "Daily Test (30 Minutes)", "kind": "daily_test"})
            # Preserve schedule timestamps for automation and human-readable labels for display.
            st.session_state.schedule = []
            for idx, row in enumerate(schedule_rows):
                st.session_state.schedule.append({
                    "id": f"schedule_{date.today().isoformat()}_{idx}",
                    "start_iso": row["start_dt"].isoformat(),
                    "end_iso": row["end_dt"].isoformat(),
                    "start": row["start_dt"].strftime("%I:%M %p"),
                    "end": row["end_dt"].strftime("%I:%M %p"),
                    "label": row["label"], "kind": row["kind"],
                    "is_weak_area": bool(row.get("is_weak_area", False)),
                    "is_first_weak_area": bool(row.get("is_first_weak_area", False)),
                })
            st.session_state.schedule_date = date.today().isoformat()
            if weak_names:
                weak_names_text = ", ".join(weak_names)
                queue_alert(
                    f"weak_area_plan_created_{date.today().isoformat()}",
                    f"Yesterday's weak areas are: {weak_names_text}. Please revise these topics first today. They have been added to the beginning of your schedule."
                )
            st.success("Schedule generated. Keep this app open to receive scheduled reminders.")
            st.rerun()

# =========================================================
# STUDY PAGE
# =========================================================
if st.session_state.page == "study":
    st.markdown("### ▶ Start Study")
    if yesterday_weak_areas:
        st.markdown("### 🔁 Yesterday's Weak Areas — Added First to Today's Schedule")
        st.caption("Topics with low observed accuracy, plus topics the Decision Tree predicts may remain difficult, are automatically placed before your additional selected topics when sufficient history is available.")
        for item in yesterday_weak_areas:
            st.warning(f"{item['subject']} — {item['topic']} | {item['correct']}/{item['total']} correct ({item['percentage']}%) — {item.get('recommendation_reason', 'revise this topic today.')}")
    today_weak_areas = get_weak_area_recommendations(
        st.session_state.progress, date.today().isoformat(), group_filter=group, threshold=60.0
    )
    if today_weak_areas:
        st.markdown("### 📌 Today's Weak Area Recommendation")
        for item in today_weak_areas:
            st.warning(f"{item['subject']} — {item['topic']} | {item['correct']}/{item['total']} correct ({item['percentage']}%) — {item.get('recommendation_method', 'Score analysis')}")
    if st.button("▶ Start Study"):
        st.session_state.study_started = True
        st.rerun()
    if st.session_state.schedule:
        st.subheader("📅 Your Smart Schedule")
        for item in st.session_state.schedule:
            icon = "🔁" if item.get("is_weak_area") else {"study": "📘", "topic_test": "📝", "break": "☕", "daily_test": "🎯"}.get(item["kind"], "•")
            st.write(f"{icon} {item['start']} – {item['end']} → {item['label']}")
        st.success("✅ Smart Schedule Generated Successfully!")
        st.caption("Automatic schedule alerts check every 10 seconds. Enable Voice Alerts from the sidebar and keep the app/browser open.")
    st.markdown("---")
    st.subheader("📚 Selected Study Topics")
    for i, (sub, top) in enumerate(selected_plan):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"📘 {sub} - {top}")
        with col2:
            if st.button("📖 Open Study Material", key=f"open_material_{i}"):
                st.session_state.selected_topic = top
                st.session_state.selected_subject = sub
                st.session_state.selected_exam = "TNPSC"
                st.session_state.selected_group = group
                st.session_state.page = "study"
                st.rerun()

    if st.session_state.selected_topic and st.session_state.page == "study":
        st.markdown("---")
        st.subheader("📖 Study Material")
        selected_topic = st.session_state.selected_topic
        selected_subject = st.session_state.selected_subject
        material_row = df[
            (df["exam"].str.casefold() == "tnpsc")
            & (df["group"].str.casefold() == str(group).casefold())
            & (df["Subject"].str.casefold() == str(selected_subject).casefold())
            & (df["Topic"].str.casefold() == str(selected_topic).casefold())
        ]
        if material_row.empty:
            st.error("This topic was not found in data/topics.csv.")
        else:
            file_name = material_row.iloc[0]["file_name"]
            folder_group = str(group).replace(" ", "_")
            file_path = os.path.join("study_material", folder_group, str(selected_subject), str(file_name))
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as material_file:
                    st.markdown(material_file.read())
                with open(file_path, "rb") as material_file:
                    st.download_button("⬇️ Download Study Material", data=material_file, file_name=os.path.basename(file_path), key="download_material")
                if st.button("📝 Start Topic Test", key="start_topic_test"):
                    st.session_state.page = "test"
                    st.session_state.test_topic = selected_topic
                    st.session_state.test_subject = selected_subject
                    st.session_state.test_exam = "TNPSC"
                    st.session_state.test_group = group
                    st.session_state.test_start_time = datetime.now()
                    st.session_state.test_answers = {}
                    st.session_state.test_submitted = False
                    st.session_state.test_result = None
                    st.session_state.current_questions = None
                    st.session_state.test_attempt_id += 1
                    st.session_state.topic_test_alerts = []
                    st.session_state.topic_countdown_seconds = []
                    st.rerun()
            else:
                st.error(f"Study material file not found: {file_path}")

def normalize_match(value):
    """Normalize text for matching and duplicate detection."""
    return " ".join(str(value if value is not None else "").replace("\ufeff", "").split()).casefold()


def read_topic_material(subject, topic, selected_group):
    """Read only the study material belonging to a selected schedule topic."""
    rows = df[
        (df["exam"].str.casefold() == "tnpsc")
        & (df["group"].map(normalize_match) == normalize_match(selected_group))
        & (df["Subject"].map(normalize_match) == normalize_match(subject))
        & (df["Topic"].map(normalize_match) == normalize_match(topic))
    ]
    if rows.empty:
        return ""
    group_folder = str(selected_group).replace(" ", "_")
    path = os.path.join("study_material", group_folder, str(subject), str(rows.iloc[0]["file_name"]))
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


import re
from typing import List

TAMIL_CHAR_PATTERN = re.compile(r"[\u0B80-\u0BFF]")


def detect_material_language(material):
    """Detect the main script of a study-material file without using file/topic labels."""
    text = str(material or "")
    tamil_chars = len(TAMIL_CHAR_PATTERN.findall(text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    return "ta" if tamil_chars >= 8 and tamil_chars >= max(1, latin_chars * 0.18) else "en"


def mcq_identity(item):
    """Stable identity for a question and its four options, independent of option order."""
    question = normalize_match(item.get("question", ""))
    options = sorted(normalize_match(item.get(f"option{i}", "")) for i in range(1, 5))
    return question + " || " + " || ".join(options)


def split_material_into_sentences(material):
    """Extract content-bearing facts from English or Tamil TXT study material."""
    text = str(material or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    cleaned_lines = []
    heading_pattern = re.compile(
        r"^(?:UNIT|CHAPTER|PART|SECTION)\s*\d*\b|^(?:CONTENTS?|STUDY MATERIAL|MODEL TEST|PRACTICE QUESTIONS?)\b|"
        r"^(?:பகுதி|அலகு|பாடம்|தலைப்பு|பொருளடக்கம்)\s*\d*\b",
        flags=re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        line = re.sub(r"[*#>`_]+", " ", raw_line).strip(" \t-:;•●")
        line = re.sub(r"^\s*(?:[-*•●]+|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        if heading_pattern.match(line):
            continue
        # Ignore stand-alone file-part headings such as Part1, Unit 2, or Biology 1.
        if re.fullmatch(r"(?:part|unit|chapter|section)\s*\d+[A-Za-z]*", line, flags=re.IGNORECASE):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,35}\s*\d+", line) and len(line.split()) <= 4:
            continue
        if re.match(r"^(?:in TNPSC|according to this chapter|according to this material|in this topic|the following questions)\b", line, flags=re.IGNORECASE):
            continue
        # All-capital short headings are not factual content.
        letters = re.sub(r"[^A-Za-z]", "", line)
        if letters and letters == letters.upper() and len(line.split()) <= 14 and not re.search(r"[.!?]", line):
            continue
        cleaned_lines.append(line)

    facts, seen = [], set()
    for line in cleaned_lines:
        pieces = re.split(r"(?<=[.!?।])\s+|\s+[•●]\s+", line)
        for piece in pieces:
            sentence = re.sub(r"\s+", " ", piece).strip(" \t-:;•●")
            sentence = re.sub(r"^\d+[.)]\s*", "", sentence).strip()
            if not sentence or "?" in sentence:
                continue
            is_tamil = bool(TAMIL_CHAR_PATTERN.search(sentence))
            words = re.findall(r"[\w\u0B80-\u0BFF]+", sentence, flags=re.UNICODE)
            min_length = 12 if is_tamil else 24
            if len(sentence) < min_length or len(sentence) > 500 or len(words) < 3:
                continue
            if not re.search(r"[A-Za-z\u0B80-\u0BFF]", sentence):
                continue
            if re.match(r"^(?:unit|chapter|part|section|contents?|study material)\b", sentence, flags=re.IGNORECASE):
                continue
            key = normalize_match(sentence)
            if key not in seen:
                seen.add(key)
                facts.append(sentence)
    return facts


def _clean_content_phrase(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" \t\n.,;:!?-–—")
    value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.IGNORECASE)
    return value


def _english_subject(sentence):
    """Extract a likely named subject/entity from a factual sentence."""
    match = re.match(
        r"^(.{2,90}?)\s+(?:is|are|was|were|refers to|means|is called|are called|known as|consists of|includes|contains|located in|founded|established|introduced|formed|born|used for|produces|has|have)\b",
        sentence,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = _clean_content_phrase(match.group(1))
        candidate = re.sub(r"^(?:the|a|an)\s+", "", candidate, flags=re.IGNORECASE)
        if 1 <= len(candidate.split()) <= 8:
            return candidate
    # A short named phrase is still useful when the sentence has no common relation verb.
    match = re.match(r"^([A-Z][A-Za-z0-9&'’.-]*(?:\s+[A-Z][A-Za-z0-9&'’.-]*){0,4})\b", sentence)
    return _clean_content_phrase(match.group(1)) if match else ""


def _build_option_set(answer, pool, salt, fallback_pool=None):
    """Return four unique options drawn from the same source material when possible."""
    answer = _clean_content_phrase(answer)
    answer_key = normalize_match(answer)
    candidates = []
    # Prefer compact answer candidates (names, terms, values); only use full source
    # statements if the study material does not provide enough short choices.
    for source_pool in (pool or [], fallback_pool or []):
        for item in source_pool:
            item = _clean_content_phrase(item)
            key = normalize_match(item)
            if not item or key == answer_key or key in {normalize_match(v) for v in candidates}:
                continue
            if len(item) > 150:
                continue
            candidates.append(item)
        if len(candidates) >= 3:
            break
    rng = random.Random(str(salt) + "|" + answer_key)
    rng.shuffle(candidates)
    chosen = candidates[:3]
    if len(chosen) < 3:
        # Keep options in the content language; do not inject subject, topic, or file labels.
        fillers = [
            "the correct answer" if not TAMIL_CHAR_PATTERN.search(answer) else "சரியான விடை",
            "the given statement" if not TAMIL_CHAR_PATTERN.search(answer) else "கொடுக்கப்பட்ட கூற்று",
            "none of these" if not TAMIL_CHAR_PATTERN.search(answer) else "இவற்றில் எதுவுமில்லை",
            "all of these" if not TAMIL_CHAR_PATTERN.search(answer) else "இவை அனைத்தும்",
        ]
        for item in fillers:
            if normalize_match(item) != answer_key and normalize_match(item) not in {normalize_match(v) for v in chosen}:
                chosen.append(item)
            if len(chosen) == 3:
                break
    return [answer] + chosen if len(chosen) == 3 else []


def _make_false_statement(statement, language, replacements, salt):
    """Create a plausible false statement by changing one value/relation in a source fact."""
    original = statement.strip().rstrip(".!?।")
    years = re.findall(r"\b(?:1[5-9]\d{2}|20\d{2})\b", original)
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", original)
    if years or numbers:
        old = (years or numbers)[0]
        if old.isdigit():
            n = int(old)
            replacement = str(n + (salt + 1) * (5 if len(old) == 4 else 2))
        else:
            replacement = str(salt + 2)
        return original.replace(old, replacement, 1) + ("." if language == "en" else ".")

    if language == "ta":
        replacements_ta = [
            ("ஆகும்", "அல்ல"), ("என்பது", "என்பதல்ல"), ("உள்ளது", "இல்லை"),
            ("இருந்தது", "இருக்கவில்லை"), ("அழைக்கப்படுகிறது", "அழைக்கப்படுவதில்லை"),
            ("செய்யப்படுகிறது", "செய்யப்படுவதில்லை"), ("கொண்டுள்ளது", "கொண்டிருக்கவில்லை"),
        ]
        for source, changed in replacements_ta:
            if source in original and changed not in original:
                return original.replace(source, changed, 1) + "."
        return original + " அல்ல."

    for pattern, changed in [
        (r"\bis\b", "is not"), (r"\bare\b", "are not"), (r"\bwas\b", "was not"),
        (r"\bwere\b", "were not"), (r"\bhas\b", "does not have"),
        (r"\bhave\b", "do not have"), (r"\bcan\b", "cannot"),
    ]:
        match = re.search(pattern, original, flags=re.IGNORECASE)
        if match and not re.search(r"\bnot\b", original, flags=re.IGNORECASE):
            return original[:match.start()] + changed + original[match.end():] + "."
    entities = [v for v in replacements if normalize_match(v) not in normalize_match(original) and len(v) > 2]
    if entities:
        candidate = entities[salt % len(entities)]
        words = re.findall(r"[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3}", original)
        if words:
            return original.replace(words[0], candidate, 1) + "."
    return "It is not correct that " + original[0].lower() + original[1:] + "."


def generate_nlp_mcqs_from_material(material, subject, topic, count=50):
    """Create fresh, TNPSC-style MCQs from the actual selected TXT content, in its language.

    Question stems never mention the file name, study material, chapter, or selected topic label.
    The selected text supplies facts and answer candidates; existing questions.csv entries are not reused.
    """
    facts = split_material_into_sentences(material)
    if len(facts) < 4:
        return []

    language = detect_material_language(material)
    tamil = language == "ta"
    all_text = " ".join(facts)
    years = list(dict.fromkeys(re.findall(r"\b(?:1[5-9]\d{2}|20\d{2})\b", all_text)))
    number_values = list(dict.fromkeys(re.findall(r"\b\d+(?:\.\d+)?\b", all_text)))
    entities = []
    for fact in facts:
        candidate = _english_subject(fact) if not tamil else ""
        if candidate and normalize_match(candidate) not in {normalize_match(v) for v in entities}:
            entities.append(candidate)
    # Extract compact noun-like phrases for Tamil material; retain Tamil script and avoid file labels.
    if tamil:
        for fact in facts:
            match = re.match(r"^(.{1,70}?)\s*(?:என்பது|எனப்படும்|என அழைக்கப்படுகிறது|என்று அழைக்கப்படுகிறது|ஆகும்|எனப்படுகிறது)", fact)
            if match:
                candidate = _clean_content_phrase(match.group(1))
                if candidate and normalize_match(candidate) not in {normalize_match(v) for v in entities}:
                    entities.append(candidate)
    if len(entities) < 4:
        # Gather short capitalized phrases only from text, not from selected labels.
        for fact in facts:
            for candidate in re.findall(r"\b[A-Z][A-Za-z0-9&'’.-]*(?:\s+[A-Z][A-Za-z0-9&'’.-]*){0,3}\b", fact):
                candidate = _clean_content_phrase(candidate)
                if len(candidate) > 2 and normalize_match(candidate) not in {normalize_match(v) for v in entities}:
                    entities.append(candidate)

    output, seen_questions = [], set()
    relation_candidates = []
    for fact in facts:
        if not tamil:
            # Entity-class question: "Which of the following is a primary activity?"
            match = re.match(r"^(.{2,90}?)\s+(is|are|was|were)\s+(a|an|the)\s+(.{3,160})$", fact, flags=re.IGNORECASE)
            if match:
                answer = _clean_content_phrase(match.group(1))
                article = match.group(3).lower()
                description = _clean_content_phrase(match.group(4))
                description = re.sub(r"\bbut\b.*$", "", description, flags=re.IGNORECASE).strip(" ,.;:")
                if description and len(description) <= 120 and len(answer.split()) <= 8:
                    if article == "the" and re.match(r"capital of\b", description, flags=re.IGNORECASE):
                        question = f"What is the {description}?"
                    else:
                        question = f"Which of the following is {article} {description}?"
                    options = _build_option_set(answer, entities, f"entity:{fact}", facts)
                    if options:
                        relation_candidates.append((question, options, answer, fact))

            # Ask for a year/value directly if the source makes a clear claim.
            year_match = re.search(r"\b(?:1[5-9]\d{2}|20\d{2})\b", fact)
            subject_phrase = _english_subject(fact)
            low_fact = fact.casefold()
            if year_match and subject_phrase and any(term in low_fact for term in ("founded", "established", "formed", "introduced", "born", "started", "signed", "passed", "occurred")):
                year = year_match.group(0)
                grammatical_subject = subject_phrase
                if any(term in subject_phrase.casefold() for term in ("national congress", "constitution", "commission", "committee", "assembly", "government act", "association", "organization", "organisation", "movement")) and not subject_phrase.casefold().startswith("the "):
                    grammatical_subject = "the " + subject_phrase
                if "born" in low_fact:
                    question = f"In which year was {subject_phrase} born?"
                elif "established" in low_fact:
                    question = f"In which year was {grammatical_subject} established?"
                elif "founded" in low_fact:
                    question = f"In which year was {grammatical_subject} founded?"
                elif "formed" in low_fact:
                    question = f"In which year was {grammatical_subject} formed?"
                elif "introduced" in low_fact:
                    question = f"In which year was {grammatical_subject} introduced?"
                elif "passed" in low_fact:
                    question = f"In which year was the relevant law passed?"
                else:
                    question = f"In which year did the event involving {subject_phrase} take place?"
                options = _build_option_set(year, [v for v in years if v != year], f"year:{fact}", [str(int(year) + d) for d in (-20, -10, -5, 5, 10, 20)])
                if options:
                    relation_candidates.append((question, options, year, fact))

            # Relations where the complement itself is the answer.
            relation_match = re.match(r"^(.{2,80}?)\s+(?:is|was|were|are)\s+(?:located in|situated in|known as|called|used for|famous for|responsible for)\s+(.{3,120})$", fact, flags=re.IGNORECASE)
            if relation_match:
                entity = _clean_content_phrase(relation_match.group(1))
                answer = _clean_content_phrase(relation_match.group(2))
                relation = re.search(r"\b(located in|situated in|known as|called|used for|famous for|responsible for)\b", fact, flags=re.IGNORECASE).group(1).lower()
                if relation in {"located in", "situated in"}:
                    question = f"Where is {entity} located?"
                elif relation in {"known as", "called"}:
                    question = f"What is {entity} known as?"
                elif relation == "used for":
                    question = f"What is {entity} used for?"
                else:
                    question = f"Which of the following is stated about {entity}?"
                options = _build_option_set(answer, facts, f"relation:{fact}", entities)
                if options:
                    relation_candidates.append((question, options, answer, fact))
        else:
            # Tamil fill-in-the-blank question from the actual sentence, not its heading.
            match = re.match(r"^(.{1,70}?)\s*(என்பது|எனப்படும்|என அழைக்கப்படுகிறது|என்று அழைக்கப்படுகிறது|எனப்படுகிறது)(.*)$", fact)
            if match:
                answer = _clean_content_phrase(match.group(1))
                blanked = fact.replace(match.group(1), "________", 1)
                question = f"பின்வரும் கூற்றில் காலியிடத்தை நிரப்புக: {blanked}"
                options = _build_option_set(answer, entities, f"ta:{fact}", facts)
                if options:
                    relation_candidates.append((question, options, answer, fact))
            # If a Tamil fact defines a number/count, ask for that value directly.
            quantity_match = re.search(r"(\d+(?:\.\d+)?)", fact)
            if quantity_match and re.search(r"(?:ஆகும்|உள்ளது|உள்ளன|கொண்டுள்ளது|கொண்டுள்ளன)", fact):
                value = quantity_match.group(1)
                blanked = fact[:quantity_match.start()] + "______" + fact[quantity_match.end():]
                question = f"காலியிடத்தை நிரப்புக: {blanked}"
                numeric_pool = [v for v in number_values if v != value]
                numeric_pool += [str(float(value) + d).rstrip("0").rstrip(".") for d in (-4, -2, -1, 1, 2, 4)]
                options = _build_option_set(value, numeric_pool, f"ta-count:{fact}")
                if options:
                    relation_candidates.append((question, options, value, fact))
            # If the sentence contains a year, ask a direct year-based question in Tamil.
            year_match = re.search(r"\b(?:1[5-9]\d{2}|20\d{2})\b", fact)
            if year_match:
                year = year_match.group(0)
                blanked = fact[:year_match.start()] + "______" + fact[year_match.end():]
                question = f"சரியான ஆண்டைத் தேர்ந்தெடுக்கவும்: {blanked}"
                options = _build_option_set(year, [v for v in years if v != year], f"ta-year:{fact}", [str(int(year) + d) for d in (-20, -10, -5, 5, 10, 20)])
                if options:
                    relation_candidates.append((question, options, year, fact))

    def add_mcq(question, options, answer, salt):
        if len(output) >= count:
            return
        question = re.sub(r"\s+", " ", str(question)).strip()
        cleaned_options, option_keys = [], set()
        for option in options:
            option = re.sub(r"\s+", " ", str(option)).strip()
            key = normalize_match(option)
            if option and key not in option_keys:
                cleaned_options.append(option)
                option_keys.add(key)
        answer_key = normalize_match(answer)
        if not question or answer_key not in option_keys or len(cleaned_options) != 4:
            return
        identity = normalize_match(question) + " || " + " || ".join(sorted(option_keys))
        if identity in seen_questions:
            return
        seen_questions.add(identity)
        random.Random(str(salt) + "|" + identity).shuffle(cleaned_options)
        output.append({
            "exam": "TNPSC", "group": str(st.session_state.get("selected_group") or ""),
            "subject": subject, "topic": topic, "question": question,
            "option1": cleaned_options[0], "option2": cleaned_options[1],
            "option3": cleaned_options[2], "option4": cleaned_options[3],
            "answer": next(option for option in cleaned_options if normalize_match(option) == answer_key),
        })

    # Direct content-derived questions come first.
    for index, (question, options, answer, source_fact) in enumerate(relation_candidates):
        add_mcq(question, options, answer, f"relation:{index}:{source_fact}")
        if len(output) >= count:
            return output[:count]

    # TNPSC-style statement questions use one source fact and three deliberately altered options.
    en_stems = [
        "Which of the following statements is correct?",
        "Select the correct statement from the options given below.",
        "Identify the correctly stated option.",
        "Choose the correct statement.",
        "Which statement is accurate?",
    ]
    ta_stems = [
        "பின்வரும் கூற்றுகளில் சரியானது எது?",
        "சரியான கூற்றைத் தேர்ந்தெடுக்கவும்.",
        "பின்வருவனவற்றில் சரியான விடையைத் தேர்ந்தெடுக்கவும்.",
        "சரியான தகவலைக் குறிப்பிடும் கூற்று எது?",
    ]
    for index, fact in enumerate(facts):
        if len(output) >= count:
            break
        # Choose three false variants; prefer different transformations.
        false_options, seen_false = [], set()
        for variant in range(1, 9):
            candidate = _make_false_statement(fact, language, entities + [f for f in facts if len(f) < 80], variant)
            key = normalize_match(candidate)
            if key and key != normalize_match(fact) and key not in seen_false:
                seen_false.add(key)
                false_options.append(candidate)
            if len(false_options) >= 3:
                break
        if len(false_options) < 3:
            continue
        stem = (ta_stems if tamil else en_stems)[index % (len(ta_stems) if tamil else len(en_stems))]
        add_mcq(stem, [fact] + false_options[:3], fact, f"statement:{index}:{fact}")

    # Year-based cloze questions add an additional standard exam-paper pattern.
    for index, fact in enumerate(facts):
        if len(output) >= count:
            break
        match = re.search(r"\b(?:1[5-9]\d{2}|20\d{2})\b", fact)
        if not match:
            continue
        value = match.group(0)
        blanked = fact[:match.start()] + "______" + fact[match.end():]
        stem = f"Choose the correct year to complete the statement: {blanked}" if not tamil else f"சரியான ஆண்டைத் தேர்ந்தெடுத்து கூற்றை நிறைவு செய்க: {blanked}"
        options = _build_option_set(value, [v for v in years if v != value], f"yearfill:{index}", [str(int(value) + d) for d in (-20, -10, -5, 5, 10, 20)])
        if options:
            add_mcq(stem, options, value, f"yearfill:{index}:{fact}")

    return output[:count]


def build_daily_test_questions(chosen_topics, selected_group, question_frame, target=50, excluded_questions=None):
    """Build a mixed Daily Test exclusively from today's selected TXT materials."""
    if not chosen_topics:
        return pd.DataFrame(), "NLP from selected TXT materials", "Select topics in today's schedule first."

    excluded_raw = {str(q).strip() for q in (excluded_questions or []) if str(q).strip()}
    excluded_ids = {q for q in excluded_raw if " || " in q}
    excluded_stems = {normalize_match(q) for q in excluded_raw if " || " not in q}
    candidates_by_topic = []
    missing_materials = []
    generation_errors = []

    for subject, topic in chosen_topics:
        material = read_topic_material(subject, topic, selected_group)
        if not material:
            missing_materials.append(f"{subject} - {topic}")
            candidates_by_topic.append([])
            continue
        try:
            candidates = generate_nlp_mcqs_from_material(material, subject, topic, count=target * 2)
            # Exclude exact questions already shown in Topic Tests and remove duplicates.
            unique_candidates, local_seen = [], set()
            for item in candidates:
                identity = mcq_identity(item)
                stem_key = normalize_match(item.get("question", ""))
                if not stem_key or identity in excluded_ids or stem_key in excluded_stems or identity in local_seen:
                    continue
                local_seen.add(identity)
                unique_candidates.append(item)
            candidates_by_topic.append(unique_candidates)
        except Exception as exc:
            generation_errors.append(f"{subject} - {topic}: {exc}")
            candidates_by_topic.append([])

    # Interleave topics so the daily test is mixed rather than grouped by subject.
    generated, seen_questions = [], set(excluded_ids)
    max_per_round = max((len(items) for items in candidates_by_topic), default=0)
    for round_index in range(max_per_round):
        for items in candidates_by_topic:
            if round_index < len(items) and len(generated) < target:
                item = items[round_index]
                key = mcq_identity(item)
                if key and key not in seen_questions:
                    seen_questions.add(key)
                    generated.append(item)
            if len(generated) >= target:
                break
        if len(generated) >= target:
            break

    frame = pd.DataFrame(generated[:target])
    if len(frame) >= target:
        message = "50 new questions generated from today's selected TXT study materials using NLP."
    elif generation_errors:
        message = f"Only {len(frame)} questions could be generated. Please check the selected material files."
    elif missing_materials:
        message = f"Only {len(frame)} questions could be generated. Missing study material: " + ", ".join(missing_materials)
    else:
        message = (
            f"Only {len(frame)} questions could be generated from the available selected-topic content. "
            "Add more explanatory content to the selected TXT files to create 50 unique questions."
        )
    return frame, "NLP from selected TXT materials", message

# =========================================================
# TOPIC TEST (10-minute timer; alerts at 7, 5, 1 minutes remaining)
# =========================================================
if st.session_state.page == "test":
    st.title("📝 Topic Test")
    topic = st.session_state.get("test_topic")
    subject = st.session_state.get("test_subject")
    test_group = st.session_state.get("test_group", group)
    if not topic or not subject:
        st.error("Topic or subject is missing. Return to Study Material and start the test again.")
        if st.button("🔙 Back to Study"):
            st.session_state.page = "study"
            st.rerun()
        st.stop()
    if st.session_state.current_questions is None:
        subject_key = normalize_match(subject)
        topic_key = normalize_match(topic)
        group_key = normalize_match(test_group)
        normalized_subjects = q_df["subject"].map(normalize_match)
        normalized_topics = q_df["topic"].map(normalize_match)
        normalized_groups = q_df["group"].map(normalize_match)
        mask = (normalized_subjects == subject_key) & (normalized_topics == topic_key) & (normalized_groups == group_key)
        matched = q_df.loc[mask].copy()
        if matched.empty:
            # Fallback if the group label in questions.csv differs from topics.csv.
            matched = q_df[(normalized_subjects == subject_key) & (normalized_topics == topic_key)].copy()
        if matched.empty:
            st.error(f"No questions found for {subject} → {topic}. Check data/questions.csv spelling.")
            if st.button("🔙 Back to Study Material"):
                st.session_state.page = "study"
                st.rerun()
            st.stop()
        st.session_state.current_questions = matched.sample(n=min(20, len(matched))).reset_index(drop=True)
        seen_questions = st.session_state.get("topic_test_seen_questions", [])
        seen_questions.extend(
            mcq_identity(row.to_dict()) for _, row in st.session_state.current_questions.iterrows()
        )
        st.session_state.topic_test_seen_questions = list(dict.fromkeys(seen_questions))
    questions = st.session_state.current_questions
    if not st.session_state.test_submitted:
        if st.session_state.test_start_time is None:
            st.session_state.test_start_time = datetime.now()
        elapsed = (datetime.now() - st.session_state.test_start_time).total_seconds()
        remaining = max(0, TOPIC_TEST_TIMER_SECONDS - int(elapsed))
        st.subheader(f"Topic: {topic}")
        st.warning(f"⏳ Time Left: {remaining // 60:02}:{remaining % 60:02}")
        # Existing 7/5/1-minute reminders. Keep the final 15-second
        # countdown separate so it is spoken only (no repeated toast/browser notifications).
        topic_alerts = st.session_state.get("topic_test_alerts", [])
        for mins in (7, 5, 1):
            if remaining <= mins * 60 and mins not in topic_alerts:
                topic_alerts.append(mins)
                queue_alert(f"topic_test_{st.session_state.test_attempt_id}_{mins}min", f"You have {mins} minute{'s' if mins != 1 else ''} left to complete your {topic} topic test.")
        st.session_state.topic_test_alerts = topic_alerts
        if 1 <= remaining <= 15:
            countdown_seconds = st.session_state.get("topic_countdown_seconds", [])
            if remaining not in countdown_seconds:
                countdown_seconds.append(remaining)
                st.session_state.topic_countdown_seconds = countdown_seconds
                if st.session_state.voice_enabled:
                    send_voice_alerts([str(remaining)], show_notification=False)
        pending_test = st.session_state.get("pending_voice_messages", [])
        if pending_test:
            for msg in pending_test:
                st.toast("🔔 " + msg)
            if st.session_state.voice_enabled:
                send_voice_alerts(pending_test)
            st.session_state.pending_voice_messages = []
        st_autorefresh(interval=1000, key=f"topic_test_timer_{st.session_state.test_attempt_id}")
        for i, row in questions.iterrows():
            st.write(f"Q{i + 1}. {row['question']}")
            options = [row["option1"], row["option2"], row["option3"], row["option4"]]
            st.session_state.test_answers[i] = st.radio("Select Answer", options, key=f"answer_{st.session_state.test_attempt_id}_{i}", index=None)
        if st.button("✅ Submit Test", key="submit_topic_test") or remaining <= 0:
            score = 0
            wrong_count = 0
            unanswered_count = 0
            for i, row in questions.iterrows():
                user_answer = st.session_state.test_answers.get(i)
                if user_answer is None or not str(user_answer).strip():
                    unanswered_count += 1
                elif str(user_answer).strip() == str(row["answer"]).strip():
                    score += 1
                else:
                    wrong_count += 1
            total = len(questions)
            # Keep these counts for the result screen only; do not save them to progress.json.
            st.session_state.test_correct_count = score
            st.session_state.test_wrong_count = wrong_count
            st.session_state.test_unanswered_count = unanswered_count
            percentage = round((score / total) * 100, 2) if total else 0.0
            status = "Strong" if percentage >= 80 else "Average" if percentage >= 60 else "Weak"
            result = {
                "date": datetime.now().strftime("%d %b %Y"),
                "date_iso": date.today().isoformat(),
                "group": test_group,
                "subject": subject,
                "topic": topic,
                "score": score,
                "total": total,
                "percentage": percentage,
                "status": status,
                "test_type": "Topic Test",
            }
            st.session_state.test_result = result
            st.session_state.test_submitted = True
            st.session_state.progress.append(result.copy())
            save_progress_for_user(user_email, st.session_state.progress)
            queue_alert(f"topic_test_{st.session_state.test_attempt_id}_finished", f"Your {topic} topic test is completed.")
            st.rerun()
    if st.session_state.test_submitted:
        result = st.session_state.get("test_result")
        if result:
            st.success("✅ Test Submitted Successfully!")
            c1, c2, c3 = st.columns(3)
            c1.metric("Score", f"{result['score']} / {result['total']}")
            c2.metric("Accuracy", f"{result['percentage']}%")
            c3.metric("Status", result["status"])
            r1, r2, r3 = st.columns(3)
            r1.metric("✅ Correct Answers", st.session_state.get("test_correct_count", result["score"]))
            r2.metric("❌ Wrong Answers", st.session_state.get("test_wrong_count", 0))
            r3.metric("⏭️ Not Answered", st.session_state.get("test_unanswered_count", 0))
            st.markdown("### 📊 Your Topic Test Result")
            st.write("📚 Topic:", result["topic"])
            st.write("📘 Subject:", result["subject"])
            for i, row in questions.iterrows():
                user_answer = st.session_state.test_answers.get(i)
                correct_answer = row["answer"]
                with st.expander(f"Question {i + 1}"):
                    st.write(row["question"])
                    st.write("Your Answer:", user_answer if user_answer is not None else "Not answered")
                    st.write("Correct Answer:", correct_answer)
                    if user_answer is not None and str(user_answer).strip() == str(correct_answer).strip():
                        st.success("Correct Answer")
                    else:
                        st.error("Wrong Answer")
            if st.button("🔙 Back to Study", key="back_from_test"):
                st.session_state.page = "study"
                st.session_state.current_questions = None
                st.session_state.test_start_time = None
                st.session_state.test_submitted = False
                st.session_state.test_result = None
                st.session_state.test_answers = {}
                st.rerun()

# =========================================================
# DAILY TEST (50 questions / 30 minutes; AI + NLP generation)
# =========================================================
if st.session_state.page == "daily_test":
    st.title("🎯 Daily Test")
    if st.session_state.daily_questions is None:
        chosen_topics = st.session_state.selected_plan or []
        selected_group_for_daily = str(st.session_state.get("selected_group") or group)
        with st.spinner("Reading today's selected study materials and preparing fresh NLP questions..."):
            daily_frame, generation_method, generation_message = build_daily_test_questions(
                chosen_topics, selected_group_for_daily, q_df, target=50,
                excluded_questions=st.session_state.get("topic_test_seen_questions", [])
            )
        st.session_state.daily_generation_method = generation_method
        st.session_state.daily_generation_message = generation_message
        if len(daily_frame) < 50:
            st.session_state.daily_questions = daily_frame.reset_index(drop=True)
            st.markdown(f"<p style='color:#888;font-size:14px'>{generation_message}</p>", unsafe_allow_html=True)
            if st.button("🔙 Back to Study", key="daily_back_insufficient"):
                st.session_state.page = "study"
                st.session_state.daily_questions = None
                st.session_state.daily_answers = {}
                st.session_state.daily_start_time = None
                st.session_state.daily_submitted = False
                st.rerun()
            st.stop()
        st.session_state.daily_questions = daily_frame.reset_index(drop=True)
        st.session_state.daily_answers = {}
        st.session_state.daily_start_time = datetime.now()
        st.session_state.daily_submitted = False
        st.session_state.daily_result = None

    questions = st.session_state.daily_questions
    if not st.session_state.daily_submitted:
        if st.session_state.daily_start_time is None:
            st.session_state.daily_start_time = datetime.now()
        elapsed = (datetime.now() - st.session_state.daily_start_time).total_seconds()
        remaining = max(0, DAILY_TEST_TIMER_SECONDS - int(elapsed))
        st.markdown(
            f"<p style='font-size:18px;font-weight:600'>⏳ Daily Test Time Left: {remaining // 60:02}:{remaining % 60:02}</p>",
            unsafe_allow_html=True,
        )
        daily_alerts = st.session_state.get("daily_test_alerts", [])
        for mins in (20, 10, 5, 1):
            if remaining <= mins * 60 and mins not in daily_alerts:
                daily_alerts.append(mins)
                queue_alert(
                    f"daily_test_{st.session_state.daily_attempt_id}_{mins}min",
                    f"You have {mins} minute{'s' if mins != 1 else ''} left in your daily test."
                )
        st.session_state.daily_test_alerts = daily_alerts
        pending_daily = st.session_state.get("pending_voice_messages", [])
        if pending_daily:
            for msg in pending_daily:
                st.toast("🔔 " + msg)
            if st.session_state.voice_enabled:
                send_voice_alerts(pending_daily)
            st.session_state.pending_voice_messages = []
        st_autorefresh(interval=1000, key=f"daily_test_timer_{st.session_state.daily_attempt_id}")
        for i, row in questions.iterrows():
            st.markdown(f"**Q{i + 1}. {row['question']}**")
            opts = [f"{letter}) {row[f'option{option_index}']}" for option_index, letter in enumerate(("A", "B", "C", "D"), start=1)]
            st.session_state.daily_answers[i] = st.radio(
                "Select Answer",
                opts,
                key=f"daily_answer_{st.session_state.daily_attempt_id}_{i}",
                index=None,
            )
        if st.button("✅ Submit Daily Test", key="submit_daily_test") or remaining <= 0:
            score = 0
            wrong_count = 0
            unanswered_count = 0
            for i, row in questions.iterrows():
                answer = st.session_state.daily_answers.get(i)
                if answer is None or not str(answer).strip():
                    unanswered_count += 1
                elif normalize_match(re.sub(r"^[A-D][).]\s*", "", str(answer))) == normalize_match(row["answer"]):
                    score += 1
                else:
                    wrong_count += 1
            total = len(questions)
            percentage = round(score / total * 100, 2) if total else 0.0
            status = "Strong" if percentage >= 80 else "Average" if percentage >= 60 else "Weak"

            # Calculate performance separately for each subject/topic represented in the mixed Daily Test.
            per_topic = {}
            active_group = str(st.session_state.get("selected_group") or group)
            for i, row in questions.iterrows():
                subject_name = str(row.get("subject", "")).strip() or "Unknown Subject"
                topic_name = str(row.get("topic", "")).strip() or "Unknown Topic"
                topic_key = (_key_text(subject_name), _key_text(topic_name))
                if topic_key not in per_topic:
                    per_topic[topic_key] = {
                        "group": str(row.get("group", active_group) or active_group),
                        "subject": subject_name, "topic": topic_name, "score": 0, "total": 0,
                    }
                per_topic[topic_key]["total"] += 1
                answer = st.session_state.daily_answers.get(i)
                cleaned_answer = re.sub(r"^[A-D][).]\s*", "", str(answer)) if answer is not None else ""
                if answer is not None and normalize_match(cleaned_answer) == normalize_match(row.get("answer", "")):
                    per_topic[topic_key]["score"] += 1
            topic_results = []
            for topic_item in per_topic.values():
                topic_pct = round(topic_item["score"] / topic_item["total"] * 100, 2) if topic_item["total"] else 0.0
                topic_item["percentage"] = topic_pct
                topic_item["status"] = "Weak" if topic_pct < 60 else "Average" if topic_pct < 80 else "Strong"
                topic_results.append(topic_item)
            topic_results.sort(key=lambda item: (item["percentage"], item["subject"].casefold(), item["topic"].casefold()))
            today_weak_topics = [item for item in topic_results if item["percentage"] < 60]

            result = {
                "date": datetime.now().strftime("%d %b %Y"),
                "date_iso": date.today().isoformat(),
                "group": active_group,
                "subject": "Daily Test",
                "topic": "Today's Scheduled Topics",
                "score": score,
                "total": total,
                "percentage": percentage,
                "status": status,
                "test_type": "Daily Test",
                "correct": score,
                "wrong": wrong_count,
                "unanswered": unanswered_count,
                "topic_results": topic_results,
                "weak_topics": today_weak_topics,
            }
            st.session_state.daily_result = result
            st.session_state.daily_submitted = True
            st.session_state.progress.append(result.copy())
            save_progress_for_user(user_email, st.session_state.progress)
            queue_alert(
                f"daily_test_{st.session_state.daily_attempt_id}_finished",
                "Your daily test has been submitted and your result has been saved."
            )
            st.rerun()

    if st.session_state.daily_submitted:
        result = st.session_state.daily_result
        if result:
            st.success("✅ Daily Test Submitted Successfully!")
            c1, c2, c3 = st.columns(3)
            c1.metric("Score", f"{result['score']} / {result['total']}")
            c2.metric("Accuracy", f"{result['percentage']}%")
            c3.metric("Status", result["status"])
            r1, r2, r3 = st.columns(3)
            r1.metric("✅ Correct", result.get("correct", result["score"]))
            r2.metric("❌ Wrong", result.get("wrong", 0))
            r3.metric("⏭️ Not Answered", result.get("unanswered", 0))
            st.markdown("### 📋 Answer Review")
            for i, row in questions.iterrows():
                answer = st.session_state.daily_answers.get(i)
                with st.expander(f"Question {i + 1}"):
                    st.write(row["question"])
                    st.write("Your Answer:", re.sub(r"^[A-D][).]\s*", "", str(answer)) if answer is not None else "Not answered")
                    st.write("Correct Answer:", row["answer"])
                    if answer is not None and normalize_match(re.sub(r"^[A-D][).]\s*", "", str(answer))) == normalize_match(row["answer"]):
                        st.success("Correct")
                    else:
                        st.error("Incorrect / Not answered")
            current_weak_areas = get_weak_area_recommendations(
                st.session_state.progress, date.today().isoformat(), group_filter=group, threshold=60.0
            )
            st.markdown("### 🔎 Weak Area Recommendation")
            if current_weak_areas:
                st.caption("The system keeps topics below 60% and also uses a Decision Tree ML prediction when enough historical results are available. If history is limited, it safely falls back to the current-score rule.")
                for item in current_weak_areas:
                    st.warning(f"{item['subject']} — {item['topic']} | {item['correct']}/{item['total']} correct ({item['percentage']}%) — {item.get('recommendation_reason', 'revise this topic tomorrow.')}")
            else:
                st.success("No weak areas identified from today's recorded topic results or available ML prediction. Keep revising all selected topics.")
            if st.button("🔙 Back to Study", key="back_from_daily_test"):
                st.session_state.page = "study"
                st.session_state.daily_questions = None
                st.session_state.daily_answers = {}
                st.session_state.daily_start_time = None
                st.session_state.daily_submitted = False
                st.session_state.daily_result = None
                st.session_state.daily_generation_method = None
                st.session_state.daily_generation_message = None
                st.rerun()

# Provide a manual button for starting the Daily Test early. Scheduled reminder still follows generated schedule.
if st.session_state.page == "study":
    st.markdown("---")
    if st.button("🎯 Start Daily Test Now", key="start_daily_test_now"):
        st.session_state.page = "daily_test"
        st.session_state.daily_questions = None
        st.session_state.daily_answers = {}
        st.session_state.daily_start_time = None
        st.session_state.daily_submitted = False
        st.session_state.daily_result = None
        st.session_state.daily_generation_method = None
        st.session_state.daily_generation_message = None
        st.session_state.daily_attempt_id += 1
        st.session_state.daily_test_alerts = []
        st.rerun()

# =========================================================
# PROGRESS REPORT
# =========================================================
def daily_status_name(percentage_value):
    return "Strong" if percentage_value >= 80 else "Average" if percentage_value >= 60 else "Weak"


def daily_status_color(status_name):
    return {"Strong": "#10b981", "Average": "#d97706", "Weak": "#dc2626"}.get(status_name, "#64748b")


def normalise_daily_topic_results(entry):
    """Read topic scores from current and older Daily Test progress records."""
    raw_items = entry.get("topic_results") or entry.get("topic_breakdown") or []
    normalised = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        subject_name = str(item.get("subject", "")).strip() or "Subject"
        topic_name = str(item.get("topic", "")).strip() or "Topic"
        try:
            correct = int(float(item.get("score", item.get("correct", 0)) or 0))
            total = int(float(item.get("total", 0) or 0))
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        correct = max(0, min(correct, total))
        try:
            pct = float(item.get("percentage")) if item.get("percentage") is not None else (correct / total * 100)
        except (TypeError, ValueError):
            pct = correct / total * 100
        pct = max(0.0, min(100.0, pct))
        normalised.append({
            "group": str(item.get("group", entry.get("group", ""))),
            "subject": subject_name,
            "topic": topic_name,
            "score": correct,
            "total": total,
            "percentage": round(pct, 2),
            "status": item.get("status") or daily_status_name(pct),
        })
    return normalised


def build_daily_dashboard_html(entry, heading):
    """Build one date/attempt dashboard from the actual topics and scores in that test."""
    import html as html_lib

    topic_results = normalise_daily_topic_results(entry)
    try:
        score = int(float(entry.get("score", 0) or 0))
        total = int(float(entry.get("total", 0) or 0))
        percentage = float(entry.get("percentage", (score / total * 100 if total else 0)) or 0)
    except (TypeError, ValueError):
        score, total, percentage = 0, 0, 0.0
    status_name = entry.get("status") or daily_status_name(percentage)
    status_color = daily_status_color(status_name)

    def metric_box(label, value, color):
        return (
            '<div style="flex:1;min-width:170px;border:2px solid ' + color + ';border-radius:10px;'
            'padding:10px 16px;background:#ffffff;box-sizing:border-box;">'
            '<div style="font-size:12px;font-weight:700;letter-spacing:0.4px;color:' + color + ';">'
            + html_lib.escape(label) + '</div>'
            '<div style="font-size:22px;font-weight:700;margin-top:2px;color:#0f172a;">'
            + html_lib.escape(str(value)) + '</div></div>'
        )

    parts = [
        '<div style="font-family:Segoe UI,Roboto,Arial,sans-serif;border:1px solid #e2e8f0;'
        'border-radius:14px;padding:22px 26px;background:#ffffff;color:#0f172a;box-sizing:border-box;">',
        '<div style="font-size:22px;font-weight:700;margin-bottom:14px;color:#0f172a;">'
        + html_lib.escape(heading) + '</div>',
        '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:22px;">'
        + metric_box("TOTAL MARKS", f"{score} / {total}", "#2563eb")
        + metric_box("OVERALL PERCENTAGE", f"{percentage:g}%", "#10b981")
        + metric_box("OVERALL STATUS", str(status_name), status_color)
        + '</div>',
    ]

    height = 150
    weak_items = []
    if topic_results:
        count = len(topic_results)
        # Allow horizontal scrolling when a Daily Test contains many topics,
        # keeping every topic label and its score readable.
        graph_width = max(700, count * 132)
        grid_cols = "grid-template-columns:repeat(" + str(count) + ",minmax(0,1fr));"
        parts.append('<div style="width:100%;overflow-x:auto;overflow-y:hidden;padding-bottom:5px;">')
        parts.append('<div style="min-width:' + str(graph_width) + 'px;">')
        plot = ['<div style="position:relative;height:260px;margin:46px 0 0 54px;border-left:1px solid #e2e8f0;border-bottom:1px solid #cbd5e1;">']
        for grid_value in range(0, 101, 20):
            plot.append('<div style="position:absolute;left:0;right:0;bottom:' + str(grid_value) + '%;border-top:1px solid #e5eaf2;"></div>')
            plot.append('<span style="position:absolute;left:-52px;bottom:calc(' + str(grid_value) + '% - 9px);width:44px;text-align:right;font-size:12px;color:#64748b;">' + str(grid_value) + '%</span>')
        plot.append('<div style="position:absolute;left:0;right:0;top:0;bottom:0;display:grid;' + grid_cols + '">')
        for item in topic_results:
            item_pct = max(0.0, min(100.0, float(item.get("percentage", 0) or 0)))
            item_color = daily_status_color(daily_status_name(item_pct))
            if item_pct < 60:
                weak_items.append(item)
            pct_text = format(item_pct, "g")
            score_text = html_lib.escape(f"{int(item.get('score', 0))} / {int(item.get('total', 0))}")
            plot.append(
                '<div style="position:relative;min-width:0;">'
                '<div style="position:absolute;bottom:0;left:50%;width:58%;max-width:96px;min-width:18px;'
                'transform:translateX(-50%);height:' + pct_text + '%;background:' + item_color + ';'
                'border-radius:5px 5px 0 0;"></div>'
                '<div style="position:absolute;left:0;right:0;bottom:calc(' + pct_text + '% + 6px);text-align:center;">'
                '<div style="font-size:15px;font-weight:700;color:#0f172a;white-space:nowrap;">' + pct_text + '%</div>'
                '<div style="font-size:12px;color:#64748b;white-space:nowrap;">' + score_text + '</div>'
                '</div></div>'
            )
        plot.append('</div></div>')
        parts.append("".join(plot))
        labels = "".join(
            '<div style="text-align:center;font-size:13px;line-height:1.3;color:#1e293b;padding:0 6px;'
            'word-break:break-word;overflow-wrap:anywhere;">'
            + html_lib.escape(str(item.get("topic", "")))
            + '<div style="font-size:11px;color:#64748b;margin-top:3px;">'
            + html_lib.escape(str(item.get("subject", ""))) + '</div></div>'
            for item in topic_results
        )
        parts.append('<div style="display:grid;' + grid_cols + 'margin:10px 0 0 54px;">' + labels + '</div>')
        parts.append(
            '<div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:20px;font-size:13px;color:#64748b;">'
            '<span><span style="color:#10b981;font-size:17px;">&#9679;</span> Strong (80% and above)</span>'
            '<span><span style="color:#d97706;font-size:17px;">&#9679;</span> Average (60&ndash;79%)</span>'
            '<span><span style="color:#dc2626;font-size:17px;">&#9679;</span> Weak (below 60%)</span>'
            '</div>'
        )
        parts.append('</div>')  # horizontally scrollable graph
        height += 46 + 260 + 100 + 60

        # Exact topic-wise marks table, using only the topics actually present in this Daily Test.
        parts.append('<div style="margin-top:20px;overflow-x:auto;">')
        parts.append(
            '<table style="width:100%;border-collapse:collapse;font-size:13px;color:#0f172a;">'
            '<thead><tr style="background:#f1f5f9;">'
            '<th style="text-align:left;padding:10px;border-bottom:1px solid #cbd5e1;">Subject</th>'
            '<th style="text-align:left;padding:10px;border-bottom:1px solid #cbd5e1;">Topic</th>'
            '<th style="text-align:center;padding:10px;border-bottom:1px solid #cbd5e1;">Score</th>'
            '<th style="text-align:center;padding:10px;border-bottom:1px solid #cbd5e1;">Accuracy</th>'
            '<th style="text-align:center;padding:10px;border-bottom:1px solid #cbd5e1;">Status</th>'
            '</tr></thead><tbody>'
        )
        for item in topic_results:
            item_pct = float(item.get("percentage", 0) or 0)
            item_status = daily_status_name(item_pct)
            item_color = daily_status_color(item_status)
            parts.append(
                '<tr><td style="padding:9px 10px;border-bottom:1px solid #e2e8f0;">'
                + html_lib.escape(str(item.get("subject", ""))) + '</td><td style="padding:9px 10px;border-bottom:1px solid #e2e8f0;">'
                + html_lib.escape(str(item.get("topic", ""))) + '</td><td style="text-align:center;padding:9px 10px;border-bottom:1px solid #e2e8f0;">'
                + html_lib.escape(f"{int(item.get('score', 0))} / {int(item.get('total', 0))}")
                + '</td><td style="text-align:center;padding:9px 10px;border-bottom:1px solid #e2e8f0;">'
                + html_lib.escape(f"{item_pct:g}%") + '</td><td style="text-align:center;padding:9px 10px;border-bottom:1px solid #e2e8f0;font-weight:700;color:' + item_color + ';">'
                + html_lib.escape(item_status) + '</td></tr>'
            )
        parts.append('</tbody></table></div>')
        height += 46 + 42 * (len(topic_results) + 1)

        parts.append('<div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;">')
        if weak_items:
            parts.append('<div style="font-size:18px;font-weight:700;color:#dc2626;margin-bottom:10px;">&#9888; Weak Areas</div>')
            for item in weak_items:
                parts.append(
                    '<div style="border-left:4px solid #dc2626;background:#fef2f2;border-radius:6px;padding:10px 14px;'
                    'margin-bottom:8px;font-size:14px;color:#7f1d1d;">'
                    '<b>' + html_lib.escape(str(item.get("subject", ""))) + ' &mdash; '
                    + html_lib.escape(str(item.get("topic", ""))) + '</b> &mdash; scored '
                    + str(int(item.get("score", 0) or 0)) + ' / ' + str(int(item.get("total", 0) or 0))
                    + ' (' + format(float(item.get("percentage", 0) or 0), "g") + '%). Revise this topic.'
                    '</div>'
                )
            height += 50 + 58 * len(weak_items)
        else:
            parts.append(
                '<div style="border-left:4px solid #10b981;background:#ecfdf5;border-radius:6px;padding:10px 14px;'
                'font-size:14px;color:#065f46;"><b>&#10003; No weak areas.</b> Every topic in this Daily Test is at 60% or above.</div>'
            )
            height += 70
        parts.append('</div>')
    else:
        parts.append(
            '<div style="color:#64748b;font-size:14px;">Topic-wise details are not stored for this earlier Daily Test attempt. '
            'New Daily Test attempts will save topic-wise scores for this chart.</div>'
        )
        height += 55
    parts.append('</div>')
    return "".join(parts), height + 40


if st.session_state.page == "progress":
    st.title("📊 Progress Report")
    progress = st.session_state.progress
    progress_view = st.session_state.get("progress_view", "topic")
    view_col1, view_col2 = st.columns(2)
    with view_col1:
        if st.button("📝 Topic Test", key="progress_view_topic", use_container_width=True, type="primary" if progress_view == "topic" else "secondary"):
            st.session_state.progress_view = "topic"
            st.rerun()
    with view_col2:
        if st.button("🎯 Daily Test", key="progress_view_daily", use_container_width=True, type="primary" if progress_view == "daily" else "secondary"):
            st.session_state.progress_view = "daily"
            st.rerun()
    st.markdown("---")

    if progress_view == "daily":
        daily_entries = [item for item in progress if item.get("test_type") == "Daily Test"]
        if not daily_entries:
            st.info("No Daily Test has been submitted yet. Submit a Daily Test and its dashboard will appear here.")
        else:
            def _daily_entry_order(entry):
                date_value = str(entry.get("date_iso", "")).strip()
                if not date_value:
                    try:
                        date_value = datetime.strptime(str(entry.get("date", "")), "%d %b %Y").date().isoformat()
                    except ValueError:
                        date_value = "0001-01-01"
                time_value = str(entry.get("time", "")).strip()
                try:
                    parsed_time = datetime.strptime(time_value, "%I:%M %p").time() if time_value else time(0, 0)
                except ValueError:
                    parsed_time = time(0, 0)
                return date_value, parsed_time

            daily_entries = sorted(daily_entries, key=_daily_entry_order)
            attempts_per_date = {}
            for item in daily_entries:
                date_key = item.get("date", "-")
                attempts_per_date[date_key] = attempts_per_date.get(date_key, 0) + 1
            attempt_counter = {}
            # Oldest day first; each new day is added below the previous one.
            for item in daily_entries:
                date_text = item.get("date", "-")
                attempt_counter[date_text] = attempt_counter.get(date_text, 0) + 1
                heading = f"Daily Test Progress — {date_text}"
                if attempts_per_date[date_text] > 1:
                    heading += f" (Attempt {attempt_counter[date_text]})"
                card_html, card_height = build_daily_dashboard_html(item, heading)
                components.html(card_html, height=card_height, scrolling=False)
    else:
        st.subheader("Test Progress")
        if not progress:
            st.info("No tests have been submitted yet.")
        else:
            progress_df = pd.DataFrame(progress)
            if "subject" not in progress_df.columns:
                progress_df["subject"] = "-"
            if "topic" not in progress_df.columns:
                progress_df["topic"] = "-"
            if "test_type" not in progress_df.columns:
                progress_df["test_type"] = "Topic Test"
            display_df = progress_df.drop_duplicates(subset=["subject", "topic", "test_type"], keep="last").copy()
            display_df["Test Type"] = display_df["test_type"]
            display_df["Subject"] = display_df["subject"]
            display_df["Topic"] = display_df["topic"]
            display_df["Score"] = display_df.apply(lambda row: f"{int(row['score'])} / {int(row['total'])}", axis=1)
            display_df["Accuracy"] = display_df["percentage"].map(lambda value: f"{value}%")
            display_df["Status"] = display_df["status"]
            st.dataframe(display_df[["Test Type", "Subject", "Topic", "Score", "Accuracy", "Status"]], use_container_width=True, hide_index=True)
            st.markdown("### 🧾 All Test Attempts")
            for item in reversed(progress):
                st.markdown("---")
                st.write(f"📅 Date: {item.get('date', '-')}")
                st.write(f"📝 Test Type: {item.get('test_type', 'Topic Test')}")
                st.write(f"📘 Subject: {item.get('subject', '-')}")
                st.write(f"📚 Topic: {item.get('topic', '-')}")
                st.write(f"🎯 Score: {item.get('score', 0)}/{item.get('total', 0)}")
                st.write(f"📊 Accuracy: {item.get('percentage', 0)}%")
                st.write(f"📌 Status: {item.get('status', '-')}")
    # =========================================================
    # EXTRA FEATURES: DOWNLOAD REPORT + PERFORMANCE TREND + REVISION HISTORY
    # These are additive and use the existing saved progress records only.
    # =========================================================
    st.markdown("---")
    st.header("✨ Additional Progress Tools")

    def _progress_date_label(record):
        value = str(record.get("date_iso", "")).strip()
        if value:
            try:
                return date.fromisoformat(value).strftime("%d %b %Y")
            except ValueError:
                pass
        return str(record.get("date", "-") or "-")

    def _progress_topic_rows(records):
        """Return topic-level assessment rows without modifying saved progress."""
        rows = []
        for record_index, record in enumerate(records or []):
            test_type = str(record.get("test_type", "Topic Test") or "Topic Test")
            record_date = _progress_date_label(record)
            record_group = str(record.get("group", "") or "")
            if _key_text(test_type) == "daily test":
                topic_items = record.get("topic_results") or record.get("topic_breakdown") or []
                for item in topic_items:
                    try:
                        score_value = int(float(item.get("score", item.get("correct", 0)) or 0))
                        total_value = int(float(item.get("total", 0) or 0))
                    except (TypeError, ValueError):
                        continue
                    if total_value <= 0:
                        continue
                    pct = item.get("percentage")
                    try:
                        pct = float(pct) if pct is not None else score_value / total_value * 100
                    except (TypeError, ValueError):
                        pct = score_value / total_value * 100
                    rows.append({
                        "date": record_date,
                        "date_iso": str(record.get("date_iso", "") or ""),
                        "test_type": "Daily Test",
                        "group": str(item.get("group", record_group) or record_group),
                        "subject": str(item.get("subject", "") or "Unknown Subject"),
                        "topic": str(item.get("topic", "") or "Unknown Topic"),
                        "score": score_value,
                        "total": total_value,
                        "percentage": round(pct, 2),
                        "status": str(item.get("status", "") or ("Weak" if pct < 60 else "Average" if pct < 80 else "Strong")),
                        "_record_index": record_index,
                    })
            else:
                try:
                    score_value = int(float(record.get("score", 0) or 0))
                    total_value = int(float(record.get("total", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if total_value <= 0:
                    continue
                try:
                    pct = float(record.get("percentage", score_value / total_value * 100) or 0)
                except (TypeError, ValueError):
                    pct = score_value / total_value * 100
                rows.append({
                    "date": record_date,
                    "date_iso": str(record.get("date_iso", "") or ""),
                    "test_type": test_type,
                    "group": record_group,
                    "subject": str(record.get("subject", "") or "-"),
                    "topic": str(record.get("topic", "") or "-"),
                    "score": score_value,
                    "total": total_value,
                    "percentage": round(pct, 2),
                    "status": str(record.get("status", "") or ("Weak" if pct < 60 else "Average" if pct < 80 else "Strong")),
                    "_record_index": record_index,
                })
        return rows

    additional_topic_rows = _progress_topic_rows(progress)

    st.subheader("📥 Download Progress Report")
    st.caption("Download your saved test attempts and Daily Test topic-wise results. This does not change your saved progress.")
    export_rows = []
    for record in progress:
        try:
            score_value = int(float(record.get("score", 0) or 0))
            total_value = int(float(record.get("total", 0) or 0))
        except (TypeError, ValueError):
            score_value, total_value = 0, 0
        try:
            pct = float(record.get("percentage", score_value / total_value * 100 if total_value else 0) or 0)
        except (TypeError, ValueError):
            pct = score_value / total_value * 100 if total_value else 0
        export_rows.append({
            "Date": _progress_date_label(record),
            "Test Type": str(record.get("test_type", "Topic Test") or "Topic Test"),
            "Group": str(record.get("group", "") or ""),
            "Subject": str(record.get("subject", "-") or "-"),
            "Topic": str(record.get("topic", "-") or "-"),
            "Score": f"{score_value}/{total_value}",
            "Accuracy": f"{pct:g}%",
            "Status": str(record.get("status", "-") or "-"),
        })
        if _key_text(record.get("test_type", "")) == "daily test":
            for topic_item in (record.get("topic_results") or record.get("topic_breakdown") or []):
                try:
                    t_score = int(float(topic_item.get("score", topic_item.get("correct", 0)) or 0))
                    t_total = int(float(topic_item.get("total", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if t_total <= 0:
                    continue
                try:
                    t_pct = float(topic_item.get("percentage", t_score / t_total * 100) or 0)
                except (TypeError, ValueError):
                    t_pct = t_score / t_total * 100
                export_rows.append({
                    "Date": _progress_date_label(record),
                    "Test Type": "Daily Test — Topic-wise",
                    "Group": str(topic_item.get("group", record.get("group", "")) or ""),
                    "Subject": str(topic_item.get("subject", "-") or "-"),
                    "Topic": str(topic_item.get("topic", "-") or "-"),
                    "Score": f"{t_score}/{t_total}",
                    "Accuracy": f"{t_pct:g}%",
                    "Status": str(topic_item.get("status", "") or ("Weak" if t_pct < 60 else "Average" if t_pct < 80 else "Strong")),
                })
    export_columns = ["Date", "Test Type", "Group", "Subject", "Topic", "Score", "Accuracy", "Status"]
    export_df = pd.DataFrame(export_rows, columns=export_columns)
    csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Download Progress Report (CSV)",
        data=csv_data,
        file_name=f"tnpsc_progress_report_{date.today().isoformat()}.csv",
        mime="text/csv",
        key="download_progress_report_csv",
        disabled=not bool(export_rows),
        use_container_width=True,
    )
    if not export_rows:
        st.info("Complete a Topic Test or Daily Test to enable report download.")

    st.markdown("---")
    st.subheader("📈 Performance Improvement Tracking")
    if not additional_topic_rows:
        st.info("Performance tracking will appear after test results have been saved.")
    else:
        topic_choices_map = {}
        for row in additional_topic_rows:
            label = f"{row['subject']} — {row['topic']}"
            topic_choices_map.setdefault(label, []).append(row)
        topic_choice_labels = sorted(topic_choices_map.keys(), key=str.casefold)
        chosen_topic_label = st.selectbox(
            "Choose a subject and topic to compare past results",
            topic_choice_labels,
            key="performance_tracking_topic",
        )
        selected_history = topic_choices_map[chosen_topic_label]
        selected_history = sorted(
            selected_history,
            key=lambda row: (
                row.get("date_iso") or "0001-01-01",
                row.get("_record_index", 0),
            ),
        )
        if len(selected_history) < 2:
            st.info("Only one saved result exists for this topic. Complete another Topic Test or Daily Test to compare improvement.")
        else:
            trend_df = pd.DataFrame([
                {
                    "Attempt": f"{row['date']} · {row['test_type']} #{index + 1}",
                    "Accuracy (%)": float(row["percentage"]),
                }
                for index, row in enumerate(selected_history)
            ])
            st.line_chart(trend_df.set_index("Attempt"), y="Accuracy (%)")
            first_accuracy = float(selected_history[0]["percentage"])
            latest_accuracy = float(selected_history[-1]["percentage"])
            change = round(latest_accuracy - first_accuracy, 2)
            metric_col1, metric_col2, metric_col3 = st.columns(3)
            metric_col1.metric("First Recorded Accuracy", f"{first_accuracy:g}%")
            metric_col2.metric("Latest Accuracy", f"{latest_accuracy:g}%")
            metric_col3.metric("Change Since First Attempt", f"{change:+g} percentage points")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Date": row["date"],
                        "Test Type": row["test_type"],
                        "Score": f"{row['score']} / {row['total']}",
                        "Accuracy": f"{row['percentage']:g}%",
                        "Status": row["status"],
                    }
                    for row in selected_history
                ]),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")
    st.subheader("🎯 Weak Area Revision History")
    if not additional_topic_rows:
        st.info("Revision history will appear after test results have been saved.")
    else:
        topic_history_map = {}
        for row in additional_topic_rows:
            key = (_key_text(row["group"]), _key_text(row["subject"]), _key_text(row["topic"]))
            topic_history_map.setdefault(key, []).append(row)
        revision_rows = []
        for history in topic_history_map.values():
            history = sorted(
                history,
                key=lambda row: (
                    row.get("date_iso") or "0001-01-01",
                    row.get("_record_index", 0),
                ),
            )
            latest = history[-1]
            previous = history[-2] if len(history) >= 2 else None
            latest_pct = float(latest.get("percentage", 0) or 0)
            previous_pct = float(previous.get("percentage", 0) or 0) if previous else None
            if previous is None:
                revision_status = "First recorded attempt" if latest_pct >= 60 else "Needs Revision"
                difference_text = "—"
            else:
                difference = round(latest_pct - previous_pct, 2)
                difference_text = f"{difference:+g} pp"
                if latest_pct < 60:
                    revision_status = "Needs Revision"
                elif difference > 0:
                    revision_status = "Improved"
                elif difference < 0:
                    revision_status = "Accuracy Decreased"
                else:
                    revision_status = "Maintained"
            revision_rows.append({
                "Group": latest["group"] or "-",
                "Subject": latest["subject"],
                "Topic": latest["topic"],
                "Previous Accuracy": f"{previous_pct:g}%" if previous_pct is not None else "—",
                "Latest Accuracy": f"{latest_pct:g}%",
                "Change": difference_text,
                "Status": revision_status,
                "Latest Test Date": latest["date"],
                "_sort_pct": latest_pct,
            })
        revision_rows.sort(key=lambda row: (0 if row["Status"] == "Needs Revision" else 1, row["_sort_pct"], row["Subject"].casefold(), row["Topic"].casefold()))
        revision_df = pd.DataFrame(revision_rows)
        if not revision_df.empty:
            st.dataframe(
                revision_df.drop(columns=["_sort_pct"]),
                use_container_width=True,
                hide_index=True,
            )
            st.caption("This table compares each topic's two most recent saved results. Topics with less than 60% accuracy are marked Needs Revision.")

    if st.button("🔙 Back to Study", key="back_from_progress"):
        st.session_state.page = "study"
        st.rerun()