# -*- coding: utf-8 -*-
"""weather 工具：查询天气（Open-Meteo，免费且无需 API Key）。

数据源：
  1. geocoding-api.open-meteo.com  城市名 → 经纬度
  2. api.open-meteo.com            当前天气
两个都是轻量 JSON 接口，纯标准库请求；失败时如实返回错误，绝不编造数据。
"""

import json
import urllib.parse

from tools.web._http import fetch_text

GEOCODE_URL = ("https://geocoding-api.open-meteo.com/v1/search?"
               "name={query}&count=1&language=zh&format=json")
FORECAST_URL = ("https://api.open-meteo.com/v1/forecast?"
                "latitude={lat:.4f}&longitude={lon:.4f}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                "weather_code,wind_speed_10m&timezone=auto")

# WMO 天气代码 → 中文描述（只保留常见项）
WMO_CODES = {
    0: "晴", 1: "基本晴", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


def _geocode(location: str, timeout: int):
    """城市名 → 经纬度。返回 (info, error)。"""
    ok, text, error = fetch_text(GEOCODE_URL.format(query=urllib.parse.quote(location)),
                                 timeout=timeout, max_bytes=64 * 1024)
    if not ok:
        return None, "地理编码失败：%s" % error
    try:
        data = json.loads(text)
    except ValueError:
        return None, "地理编码返回非法 JSON"
    results = data.get("results") or []
    if not results:
        return None, "没找到城市：%s" % location
    first = results[0]
    return {"name": first.get("name"), "country": first.get("country", ""),
            "latitude": first.get("latitude"), "longitude": first.get("longitude")}, ""


def get_weather(location: str = None, default_location: str = None, timeout: int = 10) -> dict:
    """查询天气，返回结构化最小结果（失败时 ok=false + 明确原因）。"""
    place = (location or "").strip() or (default_location or "").strip()
    if not place:
        return {"ok": False, "error": "weather: 需要城市名（location 为空且未配置默认城市）"}
    geo, error = _geocode(place, timeout)
    if geo is None:
        return {"ok": False, "error": "weather_unavailable: %s" % error}
    ok, text, error = fetch_text(
        FORECAST_URL.format(lat=float(geo["latitude"]), lon=float(geo["longitude"])),
        timeout=timeout, max_bytes=64 * 1024)
    if not ok:
        return {"ok": False, "error": "weather_unavailable: %s" % error}
    try:
        data = json.loads(text)
    except ValueError:
        return {"ok": False, "error": "weather_unavailable: 返回非法 JSON"}
    current = data.get("current") or {}
    if not current:
        return {"ok": False, "error": "weather_unavailable: 返回里没有 current 字段"}
    code = current.get("weather_code")
    return {
        "ok": True,
        "location": ("%s %s" % (geo["name"], geo["country"])).strip(),
        "temperature": "%s °C" % current.get("temperature_2m"),
        "feels_like": "%s °C" % current.get("apparent_temperature"),
        "weather": WMO_CODES.get(int(code), "未知(%s)" % code) if code is not None else "未知",
        "humidity": "%s%%" % current.get("relative_humidity_2m"),
        "wind": "%s m/s" % current.get("wind_speed_10m"),
        "observed_at": current.get("time", ""),
        "source": "open-meteo",
    }
