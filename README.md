# Cubie_A5E_San_Bot

Radxa Cubie A5E（きゅびーさん）の Mastodon / Hollo 対応AIボットです。

## 主な機能
- **`+LLM`**: キャラクター会話（スレッド履歴・添付画像対応）
- **`+M`**: 本体の CPU使用率・RAM使用率/使用量を測定して報告
- **`+TALK`**: 他SBCボットとのグループ会話リレー（2周・全8回）
- **定期投稿**: 毎日 12:00 / 15:00 / 22:00 / 02:00 に自動投稿
- **自動フォローバック**: フォロー通知を受け取ると自動でフォロー返し
- **内蔵状態サーバー**: `DASHBOARD_PORT`（既定 `8080`）で他Bot向けのJSON状態API（`/gauge_state`, `/login_bonus`, `/opizero3_state`）を提供
