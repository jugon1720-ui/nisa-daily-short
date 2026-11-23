#!/usr/bin/env python3
"""毎朝の「新NISA銘柄 株価チェック」ショート動画を自動生成する。

使い方:
  .venv/bin/python make_short.py                # 今日の銘柄（日替わりローテーション）
  .venv/bin/python make_short.py --symbol 7203.T # 銘柄を指定
出力: <output_dir>/YYYY-MM-DD/short.mp4 と meta.json（タイトル・概要欄）
"""
import argparse, datetime as dt, io, json, math, os, subprocess, sys, urllib.parse, urllib.request, wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
JST = dt.timezone(dt.timedelta(hours=9))
W, H, FPS = 1080, 1920, 30
FONT_DIR = "/System/Library/Fonts/"
def font(weight, size):
    return ImageFont.truetype(f"{FONT_DIR}ヒラギノ角ゴシック W{weight}.ttc", size)

# 前回ショートの配色（帯色, 背景色）
THEMES = {
    "red":    ("#F2533A", "#FFF3F1"),
    "orange": ("#F57C00", "#FFF4E6"),
    "green":  ("#2E7D32", "#EEF5EE"),
    "navy":   ("#1F2A7A", "#EEF1FA"),
}
UP, DOWN, FLAT = "#E53935", "#1E63D6", "#555555"   # 日本式: 上昇=赤, 下落=青
TEXT, SUB, NOTE = "#141414", "#333333", "#777777"
MX = 72                       # 左右マージン
DISCLAIMER = "※特定の銘柄をすすめるものではありません。投資は自己責任で。"


# ---------------------------------------------------------------- data
def fetch(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=13mo&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    tz = dt.timezone(dt.timedelta(seconds=res["meta"]["gmtoffset"]))
    pts = [(dt.datetime.fromtimestamp(t, tz).date(), c)
           for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    if len(pts) < 30:
        raise RuntimeError(f"{symbol}: データが足りません")
    return pts


def since(pts, days):
    start = pts[-1][0] - dt.timedelta(days=days)
    return [p for p in pts if p[0] >= start]


def pct(a, b):
    return (b - a) / a * 100


# ---------------------------------------------------------------- formatting
def fmt_price(v, unit):
    return f"{v:,.2f}" if unit == "pt" or v < 1000 else f"{v:,.0f}"

def signed(v, digits=2):
    return ("+" if v > 0 else "") + f"{v:.{digits}f}"

def color_of(v):
    return UP if v > 0.004 else DOWN if v < -0.004 else FLAT

def say_move(v):
    a = abs(v)
    if a < 0.005:
        return "ほぼ変わらず"
    return f"{a:.2f}パーセントの{'上昇' if v > 0 else '下落'}"

def unit_yomi(unit):
    return "ポイント" if unit == "pt" else unit


# ---------------------------------------------------------------- voice
def tts(cfg, text, speaker, path):
    base = cfg["voicevox_url"]
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    with urllib.request.urlopen(urllib.request.Request(f"{base}/audio_query?{q}", method="POST"), timeout=60) as r:
        query = json.load(r)
    query["speedScale"] = cfg.get("speed_scale", 1.15)
    query["prePhonemeLength"] = 0.05
    query["postPhonemeLength"] = 0.05
    req = urllib.request.Request(f"{base}/synthesis?speaker={speaker}", data=json.dumps(query).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        path.write_bytes(r.read())
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(paths, gaps, out):
    params, frames = None, []
    for p, gap in zip(paths, gaps):
        with wave.open(str(p)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
            frames.append(b"\x00" * int(params.framerate * gap) * params.sampwidth * params.nchannels)
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        for f in frames:
            w.writeframes(f)


# ---------------------------------------------------------------- drawing helpers
def fit_font(text, weight, size, max_w):
    while size > 30:
        f = font(weight, size)
        if f.getlength(text) <= max_w:
            return f
        size -= 4
    return font(weight, size)


def text_layer(lines):
    """lines: [(text, font, color, gap_after)] -> RGBA image (幅W)"""
    hs = [f.getbbox("あAg")[3] for _, f, _, _ in lines]
    total = sum(hs) + sum(g for *_, g in lines)
    img = Image.new("RGBA", (W, total + 20), (0, 0, 0, 0))
    d, y = ImageDraw.Draw(img), 0
    for (t, f, c, g), h in zip(lines, hs):
        d.text((MX, y), t, font=f, fill=c)
        y += h + g
    return img


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def base_frame(theme, label):
    band, bg = THEMES[theme]
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 176, W, 296], fill=band)
    d.text((MX, 236), label, font=font(7, 50), fill="white", anchor="lm")
    d.text((MX, 1560), DISCLAIMER, font=font(4, 30), fill=NOTE)
    d.text((MX, 1606), "データ出典: Yahoo! Finance（終値ベース）", font=font(4, 26), fill=NOTE)
    return img


def draw_chart(d, pts, frac, color, box, unit):
    x0, y0, x1, y1 = box
    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or hi * 0.01
    lo, hi = lo - pad, hi + pad
    n = len(vals)
    xy = [(x0 + (x1 - x0) * i / (n - 1), y1 - (y1 - y0) * (v - lo) / (hi - lo)) for i, v in enumerate(vals)]
    # 枠線と目盛り
    for k in range(5):
        yy = y0 + (y1 - y0) * k / 4
        d.line([(x0, yy), (x1, yy)], fill="#D9D9D9", width=2)
    small = font(5, 30)
    d.text((x0, y1 + 18), pts[0][0].strftime("%Y/%m/%d"), font=small, fill=NOTE)
    d.text((x1, y1 + 18), pts[-1][0].strftime("%Y/%m/%d"), font=small, fill=NOTE, anchor="ra")
    m = max(2, int(n * frac))
    shown = xy[:m]
    # 塗り
    poly = shown + [(shown[-1][0], y1), (shown[0][0], y1)]
    r, g, b = Image.new("RGB", (1, 1), color).getpixel((0, 0))
    d.polygon(poly, fill=(r, g, b, 40))
    d.line(shown, fill=color, width=8, joint="curve")
    ex, ey = shown[-1]
    d.ellipse([ex - 14, ey - 14, ex + 14, ey + 14], fill=color, outline="white", width=4)
    if frac >= 1:
        txt = fmt_price(vals[-1], unit) + ("" if unit == "pt" else unit)
        f = font(7, 40)
        tx = max(x0, ex - f.getlength(txt) - 24)
        ty = ey - 92 if ey - 92 > y0 - 60 else ey + 30
        d.text((tx, ty), txt, font=f, fill=color, stroke_width=8, stroke_fill="white")
    # 最初の値（基準）を点線で
    sy = xy[0][1]
    for xx in range(int(x0), int(x1), 24):
        d.line([(xx, sy), (xx + 12, sy)], fill="#9E9E9E", width=2)


# ---------------------------------------------------------------- scenes
def build_scenes(item, pts, today):
    last_d, last = pts[-1]
    prev = pts[-2][1]
    d1 = pct(prev, last)
    m1 = since(pts, 31)
    y1 = since(pts, 365)
    p1m, p1y = pct(m1[0][1], last), pct(y1[0][1], last)
    unit, name, yomi = item["unit"], item["name"], item["yomi"]
    u = "" if unit == "pt" else unit
    md = f"{last_d.month}月{last_d.day}日"
    price_say = f"{last:.0f}" if last >= 1000 else f"{last:.1f}"

    scenes = [
        dict(theme="red", label="毎朝7:30 新NISA銘柄チェック", speaker="normal",
             say=f"新NISAで人気の{yomi}、上がった？下がった？",
             lines=[(name, fit_font(name, 8, 130, W - 2 * MX), TEXT, 24),
                    ("上がった？", font(8, 120), UP, 10),
                    ("下がった？", font(8, 120), DOWN, 50),
                    (f"{md}の終値でチェック", font(6, 52), SUB, 0)], y=560),
        dict(theme="orange", label="前日比", speaker="amaama",
             say=f"{md}の終値は{price_say}{unit_yomi(unit)}。前の日より{say_move(d1)}だよ。",
             lines=[(f"{signed(d1)}%", font(8, 190), color_of(d1), 40),
                    (f"終値 {fmt_price(last, unit)}{u}", font(8, 84), TEXT, 24),
                    (f"前日より {signed(last - prev, 2 if unit == 'pt' or last < 1000 else 0)}{u}", font(6, 52), SUB, 0)],
             y=560),
        dict(theme="green", label="この1か月の推移", speaker="normal",
             say=f"この1か月では、{say_move(p1m)}。",
             lines=[(f"1か月 {signed(p1m)}%", font(8, 120), color_of(p1m), 0)], y=420, chart=m1),
        dict(theme="navy", label="この1年の推移", speaker="amaama",
             say=f"1年前と比べると、{say_move(p1y)}になってるよ。",
             lines=[(f"1年 {signed(p1y)}%", font(8, 120), color_of(p1y), 0)], y=420, chart=y1),
        dict(theme="red", label="毎朝7:30に更新", speaker="normal",
             say="毎朝7時半に更新！あしたの銘柄もお楽しみに！",
             lines=[("毎朝7:30", font(8, 140), TEXT, 10),
                    ("更新中！", font(8, 140), UP, 50),
                    ("あしたの銘柄もお楽しみに", font(6, 52), SUB, 0)], y=600),
    ]
    stats = dict(date=last_d.isoformat(), close=last, d1=d1, m1=p1m, y1=p1y)
    return scenes, stats


def render(scenes, durations, audio, out, cfg, unit):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    total = sum(durations)
    cmd = [ffmpeg, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(audio)]
    bgm = Path(os.path.expanduser(cfg.get("bgm_path", ""))) if cfg.get("bgm_path") else None
    if bgm and bgm.exists():
        cmd += ["-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex",
                f"[2:a]volume={cfg.get('bgm_volume', 0.08)},afade=t=out:st={total - 1.2:.2f}:d=1.2[b];"
                f"[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0[v];[v][b]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    elapsed = 0.0
    for sc, dur in zip(scenes, durations):
        band = THEMES[sc["theme"]][0]
        base = base_frame(sc["theme"], sc["label"])
        layer = text_layer(sc["lines"])
        nfr = int(round(dur * FPS))
        for i in range(nfr):
            t = i / FPS
            frame = base.copy()
            d = ImageDraw.Draw(frame, "RGBA")
            # 進行バー（帯の上）
            prog = (elapsed + t) / total
            d.rectangle([0, 150, int(W * prog), 162], fill=band)
            # テキスト：下からふわっと
            k = ease(t / 0.35)
            lay = layer if k >= 1 else layer.copy()
            if k < 1:
                a = lay.getchannel("A").point(lambda v: int(v * k))
                lay.putalpha(a)
            frame.paste(lay, (0, sc["y"] + int((1 - k) * 60)), lay)
            if "chart" in sc:
                frac = ease((t - 0.25) / 1.6)
                draw_chart(d, sc["chart"], max(frac, 0.02), color_of(pct(sc["chart"][0][1], sc["chart"][-1][1])),
                           (MX + 10, 760, W - MX - 60, 1370), unit)
            proc.stdin.write(frame.tobytes())
        elapsed += dur
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--date", help="ローテーション計算用の日付 YYYY-MM-DD（既定: 今日）")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text())
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(JST).date()
    wl = cfg["watchlist"]
    item = next((w for w in wl if w["symbol"] == args.symbol), None) if args.symbol \
        else wl[today.toordinal() % len(wl)]
    if item is None:
        sys.exit(f"config.json の watchlist に {args.symbol} がありません")

    outdir = Path(os.path.expanduser(cfg["output_dir"])) / today.isoformat()
    outdir.mkdir(parents=True, exist_ok=True)
    pts = fetch(item["symbol"])
    scenes, stats = build_scenes(item, pts, today)

    wavs, durs, gaps = [], [], []
    for i, sc in enumerate(scenes):
        p = outdir / f"voice_{i:02d}.wav"
        sec = tts(cfg, sc["say"], cfg["speaker_" + sc["speaker"]], p)
        gap = 0.9 if "chart" in sc else 0.35
        wavs.append(p); durs.append(sec + gap); gaps.append(gap)
    concat_wavs(wavs, gaps, outdir / "voice.wav")
    render(scenes, durs, outdir / "voice.wav", outdir / "short.mp4", cfg, item["unit"])

    md = f"{int(stats['date'][5:7])}/{int(stats['date'][8:])}"
    title = f"【{item['name']}】{md}終値 前日比{signed(stats['d1'])}%｜新NISA銘柄チェック #Shorts"
    u = "" if item["unit"] == "pt" else item["unit"]
    desc = "\n".join([
        f"新NISAで人気の「{item['name']}」の値動きを、毎朝7:30にお届けします。",
        "",
        f"■ {stats['date']} 終値: {fmt_price(stats['close'], item['unit'])}{u}",
        f"■ 前日比: {signed(stats['d1'])}%",
        f"■ 1か月: {signed(stats['m1'])}%",
        f"■ 1年: {signed(stats['y1'])}%",
        "",
        *( [f"▶ 新NISAの始め方・我が家の選び方は本編で：{cfg['main_video_url']}", ""] if cfg.get("main_video_url") else []),
        "※特定の銘柄の売買をすすめるものではありません。投資は自己責任でお願いします。",
        "※データ出典: Yahoo! Finance（終値ベース）。数値は自動集計のため誤差を含む場合があります。",
        "VOICEVOX:ずんだもん",
        "",
        f"#Shorts #新NISA #株価 #{item['name'].replace('&', '').replace(' ', '')} #資産形成",
    ])
    meta = dict(title=title[:100], description=desc, tags=["新NISA", "株価", item["name"], "資産形成", "Shorts"],
                symbol=item["symbol"], stats=stats, video=str(outdir / "short.mp4"))
    (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
