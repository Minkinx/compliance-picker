#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
数据合规 RSS 推送流 — 每日合规资讯爬虫
基于 sec-picker (https://github.com/Minkinx/sec-picker) 逻辑构建
"""

import json
import os
import re
import sys
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import yaml
import requests

from utils import Color, getenv
from bot import init_bot

root_path = os.path.dirname(os.path.abspath(__file__))
today = datetime.now()
yesterday = today - timedelta(days=1)


def load_config(path: str = "") -> dict:
    if not path:
        path = os.path.join(root_path, "config.yml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_feed(url: str, proxy: dict | None = None, timeout: int = 30) -> list[dict]:
    """Fetch and parse a single RSS feed, return articles from yesterday."""
    articles = []
    try:
        if proxy:
            proxies = {"http": proxy.get("http"), "https": proxy.get("https")}
            resp = requests.get(url, proxies=proxies, timeout=timeout)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        else:
            feed = feedparser.parse(url)

        source_name = feed.feed.get("title", url)

        for entry in feed.entries:
            pub_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub_time:
                continue

            pub_dt = datetime(*pub_time[:6])
            # Accept articles from yesterday or today (crawler might run in morning)
            if pub_dt.date() < yesterday.date():
                continue

            articles.append({
                "title": entry.get("title", "无标题"),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", "")[:300],
                "source": source_name,
                "published": pub_dt.strftime("%Y-%m-%d %H:%M"),
            })
    except Exception as e:
        Color.print_failed(f"抓取失败 [{url}]: {e}")

    return articles


def extract_region_tags(article: dict) -> list[str]:
    """Auto-tag articles by region based on keywords in title/summary."""
    text = f"{article['title']} {article.get('summary', '')}".lower()
    tags = []

    region_keywords = {
        "欧盟": ["eu", "european union", "edpb", "gdpr", "eu commission", "europe", "schrems"],
        "美国": ["us", "usa", "united states", "ftc", "ccpa", "cpra", "california",
                  "fcc", "illinois", "biometric", "state privacy"],
        "中国": ["china", "chinese", "pipl", "cac", "网信", "数据安全", "个人信息", "工信部",
                  "网络安全", "数据出境"],
        "英国": ["uk", "united kingdom", "ico", "british"],
        "全球": ["global", "international", "oecd", "un", "world", "cross-border"],
        "亚太": ["india", "japan", "korea", "singapore", "australia", "apac",
                  "south korea", "pdpb", "pdpa", "dpdp"],
    }

    for region, keywords in region_keywords.items():
        if any(kw in text for kw in keywords):
            tags.append(region)

    return tags if tags else ["全球"]


def extract_topic_tags(article: dict) -> list[str]:
    """Auto-tag articles by compliance topic."""
    text = f"{article['title']} {article.get('summary', '')}".lower()
    tags = []

    topic_keywords = {
        "数据泄露": ["breach", "leak", "泄露", "泄露", "数据泄露", "cyberattack", "ransomware", "hack"],
        "执法罚款": ["fine", "penalty", "罚款", "enforcement", "sanction", "penalty", "cease and desist"],
        "立法动态": ["bill", "legislation", "regulation", "law", "proposal", "立法", "法案", "草案"],
        "数据跨境": ["cross-border", "transfer", "数据出境", "跨境", "adequacy", "scc", "binding corporate"],
        "AI合规": ["ai", "artificial intelligence", "algorithm", "machine learning", "人工智能"],
        "隐私技术": ["pets", "privacy enhancing", "encryption", "anonymization", "differential privacy",
                      "federated learning", "隐私计算"],
        "合规指南": ["guidance", "guideline", "framework", "best practice", "指南", "指引", "标准"],
        "诉讼": ["lawsuit", "litigation", "class action", "court", "诉讼", "法院", "裁决"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in text for kw in keywords):
            tags.append(topic)

    return tags


def deduplicate(articles: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for a in articles:
        key = a["title"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def crawl_all(config: dict) -> list[dict]:
    """Crawl all enabled RSS feeds in parallel."""
    rss_configs = config.get("rss", [])
    proxy = config.get("proxy", {})
    proxy_cfg = proxy if proxy.get("rss_proxy") else None

    enabled = [r for r in rss_configs if r.get("enabled")]
    Color.print_focus(f"开始抓取 {len(enabled)} 个合规资讯源...")

    all_articles = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_map = {
            executor.submit(fetch_feed, r["opml"], proxy_cfg): r
            for r in enabled
        }
        for future in as_completed(future_map):
            rss_cfg = future_map[future]
            try:
                articles = future.result()
                # Attach tags from config + auto-tags
                cfg_tags = rss_cfg.get("tags", [])
                for a in articles:
                    region_tags = extract_region_tags(a)
                    topic_tags = extract_topic_tags(a)
                    a["tags"] = list(set(cfg_tags + region_tags + topic_tags))
                all_articles.extend(articles)
                Color.print_success(f"[{rss_cfg['name']}] 获取 {len(articles)} 条")
            except Exception as e:
                Color.print_failed(f"[{rss_cfg['name']}] 失败: {e}")

    articles = deduplicate(all_articles)
    articles.sort(key=lambda x: x.get("published", ""), reverse=True)
    Color.print_success(f"共获取 {len(articles)} 条去重合规资讯")
    return articles


def update_today(articles: list[dict], path: str = ""):
    """Generate today.md with categorized compliance news."""
    if not path:
        path = os.path.join(root_path, "today.md")

    date_str = yesterday.strftime("%Y-%m-%d")
    md = [
        f"# 数据合规资讯日报 — {date_str}\n",
        f"> 📡 共 {len(articles)} 条资讯\n",
        "---\n",
    ]

    # Group by region
    region_order = ["中国", "欧盟", "美国", "英国", "亚太", "全球", "国际"]
    regions = {}
    for a in articles:
        a_regions = [t for t in a.get("tags", []) if t in region_order]
        region = a_regions[0] if a_regions else "其他"
        regions.setdefault(region, []).append(a)

    for region in region_order:
        if region not in regions:
            continue
        md.append(f"\n## 🌍 {region}\n")
        for a in regions[region]:
            topic_tags = [t for t in a.get("tags", []) if t not in region_order]
            tag_str = " ".join(f"`{t}`" for t in topic_tags) if topic_tags else ""
            md.append(f"- [{a['title']}]({a['link']}) {tag_str}")
        md.append("")

    content = "\n".join(md)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    Color.print_success(f"已生成 {path}")

    # Save to archive
    archive_dir = os.path.join(root_path, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, f"{date_str}.md")
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Save JSON for bot processing
    tmp_dir = os.path.join(root_path, "archive", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    json_path = os.path.join(tmp_dir, f"{date_str}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def push_articles(articles: list[dict], bots: list, title: str = ""):
    """Push articles to all configured bots."""
    if not articles:
        Color.print_focus("没有文章需要推送")
        return

    for bot in bots:
        try:
            msgs = bot.parse_results(articles, title=title or f"📋 数据合规日报 {yesterday.strftime('%Y-%m-%d')}")
            if isinstance(msgs, list):
                for msg in msgs:
                    bot.send(msg)
            else:
                bot.send(msgs)
        except Exception as e:
            Color.print_failed(f"{bot.__class__.__name__} 推送失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="数据合规 RSS 推送流")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--test", action="store_true", help="使用测试数据")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.test:
        # Generate test data
        articles = []
        for i in range(20):
            articles.append({
                "title": f"测试合规资讯 #{i+1}",
                "link": "https://example.com",
                "summary": "这是一条测试数据",
                "source": "Test Feed",
                "published": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "tags": ["中国", "执法罚款", "GDPR"] if i % 2 == 0 else ["欧盟", "数据泄露"],
            })
        Color.print_focus("使用测试数据模式")
    else:
        articles = crawl_all(config)

    # Save & output
    update_today(articles)
    push_articles(articles, init_bot(config))

    Color.print_success(f"✅ 数据合规日报 {yesterday.strftime('%Y-%m-%d')} 完成")


if __name__ == "__main__":
    main()
