#!/usr/bin/env python3
"""
Automated Paper-Fund Settlement -> 3D Richman 4 RPG Office Check-In Sync (`auto_checkin_settlement.py`).
Runs automatically via GitHub Actions in `howardKung/project-lifeblood-rpg-office`
5 minutes after TWSE (14:35 Taiwan Time) and US Market (05:35 Taiwan Time) settlements:
- Fetches latest `portfolio.json` from `howardKung/alpha-stock-warroom` ($0 cost, 0 AI quota).
- Updates `paper_fund_manager` (`百萬模擬操盤手`) status, Taiwan timestamp, and task in `state.json`.
- Inserts or updates today's automated PnL & NAV check-in card at the top of `state.json["reports"]`.
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state.json"
TZ_TW = timezone(timedelta(hours=8))

PORTFOLIO_API_URL = "https://api.github.com/repos/howardKung/alpha-stock-warroom/contents/portfolio.json?ref=main"
PORTFOLIO_RAW_URL = "https://raw.githubusercontent.com/howardKung/alpha-stock-warroom/main/portfolio.json"


def fetch_portfolio() -> dict:
    for url, accept in [
        (PORTFOLIO_API_URL, "application/vnd.github.raw+json"),
        (PORTFOLIO_RAW_URL, "application/json"),
    ]:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ProjectLifeblood-OfficeSync/1.0", "Accept": accept},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    raise RuntimeError("Unable to fetch portfolio.json from alpha-stock-warroom")


def main() -> None:
    pf = fetch_portfolio()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    now_tw = datetime.now(TZ_TW)
    ts_short = now_tw.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now_tw.strftime("%Y-%m-%d")

    s = pf.get("summary", {})
    nav = int(s.get("navTwd", 1000000))
    cash = int(s.get("cashTwd", 0))
    total_ret_twd = int(s.get("totalReturnTwd", 0))
    total_ret_pct = float(s.get("totalReturnPct", 0.0))
    pos_count = len(pf.get("positions", []))
    pend_count = len(pf.get("pendingOrders", []))

    latest_daily = (pf.get("dailyReports") or [{}])[0]
    report_date = latest_daily.get("date", today_str)
    d_chg_twd = int(latest_daily.get("dailyChangeTwd", total_ret_twd))
    d_chg_pct = float(latest_daily.get("dailyChangePct", total_ret_pct))
    actions = latest_daily.get("actions") or []
    first_action = actions[0] if actions else "現有部位續抱追蹤中"

    session_label = "台股盤後結算" if 12 <= now_tw.hour < 20 else "美股盤後／全日結算"
    auto_key = f"auto-settle-{report_date}-{ 'tw' if 12 <= now_tw.hour < 20 else 'us' }"

    summary_text = (
        f"【🤖 {report_date} {session_label}】總資產 NT${nav:,} "
        f"（當日 {d_chg_twd:+,} / {d_chg_pct:+.2f}%｜累計 {total_ret_twd:+,} / {total_ret_pct:+.2f}%）"
        f"｜現金 NT${cash:,}｜摘要：{first_action}"
    )
    task_text = (
        f"【0-額度自動排程】最新總淨值 NT${nav:,} ({total_ret_pct:+.2f}%)｜"
        f"現金 NT${cash:,}｜持倉 {pos_count} 檔・右側條件單 {pend_count} 檔"
    )

    # Update paper_fund_manager agent entry
    for agent in state.get("agents", []):
        if agent.get("id") == "paper_fund_manager":
            agent["status"] = "working"
            agent["currentTask"] = task_text
            agent["lastUpdate"] = ts_short
            break

    # Deduplicate same day+session auto report, update in place or insert at top
    reports = state.get("reports", [])
    new_report = {
        "id": auto_key,
        "time": ts_short,
        "agentId": "paper_fund_manager",
        "agentName": "百萬模擬操盤手",
        "status": "working",
        "cost": 0,
        "summary": summary_text,
    }

    existing_idx = next((i for i, r in enumerate(reports) if r.get("id") == auto_key), None)
    if existing_idx is not None:
        reports[existing_idx] = new_report
    else:
        reports.insert(0, new_report)
        for agent in state.get("agents", []):
            if agent.get("id") == "paper_fund_manager":
                agent["tasksCompleted"] = int(agent.get("tasksCompleted", 0)) + 1
                break

    state["reports"] = reports[:40]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[AUTO-CHECKIN] Synced {auto_key}: NAV=NT${nav:,}, Daily={d_chg_twd:+,} ({d_chg_pct:+.2f}%)")


if __name__ == "__main__":
    main()
