# PLANT ROOM

植物を登録して、ピクセル部屋に配置するローカル/デプロイ対応のFastAPIアプリです。

## 起動

```bash
cd /Users/ryunosuke/Downloads/plant-room-codex-handoff
python3 -m venv .venv
. .venv/bin/activate
pip install -r app/requirements.txt
python3 app/server.py
```

ブラウザで `http://127.0.0.1:8787` を開きます。

## 修正の運用

修正を頼むときの基本手順:

1. ローカルでコードを直す
2. ローカルサーバーで画面を確認する
3. 仕様書と照らし合わせてOKか確認する
4. OKだった変更だけGitHubにpushする

## 仕様書

- `ROOM_VISUAL_SPEC.md`
- `REALISM_CHECKLIST.md`
- `SPEC_REALISM_AUDIT.md`
- `IMPLEMENTATION_SPEC_CHECK.md`

## 公開時にGitHubへ含めないもの

- `.venv/`
- `node_modules/`
- `app/data/`
- `app/uploads/`
- `.env`

ローカルDBやアップロード画像は個人データなので、GitHubには載せません。
