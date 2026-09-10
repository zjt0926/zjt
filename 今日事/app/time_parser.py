# -*- coding: utf-8 -*-
"""自然语言时间解析器：从中文文本中识别日期与时间。

支持示例：
    明天 / 后天 / 大后天 / 今天下午3点 / 明天早上8点半
    下周三 / 下下周一 / 周五 / 本周五 / 礼拜天
    9月12日 / 9月12号 / 2026-09-12 / 12号
    3天后 / 两小时后 / 一个半小时后 / 30分钟后 / 半小时后
    晚上8点 / 下午3点15 / 中午12点半 / 凌晨1点 / 七点半 / 14:30
"""
import re
from datetime import datetime, timedelta

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 相对日 -> 偏移天数（优先匹配长词）
DAY_WORDS = [
    (re.compile(r"大大后天"), 4),
    (re.compile(r"大后天"), 3),
    (re.compile(r"后天"), 2),
    (re.compile(r"明天|明日|明晚"), 1),
    (re.compile(r"今天|今日|今晚|今夜"), 0),
]

WEEKDAY_RE = re.compile(
    r"(下下下周|下下周|下周|下个星期|下星期|下个礼拜|下礼拜"
    r"|本星期|本周|这周|这星期|这个礼拜|本礼拜|星期|礼拜|周)\s*([一二三四五六日天])"
)
MONTH_WORD_RE = re.compile(r"(下下个?月|下个?月|月底|月末)")
FULL_DATE_RE = re.compile(r"(\d{4})[年\-/.](\d{1,2})[月\-/.](\d{1,2})[日号]?")
MONTH_DAY_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]?")
N_DAYS_RE = re.compile(r"(\d{1,3})天[之以]?后")
N_WEEKS_RE = re.compile(r"(\d{1,2})个?(?:星期|礼拜|周)[之以]?后")
N_MONTHS_RE = re.compile(r"(\d{1,2})个?月[之以]?后")
DAY_ONLY_RE = re.compile(r"(?<![\d点])((?:\d{1,2})|(?:[一二两三四五六七八九十]{1,3}))[日号](?!\d)")

CLOCK_RE = re.compile(
    r"(凌晨|清晨|早上|上午|中午|午后|下午|傍晚|晚上|夜里|夜晚)?\s*"
    r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})[点时]"
    r"(?:\s*(半|一刻|三刻|(\d{1,2})\s*分?))?"
)
BARE_COLON_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
N_MINUTES_RE = re.compile(r"(\d{1,3})\s*分[钟]?\s*[之以]?后")
N_HOURS_RE = re.compile(r"(\d{1,2}(?:\.5)?|[零一二两三四五六七八九十]{1,3})\s*个?小时\s*[之以]?后")
ONE_HALF_HOUR_RE = re.compile(r"[一1]\s*个?半\s*个?小时\s*[之以]?后")
HALF_HOUR_RE = re.compile(r"半\s*个?小时\s*[之以]?后")
PERIOD_ONLY_RE = re.compile(r"(凌晨|清晨|早上|上午|中午|午后|下午|傍晚|晚上|夜里|夜晚)")

PM_PERIODS = {"下午", "午后", "傍晚", "晚上", "夜里", "夜晚"}
PERIOD_DEFAULT_HOUR = {
    "凌晨": 1, "清晨": 6, "早上": 8, "上午": 10, "中午": 12,
    "午后": 14, "下午": 15, "傍晚": 18, "晚上": 20, "夜里": 21, "夜晚": 21,
}


def _cn_num(s):
    """中文数字/阿拉伯数字 -> int，失败返回 None"""
    if not s:
        return None
    s = s.strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    if "十" in s:
        parts = s.split("十")
        tens = CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    if all(ch in CN_DIGITS for ch in s) and s:
        return sum(CN_DIGITS[ch] for ch in s)
    return None


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return dt.replace(year=year, month=month, day=min(dt.day, days_in[month - 1]))


def _last_day_of_month(dt: datetime) -> datetime:
    nxt = _add_months(dt.replace(day=1), 1)
    return nxt - timedelta(days=1)


def _match_date(text: str, now: datetime):
    """返回 (date, 剩余文本, 描述) 或 (None, text, None)"""
    m = FULL_DATE_RE.search(text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt, text[:m.start()] + text[m.end():], m.group(0)
        except ValueError:
            pass
    m = MONTH_DAY_RE.search(text)
    if m:
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)))
            return dt, text[:m.start()] + text[m.end():], m.group(0)
        except ValueError:
            pass
    for pat, offset in DAY_WORDS:
        m = pat.search(text)
        if m:
            dt = (now + timedelta(days=offset)).replace(hour=0, minute=0)
            return dt, text[:m.start()] + text[m.end():], m.group(0)
    m = WEEKDAY_RE.search(text)
    if m:
        prefix, wd_ch = m.group(1), m.group(2)
        target = WEEKDAY_MAP[wd_ch]
        if prefix.startswith("下下下周"):
            base = now + timedelta(days=(7 - now.weekday()) % 7 or 7, weeks=2)
        elif prefix.startswith("下下周"):
            base = now + timedelta(days=(7 - now.weekday()) % 7 or 7, weeks=1)
        elif prefix.startswith("下"):
            base = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
        elif prefix.startswith("本") or prefix.startswith("这") or prefix.startswith("这个"):
            base = now - timedelta(days=now.weekday())  # 本周一
        else:  # 裸星期X：本周未到则本周，已过则下周
            base = now + timedelta(days=(target - now.weekday()) % 7)
        dt = base + timedelta(days=target - base.weekday())
        return dt.replace(hour=0, minute=0), text[:m.start()] + text[m.end():], m.group(0)
    m = MONTH_WORD_RE.search(text)
    if m:
        w = m.group(0)
        if w in ("月底", "月末"):
            dt = _last_day_of_month(now)
        else:
            dt = _add_months(now, 2 if w.startswith("下下") else 1)
        return dt.replace(hour=0, minute=0), text[:m.start()] + text[m.end():], m.group(0)
    m = N_DAYS_RE.search(text)
    if m:
        return now + timedelta(days=int(m.group(1))), text[:m.start()] + text[m.end():], m.group(0)
    m = N_WEEKS_RE.search(text)
    if m:
        return now + timedelta(weeks=int(m.group(1))), text[:m.start()] + text[m.end():], m.group(0)
    m = N_MONTHS_RE.search(text)
    if m:
        return _add_months(now, int(m.group(1))), text[:m.start()] + text[m.end():], m.group(0)
    m = DAY_ONLY_RE.search(text)
    if m:
        d = _cn_num(m.group(1))
        if d is None or not (1 <= d <= 31):
            return None, text, None
        try:
            dt = now.replace(day=d, hour=0, minute=0)
        except ValueError:
            return None, text, None
        if dt.date() < now.date():  # 本月已过 -> 下月
            dt = _add_months(dt, 1)
        return dt, text[:m.start()] + text[m.end():], m.group(0)
    return None, text, None


def _normalize_clock(period: str, hour: int, minute: int, evening_ctx: bool):
    if period:
        if period in PM_PERIODS and hour < 12:
            hour += 12
        elif period == "中午" and hour < 3:  # 中午1点 -> 13点
            hour += 12
        elif period == "凌晨" and hour == 12:  # 凌晨12点 -> 0点
            hour = 0
    elif evening_ctx and hour < 12:  # "明晚8点" 中"明晚"已被日期消费
        hour += 12
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _match_time(text: str, now: datetime, evening_ctx: bool):
    """返回 ((h,mi)|('+min',n)|None, 剩余文本, 描述)"""
    # 14:30 冒号写法（优先于 CLOCK_RE，避免 ":" 被误匹配）
    m = BARE_COLON_RE.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            prefix = text[max(0, m.start() - 4):m.start()]
            pm = re.search(r"(下午|午后|傍晚|晚上|夜里|夜晚)", prefix)
            if pm and h < 12:
                h += 12
                remain = text[:m.start() - len(pm.group(1))] + text[m.end():]
                return (h, mi), remain, pm.group(1) + m.group(0)
            remain = text[:m.start()] + text[m.end():]
            return (h, mi), remain, m.group(0)
    # X点[X分/半/一刻]
    m = CLOCK_RE.search(text)
    if m:
        period = m.group(1) or ""
        h = _cn_num(m.group(2))
        rest = m.group(3)
        if h is None:
            return None, text, None
        if rest is None:
            minute = 0
        elif rest == "半":
            minute = 30
        elif rest == "一刻":
            minute = 15
        elif rest == "三刻":
            minute = 45
        else:
            minute = int(m.group(4))
        norm = _normalize_clock(period, h, minute, evening_ctx)
        if norm:
            remain = text[:m.start()] + text[m.end():]
            return norm, remain, m.group(0)
    # 一个半小时后
    m = ONE_HALF_HOUR_RE.search(text)
    if m:
        return ("+min", 90), text[:m.start()] + text[m.end():], m.group(0)
    # 半小时后
    m = HALF_HOUR_RE.search(text)
    if m:
        return ("+min", 30), text[:m.start()] + text[m.end():], m.group(0)
    # 2小时后 / 两小时后 / 1.5小时后
    m = N_HOURS_RE.search(text)
    if m:
        n = _cn_num(m.group(1)) if not re.fullmatch(r"[\d.]+", m.group(1)) else float(m.group(1))
        if n:
            return ("+min", int(n * 60)), text[:m.start()] + text[m.end():], m.group(0)
    # 30分钟后
    m = N_MINUTES_RE.search(text)
    if m:
        return ("+min", int(m.group(1))), text[:m.start()] + text[m.end():], m.group(0)
    # 只有时段没有小时：下午 -> 默认15点
    m = PERIOD_ONLY_RE.search(text)
    if m:
        hour = PERIOD_DEFAULT_HOUR.get(m.group(1))
        remain = text[:m.start()] + text[m.end():]
        return (hour, 0), remain, m.group(0)
    return None, text, None


def _clean_title(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^[，。、,.\s-]+|[，。、,.\s-]+$", "", t)
    return t


def parse_text(text: str, now: datetime | None = None) -> dict:
    """解析自然语言文本。

    返回 {title, date, remind_at, important, matched}：
        title      清理后的标题
        date       'YYYY-MM-DD' 或 None（上层默认今天）
        remind_at  'YYYY-MM-DD HH:MM' 或 None
        important  是否重要事项
        matched    识别出的时间片段列表
    """
    now = now or datetime.now()
    original = text.strip()
    matched = []
    important = False
    working = original

    # 重要标识：!重要 / 重要! / 开头或结尾的 !
    imp_re = re.compile(r"[!！]\s*重要\s*[!！]?|重要\s*[!！]")
    if imp_re.search(working):
        important = True
        working = imp_re.sub(" ", working)
    if re.search(r"^\s*[!！]{1,3}\s*|\s*[!！]{1,3}\s*$", working):
        important = True
        working = re.sub(r"^\s*[!！]{1,3}\s*|\s*[!！]{1,3}\s*$", " ", working)

    # "今晚/明晚" 提供隐含的晚上语境
    evening_ctx = bool(re.search(r"今晚|今夜|明晚", working))

    date_dt, working, desc = _match_date(working, now)
    if date_dt:
        matched.append(desc)

    time_val, working, desc = _match_time(working, now, evening_ctx)
    if time_val:
        matched.append(desc)

    remind_at = None
    date_str = date_dt.strftime("%Y-%m-%d") if date_dt else None

    if time_val:
        if time_val[0] == "+min":  # 相对时间：30分钟后
            dt = now + timedelta(minutes=time_val[1])
        elif isinstance(time_val[0], int):  # 明确时刻
            base = date_dt or now
            dt = base.replace(hour=time_val[0], minute=time_val[1],
                              second=0, microsecond=0)
            if not date_dt and dt <= now:  # 未写日期且时刻已过 -> 明天
                dt += timedelta(days=1)
        remind_at = dt.strftime("%Y-%m-%d %H:%M")
        date_str = dt.strftime("%Y-%m-%d")

    title = _clean_title(working)
    if not title:
        title = " ".join(m for m in matched if m).strip() or "未命名事项"

    return {
        "title": title,
        "date": date_str,
        "remind_at": remind_at,
        "important": important,
        "matched": [m for m in matched if m],
    }
