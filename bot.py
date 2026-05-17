import json
import hashlib
import hmac
import base64
import time
import smtplib
import ssl
import subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

import requests
from pyrate_limiter import Duration, Rate, Limiter
from utils import Color


def get_smtp(sender: str) -> tuple:
    domain = sender.split("@")[1].lower()
    smtp_map = {
        "qq.com":     ("smtp.qq.com", 465),
        "163.com":    ("smtp.163.com", 465),
        "gmail.com":  ("smtp.gmail.com", 465),
        "outlook.com":("smtp-mail.outlook.com", 587),
        "foxmail.com":("smtp.qq.com", 465),
        "aliyun.com": ("smtp.aliyun.com", 465),
    }
    return smtp_map.get(domain, (f"smtp.{domain}", 465))


class FeishuBot:
    def __init__(self, key: str):
        self.key = key
        self.webhook = f"https://open.feishu.cn/open-apis/bot/v2/hook/{key}"

    def parse_results(self, results: list, picks: list | None = None, title: str = "") -> list[dict]:
        cards = []
        articles = picks if picks else results
        for i in range(0, len(articles), 45):
            batch = articles[i:i + 45]
            elements = []
            for a in batch:
                tags_str = " ".join(f"<tag>{t}</tag>" for t in a.get("tags", []))
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"[{a['title']}]({a['link']})\n{tags_str}"}
                })
            cards.append({
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "lark_md", "content": title or f"📋 数据合规资讯 ({len(batch)}条)"},
                        "template": "blue"
                    },
                    "elements": elements
                }
            })
        return cards

    def parse_pick(self, article: dict) -> dict:
        tags_str = " ".join(f"<tag>{t}</tag>" for t in article.get("tags", []))
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "lark_md", "content": f"⭐ 精选: {article['title']}"},
                    "template": "indigo"
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**来源:** {article.get('source','')}\n**链接:** {article['link']}\n\n{tags_str}"}}
                ]
            }
        }

    def send(self, msg: dict):
        requests.post(self.webhook, json=msg, timeout=15)
        Color.print_success(f"飞书推送成功")

    def send_raw(self, content: str):
        msg = {"msg_type": "text", "content": {"text": content}}
        requests.post(self.webhook, json=msg, timeout=15)


class WecomBot:
    def __init__(self, key: str):
        self.webhook = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        self.limiter = Limiter(Rate(20, Duration.MINUTE))

    def parse_results(self, results: list, picks: list | None = None, title: str = "") -> list[str]:
        articles = picks if picks else results
        lines = [f"## {title or '数据合规资讯'}\n"]
        for a in articles:
            tags_str = " ".join(f"`{t}`" for t in a.get("tags", []))
            lines.append(f"- [{a['title']}]({a['link']}) {tags_str}")
        chunks = []
        for i in range(0, len(lines), 50):
            chunks.append("\n".join(lines[:1] + lines[i+1:i+51]))
        return chunks

    def send(self, msg: str):
        self.limiter.try_acquire("wecom")
        payload = {"msgtype": "markdown", "markdown": {"content": msg}}
        requests.post(self.webhook, json=payload, timeout=15)
        Color.print_success(f"企微推送成功")


class DingtalkBot:
    def __init__(self, access_token: str, secret: str):
        self.access_token = access_token
        self.secret = secret
        timestamp = str(round(time.time() * 1000))
        sign_str = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode(), sign_str.encode(), digestmod=hashlib.sha256).digest()
        ).decode()
        self.webhook = (
            f"https://oapi.dingtalk.com/robot/send?access_token={access_token}"
            f"&timestamp={timestamp}&sign={sign}"
        )
        self.limiter = Limiter(Rate(19, Duration.MINUTE))

    def parse_results(self, results: list, picks: list | None = None, title: str = "") -> list[dict]:
        articles = picks if picks else results
        chunks = []
        for i in range(0, len(articles), 20):
            batch = articles[i:i + 20]
            text = f"## {title or '数据合规资讯'}\n"
            for a in batch:
                tags_str = " ".join(a.get("tags", []))
                text += f"- [{a['title']}]({a['link']}) {tags_str}\n"
            chunks.append({"msgtype": "markdown", "markdown": {"title": "合规资讯", "text": text}})
        return chunks

    def parse_pick(self, article: dict) -> dict:
        tags_str = " | ".join(article.get("tags", []))
        text = (
            f"## ⭐ 精选: {article['title']}\n"
            f"> **来源:** {article.get('source','')}\n"
            f"> **链接:** {article['link']}\n"
            f"> **标签:** {tags_str}\n"
        )
        return {"msgtype": "markdown", "markdown": {"title": "合规精选", "text": text}}

    def send(self, msg: dict):
        self.limiter.try_acquire("dingtalk")
        requests.post(self.webhook, json=msg, timeout=15)
        Color.print_success(f"钉钉推送成功")


class MailBot:
    def __init__(self, sender: str, key: str, receiver: str, smtp_host: str = "", smtp_port: int = 465):
        self.sender = sender
        self.key = key
        self.receiver = receiver
        if smtp_host:
            self.smtp_host = smtp_host
            self.smtp_port = smtp_port
        else:
            self.smtp_host, self.smtp_port = get_smtp(sender)

    def parse_results(self, results: list, picks: list | None = None, title: str = "") -> str:
        articles = picks if picks else results
        html = f"<h2>{title or '数据合规资讯'}</h2><hr>"
        for a in articles:
            tags = " ".join(f"<code>{t}</code>" for t in a.get("tags", []))
            html += f"<h3><a href=\"{a['link']}\">{a['title']}</a></h3><p>{tags}</p>"
        return html

    def send(self, html: str):
        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender
        msg["To"] = self.receiver
        msg["Subject"] = Header("数据合规资讯", "utf-8")
        msg.attach(MIMEText(html, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=ctx) as server:
            server.login(self.sender, self.key)
            server.sendmail(self.sender, [self.receiver], msg.as_string())
        Color.print_success(f"邮件推送成功")


class TgBot:
    def __init__(self, token: str, chat_id: str, proxy: dict | None = None):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.proxy = proxy

    def send(self, text: str):
        proxies = {"https": self.proxy["https"]} if self.proxy else None
        requests.post(
            f"{self.api}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            proxies=proxies,
            timeout=15
        )
        Color.print_success(f"Telegram 推送成功")


class QqBot:
    def __init__(self, host: str, port: int, access_token: str, groups: list, users: list):
        self.api = f"http://{host}:{port}"
        self.access_token = access_token
        self.groups = groups
        self.users = users

    def send(self, msg: str):
        for gid in self.groups:
            requests.post(
                f"{self.api}/send_group_msg",
                json={"group_id": gid, "message": msg},
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=10
            )
        for uid in self.users:
            requests.post(
                f"{self.api}/send_private_msg",
                json={"user_id": uid, "message": msg},
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=10
            )
        Color.print_success(f"QQ推送成功")


def init_bot(config: dict) -> list:
    bots = []
    bot_cfg = config.get("bot", {})
    proxy = config.get("proxy", {})

    feishu = bot_cfg.get("feishu", {})
    if feishu.get("enabled"):
        key = feishu.get("key", "")
        if key.startswith("$"):
            import os
            key = os.getenv(key[1:], "")
        if key:
            bots.append(FeishuBot(key))

    wecom = bot_cfg.get("wecom", {})
    if wecom.get("enabled"):
        key = wecom.get("key", "")
        if key.startswith("$"):
            import os
            key = os.getenv(key[1:], "")
        if key:
            bots.append(WecomBot(key))

    dingtalk = bot_cfg.get("dingtalk", {})
    if dingtalk.get("enabled"):
        token = dingtalk.get("access_token", "")
        secret = dingtalk.get("secret", "")
        if token.startswith("$"):
            import os
            token = os.getenv(token[1:], "")
        if secret.startswith("$"):
            import os
            secret = os.getenv(secret[1:], "")
        if token and secret:
            bots.append(DingtalkBot(token, secret))

    mail = bot_cfg.get("mail", {})
    if mail.get("enabled"):
        sender = mail.get("sender", "")
        key = mail.get("key", "")
        receiver = mail.get("receiver", "")
        if key.startswith("$"):
            import os
            key = os.getenv(key[1:], "")
        if sender and key and receiver:
            bots.append(MailBot(
                sender, key, receiver,
                mail.get("smtp_host", ""),
                mail.get("smtp_port", 465)
            ))

    tg = bot_cfg.get("telegram", {})
    if tg.get("enabled"):
        token = tg.get("token", "")
        if token.startswith("$"):
            import os
            token = os.getenv(token[1:], "")
        if token:
            bp = {"https": proxy.get("https")} if proxy.get("bot_proxy") else None
            bots.append(TgBot(token, tg.get("chat_id", ""), bp))

    qq = bot_cfg.get("qq", {})
    if qq.get("enabled"):
        token = qq.get("access_token", "")
        if token.startswith("$"):
            import os
            token = os.getenv(token[1:], "")
        if token:
            bots.append(QqBot(
                qq.get("host", "localhost"),
                qq.get("port", 6700),
                token,
                qq.get("groups", []),
                qq.get("users", [])
            ))

    return bots
