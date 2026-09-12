# X / YouTube → DeepL → Discord

Fix:
TwitterAPI.io returns tweets under:

`data.tweets`

not necessarily top-level:

`tweets`

This version supports both.

Schedule:
every 3 hours.

Secrets:
- TWITTER_API_KEY
- DEEPL_API_KEY
- DISCORD_WEBHOOK_URL
- DISCORD_WEBHOOK_URL_2 (optional)
- YOUTUBE_API_KEY (required when YouTube tracking is enabled)

## 動態追蹤設定（Actions Variables 或 Secrets）

在 GitHub 儲存庫 **Settings → Secrets and variables → Actions → Variables**
選擇 **New repository variable**，新增以下設定。修改後下次定時或手動執行即生效，無需改程式碼。
也可在 **Secrets → New repository secret** 設定同名值。兩邊都有設定時，優先使用非空的 Variable。

| Variable | 填寫方式 | 未設定時 |
| --- | --- | --- |
| `X_USERNAME` | `WhereWindsMeet_`、`@WhereWindsMeet_` 或 `https://x.com/WhereWindsMeet_` | 繼續追蹤 `WhereWindsMeet_` |
| `YOUTUBE_CHANNEL_ID` | `WhereWindsMeet`、`@WhereWindsMeet`、`https://www.youtube.com/@WhereWindsMeet`、完整 `UC` 頻道 ID 或 `/channel/UC...` 連結 | 不啟用 YouTube 追蹤 |

YouTube 裸帳號名稱視為 handle，不是任意頻道顯示名稱；不支援 `/c/`、`/user/` 或影片連結。
handle 會先透過官方 Data API 解析為 ID，再使用該 ID 取得影片與去重。
頻道 ID 連結格式可參考 [YouTube 官方說明](https://support.google.com/youtube/answer/6180214?hl=zh-Hant)。
本機執行時也可設定同名環境變數。

YouTube 僅透過官方 YouTube Data API v3 解析頻道及檢查新影片。
啟用 YouTube 追蹤時必須設定 `YOUTUBE_API_KEY`；缺少金鑰會直接報錯。
通知包含影片連結、縮圖、原標題及 DeepL 翻譯；翻譯不可用時顯示原標題。
X 和 YouTube 都發送到已設定的 Discord 頻道，每 3 小時檢查一次。

每個 X 帳號及 YouTube 頻道分開保存去重記錄，切回先前追蹤的帳號時沿用其記錄。
首次追蹤新帳號／頻道只發送目前最新一則，並將當次取得的其他內容記為已讀。
原本的 X `state.json` 記錄會自動相容，無需刪除或手動重設。
每次只取得最近最多 50 個上傳項目；長時間停用期間的所有影片不保證都能補回。
任一來源出錯仍會檢查另一來源，最後 Actions 會標示失敗以便排查。

## YouTube API 設定

1. 在 [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) 建立或選擇專案，啟用 **YouTube Data API v3**。
2. 在 **APIs & Services → Credentials** 建立 API Key，將 API 限制設為 **YouTube Data API v3**。
3. 在 GitHub **Settings → Secrets and variables → Actions → Secrets** 新增 `YOUTUBE_API_KEY`，填入金鑰。
4. 保留現有的 `YOUTUBE_CHANNEL_ID`，執行最新版本的 Actions。

API 模式讀取頻道 uploads 播放清單中最近最多 50 個項目，通知公開影片；
沿用原有的頻道 ID 和影片 ID 去重記錄，不需重設 `state.json`。
參考 [官方頻道查詢](https://developers.google.com/youtube/v3/docs/channels/list) 及
[官方上傳清單查詢](https://developers.google.com/youtube/v3/docs/playlistItems/list)。

## 本機驗證

安裝 `requirements.txt` 後執行 `python -m unittest discover -s tests -v`。
測試使用模擬網路回應和臨時狀態檔，不會發送 Discord 訊息或修改正式 `state.json`。

## 第二個 Discord 頻道

在第二個 Discord 頻道建立 Webhook，然後在 GitHub 儲存庫的
**Settings → Secrets and variables → Actions → New repository secret**
新增 `DISCORD_WEBHOOK_URL_2`，值填入該頻道的 Webhook URL。
原本的 `DISCORD_WEBHOOK_URL` 保留為第一個頻道。

定時執行和手動執行 Actions 都會將新推文及同一份翻譯發送至兩個頻道。
不設定第二個 Secret 時，仍只發送至原頻道；兩個地址相同時只發送一次。
已記錄在 `state.json` 的歷史推文不會補發至第二個頻道。

任一頻道發送失敗會讓該次執行報錯，該推文不會標記為已完成；
下次執行會重試，因此已成功收到該推文的頻道可能收到重複訊息。
