
#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Tuple, Optional

DEFAULT_STATE_PATH = "/etc/openvpn/server/logs/state.json"

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

def parse_status(path: str) -> List[Tuple[str, str, int, int, str]]:
    """
    Returns rows: (cn, real, rx, tx, since)
    Supports:
      - v3 lines: CLIENT_LIST <sep> CN <sep> Real <sep> ... <sep> RX <sep> TX <sep> Since ...
      - v2 CSV with header
    """
    rows: List[Tuple[str, str, int, int, str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return rows

    # First pass: try v3 CLIENT_LIST (tab or comma separated)
    v3_hits = 0
    for line in lines:
        if CLIENT_LIST_RE.match(line):
            normalized = line.replace("\t", ",")
            parts = [p.strip() for p in normalized.split(",")]
            # Expected indexes:
            # 0=CLIENT_LIST, 1=CN, 2=Real, 5=RX, 6=TX, 7=Since, 8=UnixTime, ...
            if len(parts) >= 8:
                cn = parts[1]
                if not cn or cn == "UNDEF":
                    continue
                real = parts[2] if len(parts) > 2 else ""
                try:
                    rx = int(parts[5]) if len(parts) > 5 else 0
                    tx = int(parts[6]) if len(parts) > 6 else 0
                except ValueError:
                    rx = 0
                    tx = 0
                since = parts[7] if len(parts) > 7 else ""
                rows.append((cn, real, rx, tx, since))
                v3_hits += 1

    if v3_hits > 0:
        return rows

    # Second pass: v2 CSV
    header = "Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since"
    try:
        start = lines.index(header)
    except ValueError:
        start = -1

    if start >= 0:
        for line in lines[start + 1 :]:
            if line.startswith("ROUTING TABLE") or line.startswith("ROUTING_TABLE"):
                break
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            cn = parts[0]
            if not cn or cn == "UNDEF" or cn == "Common Name":
                continue
            real = parts[1]
            try:
                rx = int(parts[2]); tx = int(parts[3])
            except ValueError:
                continue
            since = parts[4]
            rows.append((cn, real, rx, tx, since))

    return rows

# ---------- State ----------
class ClientState:
    __slots__ = ("last_rx", "last_tx", "total_rx", "total_tx", "real", "since", "last_seen")
    def __init__(self):
        self.last_rx: Optional[int] = None
        self.last_tx: Optional[int] = None
        self.total_rx: int = 0
        self.total_tx: int = 0
        self.real: str = ""
        self.since: str = ""
        self.last_seen: int = 0  # epoch seconds

def clear_screen():
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.flush()

def print_table(states: Dict[str, ClientState], now: int, grace: int, style: dict):
    # Header
    print(
        f"St {'Client':<28} {'Real Address':<24} "
        f"{'RX Total':>12} {'TX Total':>12}  {'Last Connected Since':<19}"
    )
    print("-" * 123)  # slightly longer separator

    # Sort by total traffic desc
    items = sorted(
        ((st.total_rx + st.total_tx, cn) for cn, st in states.items()),
        key=lambda x: x[0],
        reverse=True
    )

    for _, cn in items:
        st = states[cn]
        online = (st.last_seen > 0 and (now - st.last_seen) <= grace)
        sym = style["sym_on"] if online else style["sym_off"]
        if style["use_color"]:
            sym_colored = (
                f"{style['on']}{sym}{style['reset']}" if online
                else f"{style['off']}{sym}{style['reset']}"
            )
        else:
            sym_colored = sym
        since = (st.since or "")[:19]
        # Note: two spaces before "Last Connected Since"
        print(
            f"{sym_colored} "
            f"{cn:<28} "
            f"{(st.real or ''):<24} "
            f"{human_bytes(st.total_rx):>12} "
            f"{human_bytes(st.total_tx):>12}  "
            f"{since:<19}"
        )

    if not items:
        print("(waiting for clients...)")


# ---------- Persistence ----------
STATE_VERSION = 1

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
            st.total_rx = int(obj.get("total_rx", 0))
            st.total_tx = int(obj.get("total_tx", 0))
            st.real = str(obj.get("real", ""))[:128]
            st.since = str(obj.get("since", ""))[:64]
            st.last_seen = int(obj.get("last_seen", 0))
            # Do NOT restore last_rx/last_tx (raw counters) — they will re-init on first tick.
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
                "total_rx": st.total_rx,
                "total_tx": st.total_tx,
                "real": st.real,
                "since": st.since,
                "last_seen": st.last_seen,
            }
            for cn, st in states.items()
        },
    }
    atomic_write(path, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(description="Realtime OpenVPN usage viewer (Python) with optional persistence")
    ap.add_argument("status_file", nargs="?", default="/etc/openvpn/server/logs/vpn-udp-status.log",
                    help="Path to openvpn-status.log")
    ap.add_argument("interval", nargs="?", type=float, default=1.0,
                    help="Refresh interval seconds (default: 1)")
    ap.add_argument("grace", nargs="?", type=int, default=10,
                    help="Seconds since last seen to keep Online (default: 10)")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--ascii", action="store_true", help="Use ASCII symbols instead of ●/○")
    # Optional persistence flag with optional value:
    #   --persist            -> uses DEFAULT_STATE_PATH
    #   --persist /path.json -> uses custom path
    ap.add_argument("--persist", nargs="?", const=DEFAULT_STATE_PATH, default=None,
                    help=f"Enable persistence; optional path (default: {DEFAULT_STATE_PATH})")
    ap.add_argument("--persist-interval", type=int, default=5,
                    help="How often to save state in seconds (default: 5)")
    args = ap.parse_args()

    style = make_style(no_color=args.no_color, ascii_symbols=args.ascii)

    # Load state if requested
    states: Dict[str, ClientState]
    if args.persist:
        states = load_state(args.persist)
    else:
        states = {}

    last_save = 0

    try:
        while True:
            now = int(time.time())
            rows = parse_status(args.status_file)

            seen_this_tick = set()

            # Update states
            for cn, real, rx, tx, since in rows:
                st = states.get(cn)
                if st is None:
                    st = ClientState()
                    states[cn] = st

                # delta logic with reconnect reset detection
                if st.last_rx is not None:
                    d_rx = rx - st.last_rx
                    if d_rx < 0:
                        d_rx = rx
                else:
                    d_rx = 0

                if st.last_tx is not None:
                    d_tx = tx - st.last_tx
                    if d_tx < 0:
                        d_tx = tx
                else:
                    d_tx = 0

                st.total_rx += max(0, d_rx)
                st.total_tx += max(0, d_tx)
                st.last_rx = rx
                st.last_tx = tx
                if real:
                    st.real = real
                if since:
                    st.since = since
                st.last_seen = now
                seen_this_tick.add(cn)

            # Render
            clear_screen()
            print_table(states, now, args.grace, style)
            sys.stdout.flush()

            # Periodic save if persistence enabled
            if args.persist and (now - last_save >= args.persist_interval):
                try:
                    save_state(args.persist, states)
                    last_save = now
                except Exception:
                    # Non-fatal: just skip this save
                    pass

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        # Final save on exit if persistence enabled
        if args.persist:
            try:
                save_state(args.persist, states)
            except Exception:
                pass

if __name__ == "__main__":
    main()
