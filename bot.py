# -*- coding: utf-8 -*-
"""
VK Beauty Assistant Bot
Ежедневный дайджест в Telegram: анализ конкурентов + горячие инфоповоды (СНГ, бьюти-ниша)

Как это работает:
- Скрипт раз в сутки (по расписанию) обращается к VK API (сервисным ключом, без OAuth)
- Собирает топ-посты у заданных конкурентов за последние N часов
- Ищет свежие "горячие" посты по бьюти-ключевикам в VK (прокси для трендов/инфоповодов)
- Формирует текстовый дайджест и отправляет его тебе в Telegram

НИЧЕГО не лайкает и не комментирует автоматически — только присылает готовую подборку.
"""

import os
import time
import logging
import requests
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vk_beauty_bot")

# ============================================================
# НАСТРОЙКИ — впиши свои значения сюда (или через переменные окружения)
# ============================================================

VK_SERVICE_TOKEN = os.environ.get("VK_SERVICE_TOKEN", "e2fc3717e2fc3717e2fc371743e1bfe8c6ee2fce2fc371788524456ff2255985f0e7b1d")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8749571266:AAGr9m99GoyQNGzUlU-ia6f4NBOJgtAPAro")

# Твой Telegram chat_id. Если не знаешь — оставь 0, запусти скрипт,
# один раз напиши боту /start в Telegram, и увидишь свой chat_id в логах консоли.
TELEGRAM_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))

# Конкуренты (короткие имена из ссылок vk.ru/ИМЯ или vk.com/ИМЯ)
COMPETITORS = [
    "beauty_album",
    "krasotaa2",
    # добавляй сюда новые ники по одному в кавычках через запятую
]

# Ключевые слова ниши — используются для поиска "горячих" постов/инфоповодов
NICHE_KEYWORDS = [
    "уход за кожей", "антиэйдж", "spf", "скраб для лица",
    "эфирные масла", "бады для кожи", "распаковка косметики",
    "молодость кожи", "коллаген",
]

VK_API_VERSION = "5.199"
VK_API_URL = "https://api.vk.com/method/"

# Сколько часов "назад" считаем окном для дайджеста
LOOKBACK_HOURS = 24
# Порог: пост считается "горячим", если у него минимум столько лайков+репостов
HOT_ENGAGEMENT_THRESHOLD = 15

# Во сколько (час по UTC) слать дайджест. Например 5 = 8:00 по Москве (UTC+3)
# Можно переопределить переменной окружения SEND_HOUR_UTC на хостинге (например, для теста).
SEND_HOUR_UTC = int(os.environ.get("SEND_HOUR_UTC", "5"))

# ============================================================
# VK API
# ============================================================

def vk_call(method: str, params: dict) -> dict:
    """Вызов метода VK API с сервисным ключом."""
    query = dict(params)
    query["access_token"] = VK_SERVICE_TOKEN
    query["v"] = VK_API_VERSION
    try:
        resp = requests.get(VK_API_URL + method, params=query, timeout=15)
        data = resp.json()
        if "error" in data:
            log.warning("VK API error on %s: %s", method, data["error"])
            return {}
        return data.get("response", {})
    except Exception as e:
        log.error("VK request failed (%s): %s", method, e)
        return {}


def get_competitor_posts(domain: str, count: int = 10) -> list:
    """Последние посты сообщества по его короткому имени (домену)."""
    resp = vk_call("wall.get", {"domain": domain, "count": count, "extended": 0})
    return resp.get("items", []) if resp else []


def collect_competitor_digest() -> str:
    """Собирает топ-посты конкурентов за LOOKBACK_HOURS часов."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    lines = ["🔎 Аналитика конкурентов за сутки\n"]
    found_any = False

    for domain in COMPETITORS:
        posts = get_competitor_posts(domain)
        fresh_posts = [
            p for p in posts
            if datetime.fromtimestamp(p.get("date", 0), tz=timezone.utc) >= cutoff
        ]
        if not fresh_posts:
            continue

        # сортируем по вовлечённости (лайки + репосты + комментарии)
        def engagement(p):
            likes = p.get("likes", {}).get("count", 0)
            reposts = p.get("reposts", {}).get("count", 0)
            comments = p.get("comments", {}).get("count", 0)
            return likes + reposts * 2 + comments

        fresh_posts.sort(key=engagement, reverse=True)
        top_post = fresh_posts[0]
        eng = engagement(top_post)
        text_preview = (top_post.get("text", "") or "").strip().replace("\n", " ")[:120]
        post_url = f"https://vk.com/{domain}?w=wall-{top_post.get('owner_id', 0) * -1}_{top_post.get('id')}"

        lines.append(f"@{domain} — топ-пост, вовлечённость ~{eng}")
        if text_preview:
            lines.append(f"«{text_preview}...»")
        lines.append(post_url)
        lines.append("")
        found_any = True

    if not found_any:
        lines.append("За последние сутки у конкурентов не было заметно активных постов.")

    return "\n".join(lines)


def collect_hot_topics() -> str:
    """Ищет свежие горячие посты по ключевикам ниши — прокси для инфоповодов."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    lines = ["🔥 Горячие темы прямо сейчас\n"]
    found_any = False
    seen_texts = set()

    for kw in NICHE_KEYWORDS:
        resp = vk_call("newsfeed.search", {
            "q": kw,
            "count": 15,
            "extended": 0,
            "start_time": int(cutoff.timestamp()),
        })
        items = resp.get("items", []) if resp else []

        for p in items:
            likes = p.get("likes", {}).get("count", 0)
            reposts = p.get("reposts", {}).get("count", 0)
            eng = likes + reposts * 2
            if eng < HOT_ENGAGEMENT_THRESHOLD:
                continue

            text = (p.get("text", "") or "").strip()
            if not text or text[:60] in seen_texts:
                continue
            seen_texts.add(text[:60])

            angle = suggest_angle(kw)
            lines.append(f"Тема: {kw} (вовлечённость ~{eng})")
            lines.append(f"«{text[:150]}...»")
            lines.append(f"💡 Угол подачи: {angle}")
            lines.append("")
            found_any = True
            break  # одна лучшая находка на ключевик, чтобы не спамить

    if not found_any:
        lines.append("Сегодня явных горячих инфоповодов в нише не нашлось — можно постить по плану.")

    return "\n".join(lines)


def collect_scout_list() -> str:
    """20 постов для лайка + 10 постов с черновиком комментария, из ниши и конкурентов."""
    posts = []
    for domain in COMPETITORS:
        posts += get_competitor_posts(domain, count=10)
    for kw in NICHE_KEYWORDS:
        resp = vk_call("newsfeed.search", {"q": kw, "count": 10, "extended": 0})
        posts += resp.get("items", []) if resp else []

    seen = set()
    unique = []
    for p in posts:
        key = (p.get("owner_id"), p.get("id"))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(p)

    unique.sort(key=lambda p: p.get("date", 0), reverse=True)

    like_list = unique[:20]
    comment_list = unique[:10]

    lines = [f"👍 {len(like_list)} постов для лайка сегодня\n"]
    for i, p in enumerate(like_list, 1):
        url = f"https://vk.com/wall{p.get('owner_id')}_{p.get('id')}"
        lines.append(f"{i}. {url}")

    lines.append(f"\n💬 {len(comment_list)} постов с черновиком комментария\n")
    for i, p in enumerate(comment_list, 1):
        url = f"https://vk.com/wall{p.get('owner_id')}_{p.get('id')}"
        text_preview = (p.get("text", "") or "").strip().replace("\n", " ")[:80]
        draft = suggest_comment_draft(text_preview)
        lines.append(f"{i}. {url}\n   Черновик: {draft}")

    if not like_list:
        lines.append("Сегодня подходящих постов не нашлось — попробуй завтра.")

    return "\n".join(lines)


def suggest_comment_draft(text_preview: str) -> str:
    """Простой черновик комментария — можно доработать под свой стиль."""
    if any(w in text_preview.lower() for w in ["spf", "спф"]):
        return "Очень актуально! А вы каким SPF пользуетесь зимой? 🌤️"
    if any(w in text_preview.lower() for w in ["скраб", "пилинг"]):
        return "Обожаю такие средства! А как часто вы его используете?"
    return "Очень интересно! А можно узнать подробнее про состав? 😊"



def suggest_angle(keyword: str) -> str:
    """Простая эвристика для угла подачи — черновик, который можно доработать."""
    templates = {
        "spf": "Расскажи, почему тема SPF снова всплыла — сделай короткий разбор мифов.",
        "антиэйдж": "Свяжи тренд с своим личным опытом/рутиной — покажи 'было/стало'.",
        "коллаген": "Сделай пост-разбор состава: что реально работает, а что маркетинг.",
    }
    for key, tpl in templates.items():
        if key in keyword.lower():
            return tpl
    return f"Сними короткое видео/пост о том, как тема «{keyword}» связана с твоей рутиной."


# ============================================================
# TELEGRAM
# ============================================================

def _split_into_chunks(text: str, max_len: int = 3500) -> list:
    """Режет текст на части по границам строк, чтобы никогда не разрывать
    ссылку или слово посередине."""
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def send_telegram_message(text: str):
    if not TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID не задан — сообщение не отправлено. "
                     "Напиши боту /start и посмотри chat_id в логах.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram ограничивает длину сообщения ~4096 символов — режем на части при необходимости,
    # но только по границам строк, чтобы не разрывать ссылки
    chunks = _split_into_chunks(text, max_len=3500)
    for chunk in chunks:
        try:
            requests.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "disable_web_page_preview": True,
            }, timeout=15)
        except Exception as e:
            log.error("Не удалось отправить сообщение в Telegram: %s", e)


def poll_for_chat_id():
    """Разовая утилита: узнать свой chat_id, если он ещё не известен."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, timeout=15).json()
        for update in resp.get("result", []):
            msg = update.get("message", {})
            if msg.get("text") == "/start":
                chat_id = msg["chat"]["id"]
                log.info(">>> Твой TELEGRAM_CHAT_ID: %s (вставь его в настройки) <<<", chat_id)
                return chat_id
    except Exception as e:
        log.error("Ошибка получения updates: %s", e)
    return None


# ============================================================
# ОСНОВНОЙ ЦИКЛ
# ============================================================

def build_and_send_digest():
    log.info("Собираю дайджест...")
    scout_part = collect_scout_list()
    competitor_part = collect_competitor_digest()
    trends_part = collect_hot_topics()
    today = datetime.now().strftime("%d.%m.%Y")

    full_text = f"☀️ Доброе утро! Дайджест на {today}\n\n{scout_part}\n\n{competitor_part}\n\n{trends_part}"
    send_telegram_message(full_text)
    log.info("Дайджест отправлен.")


def main():
    global TELEGRAM_CHAT_ID
    log.info("Бот запущен.")

    if not TELEGRAM_CHAT_ID:
        log.info("chat_id не задан — жду, пока ты напишешь /start боту в Telegram...")
        chat_id = None
        while not chat_id:
            chat_id = poll_for_chat_id()
            if not chat_id:
                time.sleep(5)
        TELEGRAM_CHAT_ID = chat_id

    last_sent_date = None
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == SEND_HOUR_UTC and last_sent_date != now.date():
            build_and_send_digest()
            last_sent_date = now.date()
        time.sleep(60)


if __name__ == "__main__":
    main()
