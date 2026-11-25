import csv
import re
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:
    from pyfirmata import Arduino, util
except ImportError:
    Arduino = None
    util = None


PIN_MAP = {
    "Living Room Light": {"pin": 8, "watt": 15, "icon": "💡", "room": "Living Room"},
    "Bedroom Light": {"pin": 9, "watt": 15, "icon": "💡", "room": "Bedroom"},
    "Kitchen Light": {"pin": 10, "watt": 15, "icon": "💡", "room": "Kitchen"},
    "Bathroom Light": {"pin": 11, "watt": 15, "icon": "💡", "room": "Study Room"},
}

LOG_FILE = Path(__file__).resolve().parent / "eco_mitra_log.csv"


def init_state():
    defaults = {
        "board": None,
        "board_port": None,
        "connected": False,
        "states": {name: False for name in PIN_MAP},
        "auto_off_seconds": 0,
        "eco_mode": False,
        "away_mode": False,
        "idle_minutes": 30,
        "schedule_enabled": False,
        "schedule_time": "22:00",
        "last_action": "Waiting for a command",
        "events": [],
        "demo_mode": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def ensure_log_file():
    if not LOG_FILE.exists():
        with LOG_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["timestamp", "device", "action", "estimated_watts"])


def log_event(device, action, watts):
    ensure_log_file()
    timestamp = datetime.now().isoformat(timespec="seconds")
    with LOG_FILE.open("a", newline="", encoding="utf-8") as file:
        csv.writer(file).writerow([timestamp, device, action, watts])

    st.session_state.events.insert(
        0, {"timestamp": timestamp, "device": device, "action": action}
    )
    st.session_state.events = st.session_state.events[:100]


def connect_board(port, demo_mode):
    if demo_mode:
        st.session_state.board = None
        st.session_state.board_port = "Demo"
        st.session_state.connected = True
        st.session_state.demo_mode = True
        st.session_state.last_action = "Demo mode enabled"
        return True, "Demo mode enabled"

    if Arduino is None:
        return False, "Install dependencies with: pip install -r requirements.txt"

    try:
        board = Arduino(port)
        board_passed = getattr(board, "passed_time", None)
        st.session_state.board = board
        st.session_state.board_port = port
        st.session_state.connected = True
        st.session_state.demo_mode = False
        st.session_state.last_action = f"Connected to {port}"
        return True, f"Connected to Arduino on {port}"
    except Exception as exc:
        st.session_state.connected = False
        st.session_state.board = None
        return False, f"Could not connect: {exc}"


def set_device(name, on, source="Dashboard"):
    state = bool(on)
    st.session_state.states[name] = state
    if st.session_state.connected and not st.session_state.demo_mode:
        try:
            pin_number = PIN_MAP[name]["pin"]
            st.session_state.board.digital[pin_number].write(1 if state else 0)
        except Exception as exc:
            st.session_state.last_action = f"Board write failed: {exc}"
            st.toast(st.session_state.last_action, icon="⚠️")

    watts = PIN_MAP[name]["watt"] if state else 0
    action = f"{'ON' if state else 'OFF'} via {source}"
    log_event(name, action, watts)
    st.session_state.last_action = f"{name}: {'ON' if state else 'OFF'}"


def set_all(on, source="Dashboard"):
    for name in PIN_MAP:
        set_device(name, on, source)


def apply_eco_mode(enabled):
    st.session_state.eco_mode = enabled
    if enabled:
        set_all(False, "Eco mode")
        st.session_state.last_action = "Eco mode enabled"
    else:
        st.session_state.last_action = "Eco mode disabled"


def apply_away_mode(enabled):
    st.session_state.away_mode = enabled
    if enabled:
        set_all(False, "Away mode")
        st.session_state.last_action = "Away mode enabled: all devices OFF"
    else:
        st.session_state.last_action = "Away mode disabled"


def extract_voice_action(command):
    words = set(command.split())
    on_words = {"on", "start", "open", "activate", "enable", "up"}
    off_words = {"off", "stop", "close", "deactivate", "disable", "down"}
    turn_up = "turn up" in command
    turn_down = "turn down" in command
    wants_on = bool(words & on_words) or turn_up
    wants_off = bool(words & off_words) or turn_down

    if wants_on == wants_off:
        return None
    return wants_on


def parse_voice_command(command):
    command = " ".join(re.findall(r"[a-z0-9]+", command.casefold()))
    if not command:
        return

    action = extract_voice_action(command)
    if "eco mode" in command:
        if action is None:
            st.toast("Say 'eco mode on' or 'eco mode off'", icon="❓")
            return
        apply_eco_mode(action)
        st.toast("Eco mode updated", icon="🌿")
        return
    if "away mode" in command:
        if action is None:
            st.toast("Say 'away mode on' or 'away mode off'", icon="❓")
            return
        apply_away_mode(action)
        st.toast("Away mode updated", icon="🏠")
        return

    if command in ("0", "off", "1", "on"):
        action = command in ("1", "on")
        set_all(action, "Voice")
        st.toast(f"All lights turned {'ON' if action else 'OFF'}", icon="🔊" if action else "🔇")
        return

    if action is None:
        st.session_state.last_action = f"Voice command needs one clear ON or OFF action: {command}"
        st.toast("Say a room and either 'on' or 'off', for example 'kitchen light on'", icon="❓")
        return

    if any(word in command.split() for word in ("all", "everything")):
        set_all(action, "Voice")
        st.toast(f"All lights turned {'ON' if action else 'OFF'}", icon="🔊" if action else "🔇")
        return

    room_aliases = {
        "Living Room Light": ("living room", "living", "lounge"),
        "Bedroom Light": ("bedroom", "bed room", "bed"),
        "Kitchen Light": ("kitchen", "cooking"),
        "Bathroom Light": ("bathroom", "bath room", "bath"),
    }
    matched_devices = [
        name
        for name, aliases in room_aliases.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", command) for alias in aliases)
    ]
    if matched_devices:
        for name in matched_devices:
            set_device(name, action, "Voice")
        st.toast(
            f"{', '.join(matched_devices)} turned {'ON' if action else 'OFF'}",
            icon="🔊" if action else "🔇",
        )
        return

    st.session_state.last_action = f"Voice command not recognized: {command}"
    st.toast("Try: turn on kitchen light, bedroom light off, or all off", icon="❓")


def get_history():
    ensure_log_file()
    with LOG_FILE.open("r", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return rows


st.set_page_config(
    page_title="EcoHome Dashboard",
    page_icon="🏠",
    layout="wide",
)
init_state()

st.markdown(
    """
    <style>
    :root {
        --ink: #16352c;
        --muted: #71817b;
        --green: #16a085;
        --green-dark: #116b59;
        --green-light: #eaf7f1;
        --line: #e6ede9;
    }
    .stApp {
        background: #f4f7f5;
    }
    [data-testid="stHeader"] {
        background: transparent;
    }
    .block-container {
        max-width: 1500px;
        padding-top: 1.2rem;
        padding-bottom: 2rem;
    }
    [data-testid="stSidebar"] {
        background: #ffffff;
        border-right: 1px solid var(--line);
    }
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 16px;
        box-shadow: 0 8px 24px rgba(31, 78, 61, 0.04);
    }
    [data-testid="stMetricLabel"] {
        color: var(--muted);
    }
    .device-card {
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 18px;
        min-height: 170px;
        box-shadow: 0 8px 24px rgba(31, 78, 61, 0.05);
    }
    .device-card.off {
        background: #fafcfb;
    }
    .device-name {
        color: var(--ink);
        font-size: 18px;
        font-weight: 700;
        margin-top: 8px;
    }
    .device-room {
        color: var(--muted);
        font-size: 13px;
    }
    .status-pill {
        display: inline-block;
        border-radius: 999px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 700;
    }
    .status-pill.on {
        color: #116b59;
        background: #dff4ea;
    }
    .status-pill.off {
        color: #71817b;
        background: #edf1ef;
    }
    .hero {
        background: linear-gradient(135deg, #173f34 0%, #217b65 100%);
        border-radius: 20px;
        color: white;
        padding: 22px 26px;
        margin-bottom: 18px;
    }
    .hero h2 {
        margin: 0 0 6px 0;
    }
    .hero p {
        margin: 0;
        color: #d6eee5;
    }
    div.stButton > button {
        border-radius: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h2>🏠 Good morning, welcome home</h2>
      <p>EcoHome is monitoring and managing your home's energy</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### EcoHome")
    st.caption("Smart energy control")
    st.divider()
    st.header("Arduino Connection")
    port = st.text_input("Serial port", value="COM3", help="Examples: COM3 on Windows or /dev/ttyACM0")
    demo = st.toggle("Demo mode (no board)", value=st.session_state.demo_mode)

    if st.button("Connect", use_container_width=True):
        success, message = connect_board(port, demo)
        if success:
            st.success(message)
        else:
            st.error(message)

    if st.session_state.connected:
        st.success(f"Connected: {st.session_state.board_port}")
        if st.button("Disconnect", use_container_width=True):
            if st.session_state.board is not None:
                set_all(False, "Disconnect")
                st.session_state.board.exit()
            st.session_state.board = None
            st.session_state.connected = False
            st.session_state.board_port = None
            st.session_state.demo_mode = False
            st.rerun()
    else:
        st.warning("Board not connected")

    st.divider()
    st.subheader("Voice Control")
    voice_command = st.text_input(
        "Voice command text",
        placeholder="turn on the kitchen light",
    )
    if st.button("Run command", use_container_width=True):
        parse_voice_command(voice_command)

components.html(
    """
    <button id="mic" style="
        width:100%; padding:12px; border:0; border-radius:8px;
        background:#1b7f5a; color:white; font-size:16px; cursor:pointer;">
      🎤 Start voice control
    </button>
    <div id="status" style="font-family:sans-serif; margin-top:8px; color:#555;">
      Browser microphone ready
    </div>
    <script>
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      const button = document.getElementById("mic");
      const status = document.getElementById("status");

      if (!SpeechRecognition) {
        status.textContent = "Voice recognition is not supported in this browser.";
        button.disabled = true;
      } else {
        const recognition = new SpeechRecognition();
        recognition.lang = "en-US";
        recognition.interimResults = false;
        recognition.continuous = false;

        button.onclick = () => {
          status.textContent = "Listening...";
          button.textContent = "Listening...";
          button.disabled = true;
          recognition.start();
        };

        recognition.onresult = (event) => {
          const text = event.results[0][0].transcript;
          status.textContent = "Heard: " + text;
          button.textContent = "🎤 Start voice control";
          const url = new URL(window.top.location.href);
          url.searchParams.set("v", text);
          url.searchParams.set("vtime", String(Date.now()));
          window.top.location.href = url.toString();
        };

        recognition.onerror = (event) => {
          status.textContent = "Microphone error: " + event.error;
          button.textContent = "🎤 Start voice control";
        };

        recognition.onend = () => {
          button.textContent = "🎤 Start voice control";
          button.disabled = false;
        };
      }
    </script>
    """,
    height=90,
)

query_voice = st.query_params.get("v")
query_time = st.query_params.get("vtime")
if query_voice and query_time != st.session_state.get("processed_voice_time"):
    st.session_state.processed_voice_time = query_time
    parse_voice_command(query_voice)
    st.toast(f"Voice command: {query_voice}", icon="🎤")

active_count = sum(st.session_state.states.values())
active_watts = sum(
    PIN_MAP[name]["watt"]
    for name, active in st.session_state.states.items()
    if active
)

metric1, metric2, metric3, metric4 = st.columns(4)
metric1.metric("Active devices", f"{active_count} / 4")
metric2.metric("Live load", f"{active_watts} W")
metric3.metric("Today estimate", f"{active_watts * 24 / 1000:.2f} kWh")
metric4.metric("Board", st.session_state.board_port or "Offline")

st.divider()

st.subheader("My Devices")
device_columns = st.columns(4)
for column, (name, config) in zip(device_columns, PIN_MAP.items()):
    with column:
        is_on = st.session_state.states[name]
        status_class = "on" if is_on else "off"
        status_text = "ON" if is_on else "OFF"
        st.markdown(
            f"""
            <div class="device-card {'off' if not is_on else ''}">
              <span class="status-pill {status_class}">{status_text}</span>
              <div style="font-size:38px;margin-top:8px;">{config["icon"]}</div>
              <div class="device-name">{name}</div>
              <div class="device-room">{config["room"]} · Pin {config["pin"]} · {config["watt"]} W</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        is_on = st.toggle(
            f"Power {name}",
            value=st.session_state.states[name],
            key=f"toggle_{name}",
            label_visibility="collapsed",
        )
        if is_on != st.session_state.states[name]:
            set_device(name, is_on)

st.caption("Room controls")
control1, control2, control3, control4 = st.columns(4)
with control1:
    if st.button("All ON", use_container_width=True, type="primary"):
        set_all(True)
        st.rerun()
with control2:
    if st.button("All OFF", use_container_width=True, type="secondary"):
        set_all(False)
        st.rerun()
with control3:
    eco = st.toggle(
        "Eco mode",
        value=st.session_state.eco_mode,
        key="eco_mode_toggle",
    )
    if eco != st.session_state.eco_mode:
        apply_eco_mode(eco)
        st.rerun()
with control4:
    away = st.toggle(
        "Away mode",
        value=st.session_state.away_mode,
        key="away_mode_toggle",
    )
    if away != st.session_state.away_mode:
        apply_away_mode(away)
        st.rerun()

st.divider()
schedule1, schedule2, schedule3 = st.columns([1.2, 1, 1])
with schedule1:
    st.subheader("Smart Schedule")
    schedule_enabled = st.toggle(
        "Turn all devices off automatically",
        value=st.session_state.schedule_enabled,
        key="schedule_enabled",
    )
with schedule2:
    schedule_time = st.time_input(
        "Daily off time",
        value=datetime.strptime(st.session_state.schedule_time, "%H:%M").time(),
        step=300,
    )
    st.session_state.schedule_time = schedule_time.strftime("%H:%M")
with schedule3:
    idle = st.number_input(
        "Idle auto-off (minutes)",
        min_value=5,
        max_value=240,
        value=st.session_state.idle_minutes,
        step=5,
    )
    st.session_state.idle_minutes = int(idle)
    if st.button("Arm timer", use_container_width=True):
        st.session_state.auto_off_seconds = int(idle) * 60
        st.session_state.auto_off_started = time.time()
        st.session_state.last_action = f"Idle timer armed for {idle} minutes"
        st.success(st.session_state.last_action)

if st.session_state.auto_off_seconds > 0:
    elapsed = time.time() - st.session_state.get("auto_off_started", time.time())
    remaining = max(0, int(st.session_state.auto_off_seconds - elapsed))
    st.progress(
        1 - remaining / st.session_state.auto_off_seconds,
        text=f"Auto-off in {remaining // 60}:{remaining % 60:02d}",
    )
    if remaining == 0:
        set_all(False, "Auto-off timer")
        st.session_state.auto_off_seconds = 0
        st.toast("Auto-off timer completed", icon="⏰")
        st.rerun()

if st.session_state.schedule_enabled:
    now = datetime.now().strftime("%H:%M")
    schedule_marker = f"schedule_{datetime.now().strftime('%Y-%m-%d')}_{st.session_state.schedule_time}"
    if now >= st.session_state.schedule_time and st.session_state.get("schedule_last_run") != schedule_marker:
        set_all(False, "Smart schedule")
        st.session_state.schedule_last_run = schedule_marker
        st.toast("Smart schedule turned all devices off", icon="🌙")

st.divider()

tab1, tab2, tab3 = st.tabs(["Energy Analytics", "Recent Activity", "Arduino & Voice Help"])

with tab1:
    history = get_history()
    monthly_kwh = active_watts * 24 * 30 / 1000
    tariff = st.number_input(
        "Electricity tariff (per kWh)",
        min_value=0.0,
        max_value=1000.0,
        value=8.0,
        step=0.5,
    )
    monthly_cost = monthly_kwh * tariff
    daily_cost = active_watts * 24 / 1000 * tariff
    energy1, energy2, energy3 = st.columns(3)
    energy1.metric("Daily estimated cost", f"{daily_cost:.2f}")
    energy2.metric("Monthly estimated cost", f"{monthly_cost:.2f}")
    energy3.metric("Potential 20% savings", f"{monthly_cost * 0.20:.2f}")

    if history:
        recent = history[-100:]
        timestamps = [row["timestamp"][11:16] for row in recent]
        loads = []
        running = 0
        for row in recent:
            device_watts = PIN_MAP.get(row["device"], {}).get("watt", 0)
            if row["action"].startswith("ON"):
                running += device_watts
            elif row["action"].startswith("OFF"):
                running = max(0, running - device_watts)
            loads.append(running)

        chart_data = {
            "Time": timestamps,
            "Estimated load (W)": loads,
        }
        st.line_chart(chart_data, x="Time", y="Estimated load (W)")
        st.caption("Load estimates are derived from the watt values configured for each indicator.")
    else:
        st.info("Use a device to start generating analytics.")

with tab2:
    history = list(reversed(get_history()))[:30]
    if history:
        st.dataframe(history, use_container_width=True, hide_index=True)
    else:
        st.info("No control activity recorded yet.")

with tab3:
    st.markdown(
        """
        **Arduino setup**
        1. Install Arduino IDE and upload **StandardFirmata** (File > Examples > Firmata).
        2. Connect the Uno by USB.
        3. Enter the serial port (for example `COM3`) and select **Connect**.
        4. Pins 8, 9, 10, and 11 are digital outputs for Living Room, Bedroom, Kitchen, and Bathroom lights.

        **Voice examples**
        - `turn on living room light`
        - `turn off bedroom light`
        - `kitchen light on`
        - `bathroom light off`
        - `eco mode on`
        - `away mode on`
        - `all on`
        - `all off`

        Chrome or Edge is recommended for browser microphone access. Microphone access may require
        localhost or HTTPS.
        """
    )
