#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Tuple, Callable

DEFAULT_STATE_PATH = "/etc/openvpn/server/logs/state.json"
STATE_VERSION = 4  # bumped for the cleaned layout

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

# ---------- Parsing ----------
CLIENT_LIST_RE = re.compile(r"^\s*CLIENT_LIST(?:[\t,])")

def parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d %H:%M:%S")

def parse_status(path: str) -> Dict[str, Tuple[str, str, int, int, datetime, int, int]]:
    by_cn: Dict[str, Tuple[str, str, int, int, datetime, int, int]] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return by_cn

    # v2 CSV
    cl_header = "HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID,Data Channel Cipher"
    rt_header = "HEADER,ROUTING_TABLE,Virtual Address,Common Name,Real Address,Last Ref,Last Ref (time_t)"
    try:
        start = lines.index(cl_header)
        end = lines.index(rt_header)
    except ValueError:
        return by_cn

    for line in lines[start + 1:end]:
        parts = [p.strip() for p in line.split(",")]
        assert parts[0] == "CLIENT_LIST"
        cn = parts[1]
        rip = parts[2]
        vip = parts[3]
        # this swap is intentional, seems 'Sent' means the client downloaded and 'Recieved' -- uploaded
        tx = int(parts[5])
        rx = int(parts[6])
        since_d = parse_date(parts[7])
        since_t = int(parts[8])
        pid = int(parts[11])

        by_cn[cn] = (rip, vip, rx, tx, since_d, since_t, pid)

    return by_cn

# ---------- State (clean: no Optionals, no flags) ----------
class ClientState:
    __slots__ = (
        "base_rx", "base_tx",     # session baseline raw counters (at session start)
        "real", "virtual",
        "acc_rx", "acc_tx",       # accumulated totals across finished sessions
        "since", "pid"          # latest real address, current session "since"
    )
    def __init__(self):
        self.base_rx: int = 0
        self.base_tx: int = 0

        self.acc_rx: int = 0
        self.acc_tx: int = 0

        self.real: str = ""
        self.virtual: str = ""

        self.since: datetime = None
        self.pid = -1

    def total_rx(self) -> int:
        return self.acc_rx;

    def total_tx(self) -> int:
        return self.acc_tx;

# ---------- Screen ----------
def clear_screen():
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.flush()

def print_table(states: Dict[str, ClientState], style: dict, is_online: Callable[[str], bool]):
    # Header (two spaces between TX and Last Connected)
    print(
        f"St {'Client':<28} {'Real Address':<24} {'Virtual Address':<16}"
        f"{'RX Total':>12} {'TX Total':>12}  {'Last Connected Since':>22}  {'Peer ID':>10}")

    print("-" * 148)

    # Sort by total traffic desc
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
                f"{style['on']}{sym}{style['reset']}"
                if online else f"{style['off']}{sym}{style['reset']}"
            )
        else:
            sym_colored = sym

        print(
            f"{sym_colored} "
            f"{cn:<28} "
            f"{(st.real if online else 'N/A'):<24} "
            f"{(st.virtual if online else 'N/A'):<16} "
            f"{human_bytes(st.total_rx()):>12} "
            f"{human_bytes(st.total_tx()):>12}  "
            f"{(str(st.since) if online else ''):>22}  "
            f"{(st.pid if online else '-'):>10}"
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
            st.base_rx   = int(obj.get("base_rx", 0))
            st.base_tx   = int(obj.get("base_tx", 0))
            st.acc_rx    = int(obj.get("acc_rx", 0))
            st.acc_tx    = int(obj.get("acc_tx", 0))
            st.real      = ""
            st.virtual   = ""
            st.since     = parse_date(obj.get("since", ""))
            st.pid       = int(obj.get("pid", "-2"))
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
                "since": str(st.since),
                "pid": st.pid,
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
    #   --persist            -> uses ./state.json
    #   --persist /path.json -> custom path
    ap.add_argument("--persist", nargs="?", const=DEFAULT_STATE_PATH, default=None,
                    help=f"Enable persistence; optional path (default: {DEFAULT_STATE_PATH})")
    args = ap.parse_args()

    style = make_style(no_color=args.no_color, ascii_symbols=args.ascii)
    states: Dict[str, ClientState] = load_state(args.persist) if args.persist else {}

    last_save = 0

    try:
        while True:
            now = int(time.time())
            # clear_screen()
            snapshot = parse_status(args.status_file)  # CN -> (real, rx, tx, since)

            for cn, (rip, vip, rx, tx, since, _, pid) in snapshot.items():
                st = states.get(cn)
                if st is None:
                    st = ClientState()
                    states[cn] = st
                    # first sighting → lock baseline at current raw counters
                    st.acc_rx = st.base_rx = rx
                    st.acc_tx = st.base_tx = tx
                    st.since  = since
                    st.real = rip
                    st.virtual = vip
                    st.pid = pid
                else:
                    if pid != st.pid and since != st.since:
                        st.base_rx = 0
                        st.base_tx = 0

                    st.acc_rx += (rx - st.base_rx)
                    st.acc_tx += (tx - st.base_tx)
                    # start new baseline at current raw counters
                    st.base_rx = rx
                    st.base_tx = tx
                    st.since   = since
                    st.real = rip
                    st.virtual = vip


            is_online = lambda n: n in snapshot
            # Render
            clear_screen()
            print_table(states, style, is_online)
            sys.stdout.flush()

            # Periodic save
            if args.persist:
                try:
                    save_state(args.persist, states)
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

