import platform
import subprocess
import re
import socket
import threading
import time
import csv
import argparse
from datetime import datetime, timezone   # ✅ added timezone
from typing import Dict, Any, List, Tuple

IS_WINDOWS = platform.system().lower().startswith("win")

def run_subprocess(cmd: List[str], timeout: int = 10) -> Tuple[bool, str]:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return True, completed.stdout + completed.stderr
    except Exception as e:
        return False, f"Error running {' '.join(cmd)}: {e}"

def ping_host(host: str, count: int = 4, timeout: int = 4) -> Dict[str, Any]:
    if IS_WINDOWS:
        cmd = ["ping", host, "-n", str(count)]
    else:
        cmd = ["ping", "-c", str(count), host]

    ok, out = run_subprocess(cmd, timeout=timeout + 2)
    result = {"host": host, "success": False, "raw": out, "sent": None, "received": None, "loss_pct": None, "avg_rtt_ms": None}
    if not ok:
        return result

    text = out

    # Parse packet loss
    m = re.search(r"(\d+)\s+packets transmitted.*?(\d+)\s+received.*?([0-9]+)%\s+packet loss", text, re.S)
    if m:
        sent = int(m.group(1)); received = int(m.group(2)); loss = float(m.group(3))
        result.update({"sent": sent, "received": received, "loss_pct": loss})
    else:
        m2 = re.search(r"Sent = (\d+), Received = (\d+), Lost = (\d+)", text)
        if m2:
            sent = int(m2.group(1)); received = int(m2.group(2)); lost = int(m2.group(3))
            loss = (lost / sent) * 100 if sent else None
            result.update({"sent": sent, "received": received, "loss_pct": loss})

    # Parse avg RTT
    m3 = re.search(r"rtt [\w/]+ = [\d\.]+/([\d\.]+)/[\d\.]+/[\d\.]+ ms", text)
    if m3:
        try:
            result["avg_rtt_ms"] = float(m3.group(1))
        except:
            pass
    else:
        m4 = re.search(r"Average = (\d+)ms", text)
        if m4:
            try:
                result["avg_rtt_ms"] = float(m4.group(1))
            except:
                pass

    if result.get("received") and result["received"] > 0:
        result["success"] = True
    elif "ttl=" in text.lower():
        result["success"] = True

    return result

def traceroute_host(host: str, max_hops: int = 30) -> Dict[str, Any]:
    if IS_WINDOWS:
        cmd = ["tracert", "-h", str(max_hops), host]
    else:
        cmd = ["traceroute", "-m", str(max_hops), host]

    ok, out = run_subprocess(cmd, timeout=60)
    return {"host": host, "success": ok, "raw": out}

def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> Dict[str, Any]:
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = (time.time() - start) * 1000.0
            return {"host": host, "port": port, "open": True, "rtt_ms": elapsed}
    except Exception as e:
        elapsed = (time.time() - start) * 1000.0
        return {"host": host, "port": port, "open": False, "error": str(e), "rtt_ms": elapsed}

def dns_lookup(name: str) -> Dict[str, Any]:
    try:
        ip = socket.gethostbyname(name)
        return {"name": name, "ip": ip, "success": True}
    except Exception as e:
        return {"name": name, "ip": None, "success": False, "error": str(e)}

def run_diagnostics(hosts: List[str], ports: List[int] = None, ping_count: int = 3) -> List[Dict[str, Any]]:
    if ports is None:
        ports = []

    results = []
    for h in hosts:
        print(f"--- Running checks for: {h} ---")
        t0 = datetime.now(timezone.utc).isoformat()   # ✅ fixed here
        dns = dns_lookup(h)
        ping = ping_host(h, count=ping_count)
        tr = traceroute_host(h, max_hops=20)
        port_results = []
        for p in ports:
            port_results.append(check_tcp_port(h, p))

        record = {
            "timestamp": t0,
            "host": h,
            "dns": dns,
            "ping": ping,
            "traceroute": {"raw": tr["raw"][:400] + ("...[truncated]" if len(tr["raw"])>400 else "")},
            "ports": port_results
        }
        results.append(record)
    return results

def save_results_csv(results: List[Dict[str, Any]], filename: str = "net_diagnostics_log.csv"):
    rows = []
    for r in results:
        base = {
            "timestamp": r["timestamp"],
            "host": r["host"],
            "dns_ip": r["dns"].get("ip"),
            "dns_ok": r["dns"].get("success"),
            "ping_sent": r["ping"].get("sent"),
            "ping_recv": r["ping"].get("received"),
            "ping_loss_pct": r["ping"].get("loss_pct"),
            "ping_avg_rtt_ms": r["ping"].get("avg_rtt_ms"),
            "traceroute_snippet": r["traceroute"].get("raw"),
        }
        if r["ports"]:
            for p in r["ports"]:
                row = base.copy()
                row.update({"port": p["port"], "port_open": p.get("open"), "port_rtt_ms": p.get("rtt_ms")})
                rows.append(row)
        else:
            rows.append(base)

    keys = ["timestamp","host","dns_ip","dns_ok","ping_sent","ping_recv","ping_loss_pct",
            "ping_avg_rtt_ms","port","port_open","port_rtt_ms","traceroute_snippet"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Saved results to {filename}")

# ✅ New telemetry monitor
def monitor_hosts(hosts, ports=None, interval=30, duration=300):
    start = time.time()
    while time.time() - start < duration:
        results = run_diagnostics(hosts, ports=ports, ping_count=2)
        save_results_csv(results, filename="telemetry_log.csv")
        print(f"[{time.strftime('%H:%M:%S')}] Logged results for {len(hosts)} hosts.")
        time.sleep(interval)

def main_cli():
    parser = argparse.ArgumentParser(description="Simple Automated Network Troubleshooter")
    parser.add_argument("--hosts", nargs="+", required=True, help="Hosts to diagnose (hostname or IP)")
    parser.add_argument("--ports", nargs="*", type=int, default=[], help="TCP ports to check (e.g. 22 80 443)")
    parser.add_argument("--count", type=int, default=3, help="Ping count per host")
    parser.add_argument("--out", type=str, default="net_diagnostics_log.csv", help="CSV output filename")
    parser.add_argument("--telemetry", nargs=2, type=int, help="Enable telemetry: <interval_seconds> <duration_seconds>")
    args = parser.parse_args()

    if args.telemetry:
        interval, duration = args.telemetry
        monitor_hosts(args.hosts, ports=args.ports, interval=interval, duration=duration)
    else:
        results = run_diagnostics(args.hosts, ports=args.ports, ping_count=args.count)
        save_results_csv(results, filename=args.out)

if __name__ == "__main__":
    main_cli()
