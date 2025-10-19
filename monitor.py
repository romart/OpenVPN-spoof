#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Tuple, Callable, Optional

DEFAULT_STATE_PATH = "/etc/openvpn/server/logs/state.json"
STATE_VERSION = 5  # bump for on-disk schema stability

# ---------- Colors / Symbols ----------
def make_style(no_color: bool, ascii_symbols: bool):
    if no_color or ascii_symbols:
        return {
            "reset": "",
            "on": "",
            "off": "",
            "sym_on": "[ON]" if ascii_symbols else "ON",
            "sym_off": "[OFF]" if ascii_symbols else "OFF",
            "use_color": False,
        }
    else:
        return {
            "reset": "\033[0m",
            "on": "\033[1;32m",   # bright green
            "off": "\033[0;90m",  # dim gray
            "sym_on": "●",
            "sym_off": "○",
            "use_color": True,
        }

# ---------- Humanize ----------
def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    x = float(n)
    while x >= 1024.0 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    return f"{x:.2f} {units[i]}"

# ---------- Parsing (status-version 3 preferred) ----------
CLIENT_LIST_RE = re.compile(r"^\s*CLIENT_LIST(?:[\t,])")
HEADER_CL_RE   = re.compile(r"^\s*HEADER[\t,]CLIENT_LIST[\t,]")
HEADER_RT_RE   = re.compile(r"^\s*HEADER[\t,]ROUTING_TABLE[\t,]")

def parse_date(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    # OpenVPN v3 uses "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def parse_status(path: str) -> Dict[str, Tuple[str, str, int, int, Optional[datetime], int, int]]:
    """
    Return dict by CN:
      CN -> (real_ip, virt_ip, rx, tx, since_dt, since_unix, peer_id)

    NOTES:
    - We follow your mapping where 'Bytes Sent' (from server) is what the client downloaded (RX),
      and 'Bytes Received' is what the client uploaded (TX). We'll name the accumulators RX/TX
      the same way your table shows them.
    """
    by_cn: Dict[str, Tuple[str, str, int, int, Optional[datetime], int, int]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return by_cn

    # Find the CLIENT_LIST block between HEADER,CLIENT_LIST and HEADER,ROUTING_TABLE
    start = end = -1
    for i, line in enumerate(lines):
        if start < 0 and HEADER_CL_RE.match(line):
            start = i
        elif start >= 0 and HEADER_RT_RE.match(line):
            end = i
            break

    if start < 0 or end < 0 or end <= start:
        return by_cn

    for line in lines[start + 1 : end]:
        if not CLIENT_LIST_RE.match(line):
            continue
        # normalize to commas
        parts = [p.strip() for p in line.replace("\t", ",").split(",")]
        # Columns (v3):
        # 0: CLIENT_LIST
        # 1: Common Name
        # 2: Real Address
        # 3: Virtual Address
        # 4: Virtual IPv6 Address
        # 5: Bytes Received
        # 6: Bytes Sent
        # 7: Connected Since (YYYY-MM-DD HH:MM:SS)
        # 8: Connected Since (time_t)
        # 9: Username
        # 10: Client ID
        # 11: Peer ID
        # 12: Data Channel Cipher
        if len(parts) < 12:
            continue
        cn   = parts[1]
        if not cn or cn == "UNDEF":
            continue

        real = parts[2]
        virt = parts[3]
        try:
            bytes_received = int(parts[5])  # server RX from client (client uploaded)
            bytes_sent     = int(parts[6])  # server TX to client (client downloaded)
        except ValueError:
            continue

        # Your semantics: show client RX (download) and TX (upload)
        rx = bytes_sent     # what client downloaded
        tx = bytes_received # what client uploaded

        since_dt = parse_date(parts[7])
        try:
            since_unix = int(parts[8])
        except ValueError:
            since_unix = 0

        try:
            peer_id = int(parts[11])
        except ValueError:
            peer_id = -1

        by_cn[cn] = (real, virt, rx, tx, since_dt, since_unix, peer_id)

    return by_cn

# ---------- State (no Optionals / flags; delta accounting) ----------
class ClientState:
    """
    Delta model:
      - base_rx/tx = raw counters at the *start* of the current session (or last tick if same session)
      - acc_rx/tx  = accumulated totals from *finished* ticks/sessions
      - On each tick in the same session:
          acc += (raw - base); base = raw
        (i.e., we add just the delta since last tick)
      - On session change (Peer ID or Connected-Since changes OR counters decrease):
          DO NOT add a delta for this tick; instead reset base to current raw,
          so the new session starts cleanly.
    """
    __slots__ = ("base_rx", "base_tx", "acc_rx", "acc_tx", "real", "virtual", "since", "peer_id")

    def __init__(self):
        self.base_rx: int = 0
        self.base_tx: int = 0
        self.acc_rx:  int = 0
        self.acc_tx:  int = 0
        self.real:    str = ""
        self.virtual: str = ""
        self.since:   Optional[datetime] = None
        self.peer_id: int = -1

    def total_rx(self) -> int:
        return self.acc_rx

    def total_tx(self) -> int:
        return self.acc_tx

# ---------- Screen ----------
def clear_screen():
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.flush()

def print_table(states: Dict[str, ClientState], style: dict, is_online: Callable[[str], bool]):
    # Header (two spaces between TX and Last Connected)
    print(
        f"St {'Client':<28} {'Real Address':<24} {'Virtual Address':<16}"
        f"{'RX Total':>12} {'TX Total':>12}  {'Last Connected Since':>22}  {'Peer ID':>10}"
    )
    print("-" * 148)

    items = sorted(
        ((st.total_rx() + st.total_tx(), cn) for cn, st in states.items()),
        key=lambda x: x[0],
        reverse=True
    )

    for _, cn in items:
        st = states[cn]
        online = is_online(cn)
        sym = style["sym_on"] if online else style["sym_off"]
        if style["use_color"]:
            sym_colored = (
                f"{style['on']}{sym}{style['reset']}" if online else f"{style['off']}{sym}{style['reset']}"
            )
        else:
            sym_colored = sym

        since_str = st.since.strftime("%Y-%m-%d %H:%M:%S") if (online and st.since) else ""

        print(
            f"{sym_colored} "
            f"{cn:<28} "
            f"{(st.real if online else 'N/A'):<24} "
            f"{(st.virtual if online else 'N/A'):<16} "
            f"{human_bytes(st.total_rx()):>12} "
            f"{human_bytes(st.total_tx()):>12}  "
            f"{since_str:>22}  "
            f"{(st.peer_id if online else '-'):>10}"
        )

    if not items:
        print("(waiting for clients...)")

# ---------- Persistence ----------
def load_state(path: str) -> Dict[str, ClientState]:
    data: Dict[str, ClientState] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return data
    except Exception:
        return data

    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return data

    payload = raw.get("clients", {})
    if not isinstance(payload, dict):
        return data

    for cn, obj in payload.items():
        try:
            st = ClientState()
            st.base_rx = int(obj.get("base_rx", 0))
            st.base_tx = int(obj.get("base_tx", 0))
            st.acc_rx  = int(obj.get("acc_rx", 0))
            st.acc_tx  = int(obj.get("acc_tx", 0))
            st.real    = str(obj.get("real", ""))[:128]
            st.virtual = str(obj.get("virtual", ""))[:64]
            since_iso  = obj.get("since_iso", "")
            st.since   = datetime.fromisoformat(since_iso) if since_iso else None
            st.peer_id = int(obj.get("peer_id", -1))
            data[cn] = st
        except Exception:
            continue
    return data

def atomic_write(path: str, text: str):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def save_state(path: str, states: Dict[str, ClientState]):
    payload = {
        "version": STATE_VERSION,
        "saved_at": int(time.time()),
        "clients": {
            cn: {
                "base_rx": st.base_rx,
                "base_tx": st.base_tx,
                "acc_rx": st.acc_rx,
                "acc_tx": st.acc_tx,
                "real": st.real,
                "virtual": st.virtual,
                "since_iso": (st.since.strftime("%Y-%m-%d %H:%M:%S") if st.since else ""),
                "peer_id": st.peer_id,
            }
            for cn, st in states.items()
        },
    }
    atomic_write(path, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(
        description="Realtime OpenVPN usage viewer (Python) — clean, session-aware, aligned, optional persistence"
    )
    ap.add_argument("status_file", nargs="?", default="/etc/openvpn/server/logs/vpn-udp-status.log",
                    help="Path to openvpn-status.log")
    ap.add_argument("interval", nargs="?", type=float, default=1.0,
                    help="Refresh interval seconds (default: 1)")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--ascii", action="store_true", help="Use ASCII symbols instead of ●/○")
    # Optional persistence:
    ap.add_argument("--persist", nargs="?", const=DEFAULT_STATE_PATH, default=None,
                    help=f"Enable persistence; optional path (default: {DEFAULT_STATE_PATH})")
    ap.add_argument("--persist-interval", type=int, default=5,
                    help="Seconds between state saves when --persist is set (default: 5)")

    args = ap.parse_args()
    style = make_style(no_color=args.no_color, ascii_symbols=args.ascii)
    states: Dict[str, ClientState] = load_state(args.persist) if args.persist else {}

    last_save = 0

    try:
        while True:
            snapshot = parse_status(args.status_file)  # CN -> (real, virt, rx, tx, since_dt, since_unix, pid)

            # Update per-CN state with safe delta accounting
            for cn, (real, virt, rx, tx, since_dt, _, peer_id) in snapshot.items():
                st = states.get(cn)
                if st is None:
                    # First sighting: initialize baselines; do NOT add to accumulators this tick
                    st = ClientState()
                    st.base_rx = rx
                    st.base_tx = tx
                    st.real    = real
                    st.virtual = virt
                    st.since   = since_dt
                    st.peer_id = peer_id
                    states[cn] = st
                    continue

                # Detect session change: new Peer ID OR new "Connected Since" OR counter decrease
                session_changed = False
                if st.peer_id != -1 and peer_id != st.peer_id:
                    session_changed = True
                elif st.since and since_dt and since_dt != st.since:
                    session_changed = True
                elif rx < st.base_rx or tx < st.base_tx:
                    session_changed = True

                if session_changed:
                    # Reset baselines to current raw counters; DO NOT add a delta this tick
                    st.base_rx = rx
                    st.base_tx = tx
                    st.since   = since_dt or st.since
                    st.peer_id = peer_id
                else:
                    # Same session → add delta since last tick, then move baseline to current
                    st.acc_rx += (rx - st.base_rx)
                    st.acc_tx += (tx - st.base_tx)
                    st.base_rx = rx
                    st.base_tx = tx
                    # keep since/peer_id as-is

                # Always refresh live addresses
                st.real    = real or st.real
                st.virtual = virt or st.virtual

            # Online/Offline: membership in snapshot
            is_online = lambda name: name in snapshot

            # Render
            clear_screen()
            print_table(states, style, is_online)
            sys.stdout.flush()

            # Persist periodically
            if args.persist:
                now = int(time.time())
                if now - last_save >= args.persist_interval:
                    try:
                        save_state(args.persist, states)
                        last_save = now
                    except Exception:
                        pass

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if args.persist:
            try:
                save_state(args.persist, states)
            except Exception:
                pass

if __name__ == "__main__":
    main()

