#!/usr/bin/env python3
"""
Automated Paper-Fund Settlement + News/Social Radar -> 3D Richman 4 RPG Office Check-In Sync (`auto_checkin_settlement.py`).
Runs automatically via GitHub Actions in `howardKung/project-lifeblood-rpg-office`
at Taiwan Time 08:22 (morning news brief), 14:38 (TWSE close), and 05:38 (US market close):
- Fetches latest `portfolio.json` from `howardKung/alpha-stock-warroom` ($0 cost, 0 AI quota).
- Fetches latest `news_feed.json` from `howardKung/alpha-stock-warroom` (gracefully skipped if missing).
- Updates `paper_fund_manager` (`百萬模擬操盤手`) and `news_social_radar` (`新聞輿情特派員`) in `state.json`.
- Inserts or updates today's automated check-in cards (`cost: 0`) at the top of `state.json["reports"]`.
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state.json"
TZ_TW = timezone(timedelta(hours=8))

PORTFOLIO_API_URL = "https://api.github.com/repos/howardKung/alpha-stock-warroom/contents/portfolio.json?ref=main"
PORTFOLIO_RAW_URL = "https://raw.githubusercontent.com/howardKung/alpha-stock-warroom/main/portfolio.json"
NEWS_RAW_URL = "https://raw.githubusercontent.com/howardKung/alpha-stock-warroom/main/news_feed.json"
NEWS_API_URL = "https://api.github.com/repos/howardKung/alpha-stock-warroom/contents/news_feed.json?ref=main"


def fetch_json_urls(candidates: list[tuple[str, str]]) -> Optional[dict]:
    for url, accept in candidates:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ProjectLifeblood-OfficeSync/1.0", "Accept": accept},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    return None


def fetch_portfolio() -> dict:
    data = fetch_json_urls([
        (PORTFOLIO_API_URL, "application/vnd.github.raw+json"),
        (PORTFOLIO_RAW_URL, "application/json"),
    ])
    if data is None:
        raise RuntimeError("Unable to fetch portfolio.json from alpha-stock-warroom")
    return data


def fetch_news_feed_optional() -> Optional[dict]:
    return fetch_json_urls([
        (NEWS_RAW_URL, "application/json"),
        (NEWS_API_URL, "application/vnd.github.raw+json"),
    ])


def upsert_report(state: dict, agent_id: str, auto_key: str, new_report: dict) -> None:
    reports = state.get("reports", [])
    existing_idx = next((i for i, r in enumerate(reports) if r.get("id") == auto_key), None)
    if existing_idx is not None:
        reports[existing_idx] = new_report
    else:
        reports.insert(0, new_report)
        for agent in state.get("agents", []):
            if agent.get("id") == agent_id:
                agent["tasksCompleted"] = int(agent.get("tasksCompleted", 0)) + 1
                break
    state["reports"] = reports[:40]


def sync_paper_fund(state: dict, pf: dict, now_tw: datetime, ts_short: str, today_str: str) -> None:
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
    auto_key = f"auto-settle-{report_date}-{'tw' if 12 <= now_tw.hour < 20 else 'us'}"

    summary_text = (
        f"【🤖 {report_date} {session_label}】總資產 NT${nav:,} "
        f"（當日 {d_chg_twd:+,} / {d_chg_pct:+.2f}%｜累計 {total_ret_twd:+,} / {total_ret_pct:+.2f}%）"
        f"｜現金 NT${cash:,}｜摘要：{first_action}"
    )
    task_text = (
        f"【0-額度自動排程】最新總淨值 NT${nav:,} ({total_ret_pct:+.2f}%)｜"
        f"現金 NT${cash:,}｜持倉 {pos_count} 檔・右側條件單 {pend_count} 檔"
    )

    for agent in state.get("agents", []):
        if agent.get("id") == "paper_fund_manager":
            agent["status"] = "working"
            agent["currentTask"] = task_text
            agent["lastUpdate"] = ts_short
            break

    upsert_report(
        state,
        "paper_fund_manager",
        auto_key,
        {
            "id": auto_key,
            "time": ts_short,
            "agentId": "paper_fund_manager",
            "agentName": "百萬模擬操盤手",
            "status": "working",
            "cost": 0,
            "summary": summary_text,
        },
    )
    print(f"[AUTO-CHECKIN] Synced {auto_key}: NAV=NT${nav:,}, Daily={d_chg_twd:+,} ({d_chg_pct:+.2f}%)")


def sync_news_social_radar(state: dict, news: Optional[dict], now_tw: datetime, ts_short: str, today_str: str) -> None:
    if not news:
        print("[AUTO-NEWS] news_feed.json not available yet; gracefully skipped.")
        return

    exec_brief = news.get("executiveBrief") or {}
    live_pulse = exec_brief.get("livePulse") or {}
    sections = news.get("sections") or {}

    top_headline = live_pulse.get("topMarketHeadline")
    if not top_headline:
        market_items = sections.get("marketAndAi") or []
        top_headline = market_items[0].get("title", "全球財經新聞持續監測中") if market_items else "全球財經新聞持續監測中"

    ptt_sentiment = live_pulse.get("pttSentiment") or "PTT 股版輿情掃描中"
    top_ptt = live_pulse.get("topPttPost") or ""
    top_reddit = live_pulse.get("topRedditTicker") or ""
    if not top_reddit:
        reddit_items = sections.get("redditHot") or []
        if reddit_items:
            r0 = reddit_items[0]
            top_reddit = f"${r0.get('ticker', '')} ({r0.get('mentions', 0)} 則 / {r0.get('momentum24hPct', 0):+d}%)"

    slot = "am" if now_tw.hour < 12 else ("tw" if now_tw.hour < 20 else "us")
    slot_label = "台美股早報" if slot == "am" else ("盤後輿情速報" if slot == "tw" else "美股夜盤風向")
    news_key = f"auto-news-{today_str}-{slot}"

    task_text = (
        f"【📺 NEWS 0-額度自動聯播】頭條：{top_headline[:46]}｜"
        f"PTT：{ptt_sentiment[:24]}｜Reddit：{top_reddit}"
    )
    summary_parts = [
        f"【📺 {today_str} {slot_label}】頭條：{top_headline}",
        f"PTT 股版風向：{ptt_sentiment}" + (f"（熱門：{top_ptt}）" if top_ptt else ""),
    ]
    if top_reddit:
        summary_parts.append(f"Reddit 熱度領頭：{top_reddit}")
    summary_text = "｜".join(summary_parts)

    for agent in state.get("agents", []):
        if agent.get("id") == "news_social_radar":
            agent["status"] = "working"
            agent["currentTask"] = task_text
            agent["lastUpdate"] = ts_short
            break

    upsert_report(
        state,
        "news_social_radar",
        news_key,
        {
            "id": news_key,
            "time": ts_short,
            "agentId": "news_social_radar",
            "agentName": "新聞輿情特派員",
            "status": "working",
            "cost": 0,
            "summary": summary_text,
        },
    )
    print(f"[AUTO-NEWS] Synced {news_key}: {top_headline}")


def main() -> None:
    pf = fetch_portfolio()
    news = fetch_news_feed_optional()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    now_tw = datetime.now(TZ_TW)
    ts_short = now_tw.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now_tw.strftime("%Y-%m-%d")

    sync_paper_fund(state, pf, now_tw, ts_short, today_str)
    sync_news_social_radar(state, news, now_tw, ts_short, today_str)

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
