"""
TransitFlow — Gradio Web Interface
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Students: You do NOT need to change this file.
"""

"""
TransitFlow — Gradio Web Interface
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Students: You do NOT need to change this file. (But we upgraded it!)
"""
"""
TransitFlow — Gradio Web Interface
====================================
【新增功能與修改說明註記】

1. 新增功能：
   - 使用者歷史行程查詢 (Trip History)：當使用者成功登入或註冊後，右上角控制區會動態顯示「📜 Trip History」按鈕。點擊後會直接調用後端 PostgreSQL 的 DDL/SQL 資料流，撈取該登入帳號專屬的國鐵 (National Rail) 訂票與捷運 (Metro) 搭乘紀錄。
   - 獨立歷史紀錄面板：在主聊天視窗上方新增一個獨立的 `history_panel` 控制區塊，內建 `gr.JSON()` 元件。撈取出來的巢狀行程歷史資料會以高度視覺化的「樹狀可折疊結構」呈現在網頁上，並附帶「Close History」按鈕供隨時關閉收合。

2. 修改項目：
   - 後端查詢整合：於檔案開頭匯入 `databases.relational.queries` 中的關聯式資料庫歷史查詢核心函式 `query_user_bookings`。
   - 驗證狀態機連動：全面重構 `do_login()`、`do_logout()` 與 `do_register()` 的回傳元組 (Tuple) 架構，將「歷史紀錄按鈕」與「歷史紀錄面板」的顯示狀態 (visible) 強制與使用者登入狀態綁定（未登入或登出時，按鈕與面板皆會自動強制隱藏）。
   - 前端元件佈局與排版：將右上角的 `Gradio Column` 稍微加寬至 320px，並使用 `gr.Row()` 讓「📜 Trip History」按鈕與原有的「Logout」按鈕能完美並排對齊。
   - 事件驅動網路更新 (Event Wiring)：在 Blocks 底部新增 `view_history_btn.click` 與 `close_history_btn.click` 的事件處理邏輯，並同步更新所有驗證按鈕觸發時的 `outputs` 元件清單數量與對應關係。
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Students: You do NOT need to change this file. (But we upgraded it for the mission!)
"""

import sys
sys.path.insert(0, ".")

import gradio as gr
from skeleton.agent import run_agent
from skeleton.llm_provider import llm
from skeleton.config import GEMINI_CHAT_MODEL, OLLAMA_CHAT_MODEL
from databases.relational.queries import (
    login_user,
    register_user,
    get_user_secret_question,
    verify_secret_answer,
    update_password,
    query_user_bookings,  # ✨ 新增：匯入歷史紀錄查詢函式
)

SECRET_QUESTIONS = [
    "What is the name of your first pet?",
    "What is your mother's maiden name?",
    "What city were you born in?",
    "What was the name of your first school?",
    "What is your favourite book?",
    "What was the make of your first car?",
]


# ── Chat handler ───────────────────────────────────────────────────────────────

def chat(user_message: str, history_display: list, agent_history: list,
         show_debug: bool, current_user: str):
    if not user_message.strip():
        return history_display, agent_history, gr.update()

    if show_debug:
        answer, new_agent_history, debug_text = run_agent(
            user_message, agent_history, debug=True, current_user_email=current_user
        )
    else:
        answer, new_agent_history = run_agent(
            user_message, agent_history, debug=False, current_user_email=current_user
        )
        debug_text = ""

    history_display = history_display + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    debug_update = gr.update(value=debug_text, visible=show_debug)
    return history_display, new_agent_history, debug_update


def clear_conversation():
    return [], [], gr.update(value="", visible=False)


# ── Provider / model selection ────────────────────────────────────────────────

_KNOWN_OLLAMA_MODELS = ["llama3.2:1b", "llama3.1:8b"]


def get_ollama_status():
    if llm.ollama_available():
        return "🟢 Ollama is running locally"
    return "🔴 Ollama not detected — install from ollama.com and run `ollama pull " + OLLAMA_CHAT_MODEL + "`"


def get_chat_model_choices() -> list:
    available = set(llm.get_available_ollama_models())
    choices = []
    for m in _KNOWN_OLLAMA_MODELS:
        label = m if m in available else f"{m}  (not pulled)"
        choices.append((label, m))
    choices.append((f"☁️ Gemini ({GEMINI_CHAT_MODEL})", "gemini"))
    return choices


def get_initial_chat_model_value() -> str:
    return "llama3.2:1b"


def on_chat_model_change(value: str):
    if value == "gemini":
        status = llm.set_chat_provider("gemini")
        return f"**Active:** ☁️ Gemini ({GEMINI_CHAT_MODEL})\n\n{status}", get_ollama_status()
    available = set(llm.get_available_ollama_models())
    if value not in available:
        return f"⚠️ `{value}` is not pulled. Run: `ollama pull {value}`", get_ollama_status()
    llm.set_chat_provider("ollama")
    status = llm.set_chat_model(value)
    return f"**Active:** {value}\n\n{status}", get_ollama_status()


# ── Auth & History handlers ───────────────────────────────────────────────────

def do_login(email: str, password: str):
    """Handle login form submission."""
    if not email.strip() or not password.strip():
        return (
            gr.update(value="Please enter your email and password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
            gr.update(), gr.update()
        )

    user = login_user(email.strip(), password)
    if user is None:
        return (
            gr.update(value="Incorrect email or password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
            gr.update(), gr.update()
        )

    display_name = f"{user['first_name']} {user['surname']}"
    return (
        gr.update(value="", visible=False),
        user["email"],
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=True),  # ✨ 登入成功：顯示歷史紀錄按鈕
        gr.update(visible=False)  # 面板預設隱藏
    )


def do_logout():
    return (
        None,
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="", visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False), # ✨ 登出時：隱藏歷史紀錄按鈕
        gr.update(visible=False)  # ✨ 登出時：隱藏歷史紀錄面板
    )


def do_register(email, first_name, surname, year_of_birth, password, secret_question, secret_answer):
    """Handle registration form submission."""
    if not all([
        str(email).strip(), str(first_name).strip(), str(surname).strip(),
        str(password).strip(), secret_question, str(secret_answer).strip(),
    ]):
        return (
            gr.update(value="All fields are required.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
            gr.update(), gr.update()
        )

    try:
        year = int(year_of_birth)
        if year < 1900 or year > 2015:
            raise ValueError
    except (ValueError, TypeError):
        return (
            gr.update(value="Please enter a valid year of birth (e.g. 1990).", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
            gr.update(), gr.update()
        )

    ok, err = register_user(
        email.strip(), first_name.strip(), surname.strip(),
        year, password, secret_question, secret_answer.strip(),
    )
    if not ok:
        return (
            gr.update(value=err, visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
            gr.update(), gr.update()
        )

    display_name = f"{first_name.strip()} {surname.strip()}"
    return (
        gr.update(value="", visible=False),
        email.strip().lower(),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=True),  # ✨ 註冊成功並登入：顯示歷史紀錄按鈕
        gr.update(visible=False)
    )

def fetch_trip_history(email: str):
    """✨ 升級版：拉取歷史紀錄，並轉換為現代化卡片 HTML 介面"""
    if not email:
        return gr.update(visible=False), ""
    
    # 1. 呼叫 SQL 查詢拉取原始資料
    history_data = query_user_bookings(email)
    
    # 2. 將國鐵與捷運的資料合併，並依照日期排序 (新到舊)
    all_trips = []
    for trip in history_data.get("national_rail", []):
        all_trips.append(trip)
    for trip in history_data.get("metro", []):
        all_trips.append(trip)
        
    if not all_trips:
        return gr.update(visible=True), "<p style='color: gray; padding: 20px;'>No trip history found.</p>"

    # 依照日期排序 (最新的在最上面)
    all_trips.sort(key=lambda x: str(x.get('travel_date', '')), reverse=True)

    # 3. 撰寫前端 CSS 樣式
    html = """
    <style>
    .ticket-card { border: 1px solid #e2e8f0; border-radius: 10px; margin-bottom: 12px; padding: 14px; background-color: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.03); transition: all 0.2s ease; }
    .ticket-card:hover { box-shadow: 0 6px 12px rgba(0,0,0,0.08); border-color: #cbd5e1; }
    .ticket-summary { cursor: pointer; display: flex; justify-content: space-between; align-items: center; font-size: 1.05em; font-weight: 600; outline: none; list-style: none; }
    .ticket-summary::-webkit-details-marker { display: none; }
    .ticket-summary:hover { opacity: 0.8; }
    .ticket-details { margin-top: 14px; padding-top: 14px; border-top: 1px dashed #cbd5e1; font-size: 0.9em; color: #475569; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; line-height: 1.6; }
    .badge { padding: 4px 10px; border-radius: 6px; font-size: 0.75em; font-weight: bold; color: white; text-transform: uppercase; letter-spacing: 0.5px; }
    .badge-rail { background-color: #1e40af; } /* 深藍色代表國鐵 */
    .badge-metro { background-color: #047857; } /* 翡翠綠代表捷運 */
    .status-completed { color: #16a34a; font-weight: bold; }
    .status-cancelled { color: #dc2626; font-weight: bold; }
    .price-tag { font-weight: bold; color: #0f172a; font-size: 1.1em; }
    </style>
    <div>
    """

    # 4. 動態生成每一張票的 HTML 結構
    for trip in all_trips:
        t_type = trip.get("travel_type")
        is_rail = t_type == "national_rail"

        badge_class = "badge-rail" if is_rail else "badge-metro"
        badge_text = "National Rail" if is_rail else "City Metro"
        date_str = trip.get("travel_date", "N/A")
        origin = trip.get("origin_station", "Unknown")
        dest = trip.get("destination_station", "Unknown")
        price = f"${trip.get('amount_usd', 0):.2f}"
        status = trip.get("status", "").lower()
        status_class = f"status-{status}" if status in ["completed", "cancelled"] else ""

        t_id = trip.get("booking_id") if is_rail else trip.get("trip_id")

        # 外層大綱 (不點開也能看到的資訊)
        summary_html = f"""
        <summary class="ticket-summary">
            <div style="display: flex; align-items: center; gap: 12px;">
                <span class="badge {badge_class}">{badge_text}</span>
                <span>{date_str} <span style="color:#94a3b8; font-weight:normal;">|</span> <strong>{origin}</strong> ➔ <strong>{dest}</strong></span>
            </div>
            <div class="price-tag">{price}</div>
        </summary>
        """

        # 內層詳細資訊 (點擊後展開)
        details_left = f"""
        <div>
            <p><strong>Booking Ref:</strong> {t_id}</p>
            <p><strong>Status:</strong> <span class="{status_class}">{status.capitalize()}</span></p>
            <p><strong>Ticket Type:</strong> {trip.get('ticket_type', 'N/A').replace('_', ' ').title()}</p>
        </div>
        """

        if is_rail:
            details_right = f"""
            <div>
                <p><strong>Departure:</strong> {trip.get('departure_time', 'N/A')}</p>
                <p><strong>Service:</strong> Line {trip.get('line', '')} ({trip.get('service_type', 'normal').title()})</p>
                <p><strong>Seat:</strong> Coach {trip.get('coach', 'N/A')}, Seat {trip.get('seat_id', 'N/A')} ({trip.get('fare_class', '').title()})</p>
            </div>
            """
        else:
            pass_info = f"<p><strong>Monthly Pass:</strong> {trip.get('monthly_pass_ref')}</p>" if trip.get('monthly_pass_ref') else ""
            details_right = f"""
            <div>
                <p><strong>Line:</strong> {trip.get('line', 'N/A')}</p>
                <p><strong>Stops Travelled:</strong> {trip.get('stops_travelled', 'N/A')}</p>
                {pass_info}
            </div>
            """

        details_html = f"<div class='ticket-details'>{details_left}{details_right}</div>"

        # 使用 HTML 的 <details> 標籤達成手風琴展開效果
        html += f"<div class='ticket-card'><details>{summary_html}{details_html}</details></div>"

    html += "</div>"
    
    return gr.update(visible=True), html


def forgot_find_question(email: str):
    """Step 1 — look up the secret question for the given email."""
    if not email.strip():
        return (
            gr.update(value="Please enter your email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    question = get_user_secret_question(email.strip())
    if question is None:
        return (
            gr.update(value="No account found with that email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    return (
        gr.update(value="", visible=False),
        gr.update(value=f"**Your security question:** {question}", visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def forgot_reset_password(email: str, answer: str, new_password: str):
    """Step 2 — verify the secret answer and update the password."""
    if not str(answer).strip() or not str(new_password).strip():
        return gr.update(value="Please fill in all fields.", visible=True)

    if not verify_secret_answer(email.strip(), answer.strip()):
        return gr.update(value="Incorrect answer. Please try again.", visible=True)

    if not update_password(email.strip(), new_password):
        return gr.update(value="Failed to update password. Please try again.", visible=True)

    return gr.update(value="**Password reset successfully. You can now log in.**", visible=True)


# ── Panel visibility toggles ──────────────────────────────────────────────────

def show_login_panel():
    return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

def show_register_panel():
    return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

def show_forgot_panel():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

def hide_all_panels():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)


# ── Example queries ────────────────────────────────────────────────────────────

EXAMPLES = [
    "What national rail trains run from Central (NR01) to Stonehaven (NR05)?",
    "What is the fastest metro route from MS01 to MS14?",
    "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
    "If Old Town station (NR03) is closed, what alternative routes exist from NR01 to NR05?",
    "My train was delayed 45 minutes — what compensation am I entitled to?",
    "What is the company policy on travelling with a bicycle on national rail?",
]


# ── Build UI ───────────────────────────────────────────────────────────────────

with gr.Blocks(title="TransitFlow") as demo:

    # ── Hidden state ──────────────────────────────────────────────────
    agent_history_state = gr.State([])
    current_user_state  = gr.State(None)   # None = guest, email str = logged in

    # ── Header: title + auth buttons ─────────────────────────────────
    with gr.Row(equal_height=True):
        gr.Markdown("""
# 🚂 TransitFlow Intelligent Rail Assistant
*Powered by PostgreSQL · pgvector · Neo4j · LLM*
        """)
        with gr.Column(scale=0, min_width=320): # ✨ 稍微加寬，容納新按鈕
            with gr.Row():
                login_btn    = gr.Button("👤 Login",    size="sm", variant="secondary")
                register_btn = gr.Button("📝 Register", size="sm", variant="secondary")
            
            user_info_display = gr.Markdown("", visible=False)
            
            with gr.Row():
                # ✨ 新增：歷史紀錄與登出按鈕並排
                view_history_btn = gr.Button("📜 Trip History", size="sm", visible=False)
                logout_btn = gr.Button("Logout", size="sm", variant="stop", visible=False)

    # ── Login panel (hidden by default) ──────────────────────────────
    with gr.Column(visible=False) as login_panel:
        gr.Markdown("### Login")
        login_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        login_password_in = gr.Textbox(label="Password", type="password")
        login_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            login_submit_btn = gr.Button("Login", variant="primary")
            forgot_link_btn  = gr.Button("Forgot password?", size="sm")
            login_cancel_btn = gr.Button("Cancel", size="sm")

    # ── Register panel (hidden by default) ───────────────────────────
    with gr.Column(visible=False) as register_panel:
        gr.Markdown("### Create an Account")
        with gr.Row():
            reg_first_name_in = gr.Textbox(label="First name")
            reg_surname_in    = gr.Textbox(label="Surname")
        reg_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        reg_year_in     = gr.Textbox(label="Year of birth", placeholder="e.g. 1990")
        reg_password_in = gr.Textbox(label="Password", type="password")
        reg_question_in = gr.Dropdown(choices=SECRET_QUESTIONS, label="Security question")
        reg_answer_in   = gr.Textbox(label="Secret answer")
        reg_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            reg_submit_btn = gr.Button("Register", variant="primary")
            reg_cancel_btn = gr.Button("Cancel", size="sm")

    # ── Forgot password panel (hidden by default) ─────────────────────
    with gr.Column(visible=False) as forgot_panel:
        gr.Markdown("### Reset Your Password")
        forgot_email_in          = gr.Textbox(label="Email address", placeholder="you@example.com")
        forgot_check_btn         = gr.Button("Find my question", variant="secondary")
        forgot_question_display  = gr.Markdown("", visible=False)
        forgot_answer_in         = gr.Textbox(label="Your answer", visible=False)
        forgot_new_password_in   = gr.Textbox(label="New password", type="password", visible=False)
        forgot_reset_btn         = gr.Button("Reset password", variant="primary", visible=False)
        forgot_msg               = gr.Markdown("")
        forgot_back_btn          = gr.Button("Back to login", size="sm")

    # ✨ 新增：歷史紀錄面板 (預設隱藏) ──────────────────────────────────
    # ✨ 修改：歷史紀錄面板 (預設隱藏) ──────────────────────────────────
    with gr.Column(visible=False) as history_panel:
        gr.Markdown("### 📜 Your Trip History")
        history_html = gr.HTML()  # <--- 把 history_json 換成 history_html
        close_history_btn = gr.Button("Close History", size="sm")
        gr.Markdown("---")

    # ── Main chat area ────────────────────────────────────────────────
    with gr.Row():

        # ── Left: chat ────────────────────────────────────────────────
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(label="TransitFlow Assistant", height=420)

            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask e.g. 'Are there seats from London to Bristol?'",
                    show_label=False,
                    scale=4,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)

            with gr.Row():
                clear_btn    = gr.Button("🗑️ Clear conversation", size="sm")
                debug_toggle = gr.Checkbox(label="🔍 Show database debug panel", value=True)

            # Debug panel — hidden until checkbox is ticked and a message is sent
            debug_panel = gr.Markdown(
                value="",
                visible=False,
            )

        # ── Right: sidebar ────────────────────────────────────────────
        with gr.Column(scale=1):

            gr.Markdown("### 🤖 LLM Provider")
            chat_model_dropdown = gr.Dropdown(
                choices=get_chat_model_choices(),
                value=get_initial_chat_model_value(),
                label="Chat model",
                info="Local Ollama models run fully locally. Gemini uses your API key.",
            )
            provider_status = gr.Markdown(value="**Active:** llama3.2:1b")
            ollama_status   = gr.Markdown(value=get_ollama_status())

            gr.Markdown("---")

            gr.Markdown("### 💡 Try these examples")
            for example in EXAMPLES:
                gr.Button(example, size="sm").click(
                    fn=lambda e=example: e,
                    outputs=msg,
                )

    # ── Event wiring ──────────────────────────────────────────────────

    chat_model_dropdown.change(
        fn=on_chat_model_change,
        inputs=chat_model_dropdown,
        outputs=[provider_status, ollama_status],
    )

    send_btn.click(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    msg.submit(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    clear_btn.click(
        fn=clear_conversation,
        outputs=[chatbot, agent_history_state, debug_panel],
    )

    # Panel toggle buttons
    login_btn.click(
        fn=show_login_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    register_btn.click(
        fn=show_register_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    login_cancel_btn.click(
        fn=hide_all_panels,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    reg_cancel_btn.click(
        fn=hide_all_panels,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    forgot_link_btn.click(
        fn=show_forgot_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    forgot_back_btn.click(
        fn=show_login_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )

    # ✨ 新增：歷史紀錄面板的開關事件
    # ✨ 修改：歷史紀錄面板的開關事件
    view_history_btn.click(
        fn=fetch_trip_history,
        inputs=[current_user_state],
        outputs=[history_panel, history_html]  # <--- 從 history_json 變成 history_html
    )
    close_history_btn.click(
        fn=lambda: gr.update(visible=False),
        outputs=[history_panel]
    )

    # Login
    login_submit_btn.click(
        fn=do_login,
        inputs=[login_email_in, login_password_in],
        outputs=[
            login_error_msg,
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            login_panel,
            view_history_btn, # ✨ 新增綁定
            history_panel     # ✨ 新增綁定
        ],
    )

    # Logout
    logout_btn.click(
        fn=do_logout,
        outputs=[
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            login_panel,
            register_panel,
            forgot_panel,
            view_history_btn, # ✨ 新增綁定
            history_panel     # ✨ 新增綁定
        ],
    )

    # Register
    reg_submit_btn.click(
        fn=do_register,
        inputs=[
            reg_email_in, reg_first_name_in, reg_surname_in,
            reg_year_in, reg_password_in, reg_question_in, reg_answer_in,
        ],
        outputs=[
            reg_error_msg,
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            register_panel,
            view_history_btn, # ✨ 新增綁定
            history_panel     # ✨ 新增綁定
        ],
    )

    # Forgot password — step 1: find question
    forgot_check_btn.click(
        fn=forgot_find_question,
        inputs=[forgot_email_in],
        outputs=[
            forgot_msg,
            forgot_question_display,
            forgot_answer_in,
            forgot_new_password_in,
            forgot_reset_btn,
        ],
    )

    # Forgot password — step 2: reset
    forgot_reset_btn.click(
        fn=forgot_reset_password,
        inputs=[forgot_email_in, forgot_answer_in, forgot_new_password_in],
        outputs=[forgot_msg],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
    )