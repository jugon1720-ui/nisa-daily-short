#!/bin/zsh
# 毎朝 launchd から呼ばれる: 動画生成 → YouTubeへ予約アップロード（7:30公開）
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
exec >> "logs/$(date +%Y-%m-%d).log" 2>&1
echo "=== $(date) start"

# VOICEVOX を起動して待つ
if ! curl -s -m 2 http://127.0.0.1:50021/version >/dev/null; then
  open -g -a VOICEVOX
  for i in {1..60}; do curl -s -m 2 http://127.0.0.1:50021/version >/dev/null && break; sleep 2; done
fi

DIR="$HOME/Movies/nisa_daily/$(date +%Y-%m-%d)"
for try in 1 2 3; do
  .venv/bin/python make_short.py && break
  echo "make_short failed (try $try)"; sleep 60
done
ok=0
for try in 1 2 3; do
  if .venv/bin/python -W ignore upload.py "$DIR"; then ok=1; break; fi
  echo "upload failed (try $try)"; sleep 60
done
if [[ $ok == 0 ]]; then
  osascript -e 'display notification "YouTubeへの投稿に失敗しました。ターミナルで cd ~/nisa_short && .venv/bin/python upload.py --auth を実行してください" with title "新NISAショート" sound name "Basso"'
  exit 1
fi
echo "=== $(date) done"
