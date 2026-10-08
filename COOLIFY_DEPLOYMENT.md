# Meet2Notes 部署至 NCDRCC Coolify

本次目標：`https://meet2notes.ncdrcc.com`。平台管理介面：`https://deploy.ncdrcc.com`。
無既有資料須搬移。VM 為 16 CPU／128 GB RAM、無 GPU；平台文件列出 14 TB SSD、Docker root `/data/docker`、Traefik 80/443。Coolify 版本尚未提供。

## 1. 部署與登入

1. 管理者在 Coolify 建立 GitHub Application，來源 `Tedliu13/Meet2Notes`，選擇已包含本次修改的 branch／commit。
2. Build Pack 選 **Docker Compose**，Base Directory `/`，Compose Location `/docker-compose.yaml`。
3. 載入 Compose 後，meet2notes service 的 Domain 設為 `https://meet2notes.ncdrcc.com:8765`；`:8765` 指容器內部路由 port，使用者仍開啟不帶 port 的正式 HTTPS 網址。
4. 填入下表三個必要 secrets（runtime variables），確認 storage、資源限制及 health check 後由管理者部署。
5. 瀏覽器會顯示 HTTP Basic 登入視窗。所有頁面、API、靜態資源均需登入，只有 `/api/health` 公開。

這是個人、單一 workspace 的部署；共用帳密者可以存取全部會議及設定。正式導入多人使用前，需另行設計帳號、角色、資料歸屬與隔離，不能把 Basic Auth 視為多租戶支援。

參考：[Coolify Compose](https://coolify.io/docs/applications/builds/docker-compose)、[持久化儲存](https://coolify.io/docs/applications/configuration/persistent-storage)。

## 2. 環境變數

| 變數 | 必要性／設定 |
|---|---|
| `M2N_AUTH_USERNAME` | 必要，個人登入帳號 |
| `M2N_AUTH_PASSWORD` | 必要，管理者設定強密碼；不得提交 Git |
| `M2N_SECRETS_KEY` | 必要，Fernet key；獨立於資料備份保管，重新部署保持不變 |
| `M2N_ALLOWED_HOSTS` | 預設 `meet2notes.ncdrcc.com`，多個 hostname 用逗號分隔 |
| `M2N_MAX_UPLOAD_MB` | 預設 `0`，不設應用大小上限；可設正整數限制 |
| `M2N_MAX_HEAVY_JOBS` | 預設 `4`，範圍 1–4，持久化 job queue workers |
| `M2N_CPU_LIMIT` | 預設 `8` CPU，留資源給共用 VM |
| `M2N_MEMORY_LIMIT` | 預設 `32g`；管理者依實際模型及其他專案調整 |
| `M2N_CPU_THREADS` | 預設 `2`；首次 Hosted 啟動設定 Faster Whisper CPU threads，亦控制 BLAS/OMP threads |
| `M2N_LOG_LEVEL` | 預設 `INFO` |
| `M2N_LLAMACPP_VERBOSE` | Compose 預設 `true`，保留原生 llama.cpp 訊息至容器 stderr，供推論崩潰診斷；桌面預設關閉 |
| `M2N_PYANNOTE_TOKEN` | 選用，下載 gated Pyannote 模型時需要先接受條款 |

生成 `M2N_SECRETS_KEY`（在可信任的 Python 環境執行，將結果只填入 Coolify）：

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

若啟動 logs 顯示 `Incorrect padding` 或 `Fernet key must be 32 url-safe base64-encoded bytes`，代表收到的 `M2N_SECRETS_KEY` 格式錯誤，程式退出後會依 restart policy 重啟。這不是登入密碼，也不是任意 32 字元字串。上方指令只使用 Python 標準函式庫，可在本機 PowerShell 執行；輸出應為 44 個字元，以 `=` 結尾。Coolify Normal view 的 Value 只貼完整輸出，不包含 `M2N_SECRETS_KEY=` 前綴、引號、`b'...'` 或換行，不要刪掉結尾 `=`；取消 Build Variable，保留 Runtime Variable，保存後重新部署讓容器取得新值。不需要為環境變數修正重新 build image，也不要刪除 volumes。

若此部署從未成功啟動及保存 API／Webhook 憑證，可使用新生成的 key。若曾保存 `/data/secrets/credentials.enc`，必須先恢復原本有效的 key；換一把新 key 無法解密舊憑證。程式不會自動產生替代 key 或覆寫無法解密的檔案。不要把正式 key 貼到對話或 logs。

不要公開 key 或密碼。API key 可在 Settings 設定；原有 keyring 操作在 Hosted 模式改由加密檔案後端保存 `/data/secrets/credentials.enc`，包含摘要 provider 與 Webhook 密鑰。若要以 provider 自訂環境變數讀取 API key，需自行在 Compose 加入該變數引用，再由 Coolify 填入正式值。

Coolify 的 Environment Variables 中，本專案的 `M2N_*` 設定只供 runtime／Compose 使用，請取消 **Build Variable**，保留 **Runtime Variable**，尤其是 `M2N_AUTH_PASSWORD`、`M2N_SECRETS_KEY` 與 `M2N_PYANNOTE_TOKEN`。本 Dockerfile 不需要這些 build arguments。保存後重新部署，並保留原有加密金鑰。參考：[Coolify 環境變數的 build／runtime 範圍](https://coolify.io/docs/applications/configuration/environment-variables)。

## 3. 儲存與 mounts 稽核

| Type | Source（Compose volume 名稱） | Destination | Read-only | 用途 |
|---|---|---|---|---|
| volume | `meet2notes-data` | `/data` | false | SQLite、會議原檔／標準化音訊／匯出、聲紋、加密 secrets、logs |
| volume | `meet2notes-models` | `/models` | false | 所有模型權重、HF/Torch 模型快取與私有 runtimes |
| volume | `meet2notes-cache` | `/cache` | false | 可重建快取、multipart 暫存檔 `/cache/tmp` |

實際 named volume 名稱會帶 Compose/Coolify project prefix。Docker 依平台 root 將其放於 SSD，禁止直接指定 `/data/docker/volumes/...`。容器 UID/GID `10001:10001`；首次空 named volume 由 image 初始化目錄內容與擁有者。不要 chmod 777。

沒有 host ports、host bind mounts、Docker socket、host network、privileged。服務 drop 所有 Linux capabilities 並啟用 no-new-privileges。Compose 保留 storage 名稱；不要更換 project identity、刪除 volumes 或使用 `down -v`，以免資料遺失。

## 4. Build、啟動與模型

- VM 曾在摘要開始約兩秒後出現 `Fatal Python error: Illegal instruction`（SIGILL）。Docker 強制由 source build llama-cpp-python（`--no-binary=llama-cpp-python`），關閉 `GGML_NATIVE`、AVX／AVX2／AVX512、FMA／F16C／BMI2、AMX、SSE4.2 與 llamafile 指令集最佳化，以 CPU baseline 換取 VM 相容性，推論速度可能降低。建置會輸出 `Portable llama-cpp-python ...` 與 backend system info，拒絕宣告啟用上述擴充的 build。這是針對疑似原生 CPU 指令集不相容的修正，SIGILL 也可能來自其他 native library 或程式錯誤；必須部署新 image 並實際推論確認。不能只 restart 舊 image 或只更改環境變數。模型與資料 volumes 不需重建。參考：[llama-cpp-python source build](https://github.com/abetlen/llama-cpp-python#installation)、[ggml CPU build options](https://github.com/ggml-org/llama.cpp/blob/master/ggml/CMakeLists.txt)。
- Build：`docker compose build meet2notes`（首次需下載大型 CPU 執行套件，llama.cpp 可能編譯，時間較長）。
- Start：image CMD `meet2notes --host 0.0.0.0 --port 8765 --no-browser`。
- 內部 port：`8765`，由 Coolify 既有 Traefik 提供 HTTPS。
- Health：GET `/api/health`，確認 `status=ok`；30 秒檢查、5 秒 timeout、120 秒 startup grace、3 次失敗。
- Docker base 固定 `python:3.12.12-slim-bookworm`；Torch 安裝 CPU wheels。image 包含 FFmpeg 及各引擎執行依賴，不包含模型權重。
- Faster Whisper 的 transcription extra 限制 `av>=11,<19`：PyAV 19 移除 `av.open(metadata_errors=...)`，會造成音訊解碼時 `open() got an unexpected keyword argument 'metadata_errors'`。遇到此錯誤須部署包含此相依限制的新 commit，重新 build image；僅重啟舊 image 無效。更新後重新執行失敗會議的處理工作，保留 data／models volumes 與原加密 key，無須重新下載模型。參考：[PyAV 19 release notes](https://github.com/PyAV-Org/PyAV/releases/tag/v19.0.0)。
- `.dockerignore` 採 build context 允許清單，排除本機模型、資料、secrets、虛擬環境。

若 build 在 `apt-get` 階段以 exit code 100 失敗，請展開詳細 build log，保留失敗前的 `Err:`／`E:` 訊息；退出碼本身不足以判定是 DNS、套件來源、磁碟或其他問題。Dockerfile 在系統套件及目錄建立完成後才設定 `TMPDIR=/cache/tmp`，避免 apt 使用尚不存在的暫存目錄；build 期間先使用 base image 的 `/tmp`，不是新增主機 mount。2026-10-05 部署摘要指出 apt 階段失敗，這項順序修正仍需在 Docker 環境重新建置確認。

若重新部署後仍出現 `metadata_errors`，先確認最新部署 log 的 commit SHA 包含 `av<19` 修正，再於 Coolify 的執行中 meet2notes 容器 Terminal 執行 `python -m pip show av faster-whisper meet2notes`，取得實際 runtime 的版本與 Location。不要只依 repository 設定推定已更新，也不要用臨時 pip install 修正式容器。Dockerfile 安裝完成後會用一秒記憶體 WAV 呼叫 Faster Whisper 的 `decode_audio`，build log 應包含 `Transcription runtime: av=...` 及 `Faster Whisper WAV decode passed`；不相容時直接拒絕建置。此檢查不下載模型，也不使用正式資料。若該建置已通過而工作仍失敗，提供完整 Python traceback（從 Traceback 到最後一行），才能確認實際呼叫的程式與引擎。

部署後，在 Settings 安裝模型。先用 Faster Whisper small CPU/int8 驗證完整流程，再依實際中文錄音品質選 medium／large-v3。四個 queue workers 並不保證全部引擎四路同時推論：首次 Hosted 設定 Faster Whisper `num_workers=4`／每 worker 2 threads；其餘引擎依原本 executor／序列化限制處理。模型下載、解碼及推論都消耗 CPU/RAM，需實測長錄音並觀察 VM。

Settings 可下載本地 GGUF LLM，或設定外部 API。Ollama 若部署為另一個 container，使用容器 hostname／Coolify Internal URL，不能用 `localhost` 指向它。自訂 GGUF 的 Browse 改為列出 `/models` 已安裝檔案；可先透過模型目錄下載模型，或由管理者匯入獨立模型 volume。

### OpenAI API：AI notes 與 RAG

現有 LiteLLM adapters 已支援 OpenAI，無須新增引擎或為此重建 image。在 Settings 的 AI Engine 選 `Custom local / remote via LiteLLM`，Model preset 選 Custom，LiteLLM model 填 `openai/gpt-4.1-mini` 作為初始設定，API base URL 留空，將自己的 OpenAI API key 填入 API key 欄位並保存。此設定同時供 AI notes 與 RAG 問答生成使用；不會把語音轉錄切換到 API。模型可依帳號可用模型及實測品質調整。[模型文件](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

RAG 的 Embedding model 另選 `Custom local / remote via LiteLLM`，填 `openai/text-embedding-3-small`，base URL 留空，開啟 Enable historical RAG 並保存。它與 AI Engine 共用安全保存的 LiteLLM key，不須重複填同一把 key。更換 embedding model 後執行 Rebuild index，重新向量化已完成會議；舊 BGE-M3 向量不能和 OpenAI 向量混用。若要評估另一個 embedding model，可填 `openai/text-embedding-3-large`，切換後同樣要重建索引。[Embeddings 文件](https://developers.openai.com/api/docs/guides/embeddings)

資料庫、原始音檔、向量索引及檢索仍在 VM；摘要的會議文字／RAG 問答的相關片段與問題，以及 embedding 所需文字會送往 OpenAI。請在 Settings 輸入 key，不要貼至對話、Git 或 Docker build arguments。既有部署須保持相同 `M2N_SECRETS_KEY` 以讀取已保存 key。切換能移除這兩部分本機模型推論負擔，但網路、API 速率限制與文本長度仍影響延遲；本次只確認程式支援，未使用正式 key 做 API 實測。索引與生成均會產生 API 用量費用。[API 價格](https://developers.openai.com/api/docs/pricing)

啟動不自動下載模型，也不需要先取得模型才能通過 health check。Nemotron 等引擎按原有流程在 model volume 建立 private runtime；不要刪除正在使用的模型或 runtime。

外掛仍以 Python entry points 管理：將選定套件加入 Docker build 的 dependency installation，再重新建置，Settings rescan／enable。不要依賴進入執行中容器的臨時 pip install 來保存外掛。

## 5. 功能差異與上傳

- 匯入視窗支援拖放單一媒體檔案及點選 Browse；兩者使用相同選檔與上傳流程。拖放只更新選取檔案，仍須按 Import and transcribe；正在上傳時不能以拖放替換檔案。
- Hosted 停用原生音訊 capture、即時轉錄及 Live Assistant 寫入／啟動，保留讀取相容性。會後摘要、問答、講者、RAG、匯出及處理事件 Webhook 沿用原有流程。
- 桌面 folder picker、資料／模型位置搬移、桌面 MCP config 開啟、CUDA runtime 安裝與應用關機入口停用；持久化位置、重啟、更新改由 Coolify 管理。
- 原有 desktop 模式保持可用，`M2N_HOSTED=false` 不要求登入 secrets。
- `0` 取消應用檔案大小限制；仍驗證檔案格式、空檔案及 FFmpeg 媒體結構。
- Multipart 會先 spool 到 `/cache/tmp`，再寫原檔，須預留暫存、原檔、標準化 WAV、exports 與四個 jobs 的空間。16 kHz mono PCM 約 115 MB／小時，原檔另計；模型與 cache 的容量依選擇增加。
- Cloudflare Proxy 對單次 request body 有方案上限。若要單次大檔上傳，由管理者將此 DNS record 設為 **DNS only**，仍經 Coolify HTTPS；若必須保留 Proxy，需要另作分段上傳。本版本尚未實作分段／續傳。
- 大檔案也需檢查 Traefik 及上游網路的 upload timeout；不在 repository 修改共用 proxy。正式上線測試最大預期錄音大小。

參考：[Cloudflare 413／上傳限制](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/)。

## 6. SQLite、migration 與備份

中文轉錄可能混用簡繁字形；介面語言不控制模型輸出。Settings → General →「中文逐字稿字形」可選「繁體中文（臺灣）」或「保留模型原文」。新產生的最終中文逐字稿使用純 Python OpenCC `s2tw` 在本機轉換，儲存後供摘要、搜尋、RAG 與匯出使用；不更改時間戳記或講者資訊，變更前文字保留於片段 metadata 的 `transcription_original_text`。不自動改寫現有逐字稿；要套用需重新轉錄產生新版本。Faster Whisper 偵測到中文後，暫定片段亦會轉換；其他引擎若尚未回報語言，會到完成時才轉換。日文、英文等其他語言與翻譯為英文的工作不轉換。`s2tw` 僅統一字形，不將「軟件」等原詞改寫為臺灣慣用詞，也不能修正誤聽；人名及專業用字仍須核對。[OpenCC 字形與用詞設定](https://github.com/yichen0831/opencc-python#conversions-轉換)

Compose 的 `M2N_CHINESE_TRANSCRIPT_SCRIPT` 預設 `traditional`，桌面預設 `original`；只在 workspace 尚無該偏好設定時初始化，之後以 Settings 儲存值為準。提交的新工作會保留當時設定，不因另一個瀏覽器中途修改而改變。新 dependency 含軟體轉字字典、不含推論模型；既有部署需 rebuild image，無需下載模型、修改 SQLite schema 或刪除資料 volume。

介面可選英文、西班牙文與繁體中文（`zh-TW`）。在語言選單或 Settings → General → Interface language 選擇「繁體中文」後會立即套用，並儲存為共用 workspace 的設定；重開頁面仍會保留。既有部署需部署包含 `zh-TW.json` 的新版 image。介面語系與轉錄語言、AI 筆記的輸出語言指令分別設定，不會因切換介面而重建索引或下載模型。

Rebuild index 顯示 `Embedding ...: chunks 1-11 of 11` 代表正在等待該批 embedding，並不代表已完成 11 個片段。新版在工作進度與應用 log 列出 provider／model，每 10 秒報告等待秒數，批次完成才計入進度；等待訊息只證明應用仍能更新狀態，不能證明模型／API 正在有效推進。應用 log 可找 `RAG indexing started`、`RAG batch started`、`RAG batch waiting`、`RAG batch completed`。若失敗，該會議原有向量會保留，只有全部批次成功後才替換；整次多會議重建不是單一交易。診斷請保留工作 UUID、最後進度、Settings 的 embedding profile／model，以及 `meet2notes.log` 和 `native-fault.log` 的末段；不要提供 API key。改用外部 API 必須保存 RAG 的 embedding 設定，單獨改 AI Engine 不會改變索引所用模型。

AI notes 顯示 `running` 時，先查看該 summarize 工作的進度文字與應用 logs。CPU 本機模型可能仍在載入或生成；外部 API 則需檢查 provider／model／base URL 與連線（另一容器不能用 localhost）。啟動時會把上次中斷而仍為 running 的摘要改為 failed，保留已完成內容及 queued 摘要，不自動重送模型／API 請求；管理者確認原因後可在 AI notes 使用 Rebuild。若工作仍在運行，重啟不是通用解法；請保留工作 UUID、進度、模型設定及錯誤 traceback 供診斷，不提供 API key。

Activity 是記憶體中的本次執行記錄，重啟後清空；`/data/logs/meet2notes.log` 及輪替檔才是跨重啟的應用紀錄。Hosted CLI 另外把 Python 可捕捉的原生 fatal signal traceback 寫到 `/data/logs/native-fault.log`（啟動時超過 5 MiB 輪替一份）；診斷報告同時列出應用與 native fault log。SIGKILL／OOM kill 不會產生 Python traceback，須由管理者檢查容器退出／重啟事件；檔案只有啟動標頭也不能證明沒有崩潰。`M2N_LLAMACPP_VERBOSE=true` 避免 llama-cpp-python 在靜默模式下把原生載入期間的 process stdout／stderr 暫時導向空裝置。模型成功載入只證明載入階段完成，推論是否完成須看 `Started summarize`、`Local AI notes inference started` 與工作完成紀錄；請勿把 restart recovery 訊息當作原始錯誤原因。

本專案保留既有 SQLite／FTS5／向量儲存，不需要 `DATABASE_URL`、PostgreSQL resource 或 extensions。這是原有 fork 的容器化例外；不能只設定 PostgreSQL URL 就切換資料庫。

Migration 在啟動時由 `MigrationRunner.apply()` 執行編號 SQL，已套用版本記錄於 schema_migrations，可重複啟動，不 drop 現有表。只運行一個 app process／一個 replica，禁止多 container 共用同一 writable SQLite volume；四個 jobs 由同一 process 處理。

每日備份 `/data`，保留 7–14 份並同步異地；模型可重下載，模型清單／設定應隨資料保存。大型自訂模型另備份 `/models`。`/cache` 不須備份，僅可在停止服務後清理。重要音訊位於 `/data/meetings`，不是 cache。

一致性備份／還原由管理者使用 Coolify storage backup 或核准的備份工具：

1. 備份前暫停匯入與修改，等待 jobs 完成；停止 app 後備份整個 data volume，可取得資料庫及音訊一致的時間點。若採在線備份，SQLite 必須使用 Python `sqlite3.Connection.backup()` 等 backup API，不可只複製 `app.db` 而漏掉 WAL。
2. 將 `/data` 備份保存到管理者核准 `/data/backups/meet2notes/` 或異地 storage；不要由 app 自行新增 host mount。
3. 獨立安全保存 `M2N_SECRETS_KEY`；失去此 key 就無法還原 provider／Webhook secrets。不要把 key 與加密檔案一起公開。
4. 還原時停止 app，備份目前狀態，還原到對應 data volume，維持 UID/GID 10001、容器內 `/data` 與 `/models` 路徑及相同 secrets key。
5. 以相容版本啟動，驗證 health、會議、音訊播放、摘要／RAG 及密鑰可讀。migration 前保留快照；rollback image 不會撤銷 database migration，必要時還原配套資料備份。

上線前至少以一份測試會議驗證重新部署資料保留及備份／還原；目前尚未在正式平台驗證。

### 聲紋儲存狀態

講者卡片新增「歸入既有人物」：先儲存一份清楚聲紋，再將誤拆的其他講者逐一連結到同一已儲存人物。使用既有 `speakers.profile_id` 關聯並沿用人物姓名，不因同名自動歸併，不覆寫、追加或重新計算既有聲紋樣本；不需要 migration、額外 runtime variable 或模型下載。卡片會顯示「已連結既有人物」，已儲存人物的會議清單會包含該會議。API 驗證講者屬於指定轉錄、人物存在且樣本可用；重複指定同一人物不建立新資料。

這是人物身分連結，不合併講者卡片、逐字稿片段或各講者摘要。原有時間戳記、speaker IDs 與匯出均保留。重新辨識講者會重建講者資料，需重新核對人工歸屬；已生成的摘要不自動重寫。選錯時可再使用同一操作改指定正確人物。請管理者部署後以兩組誤拆講者測試連結、樣本播放、重新載入及資料持久化。

「記住此聲紋」視窗儲存時顯示進度；成功後才關閉，失敗可重試。儲存已成功而畫面更新失敗會分別提示，避免重複提交。可在「講者」頁面的「已儲存的聲紋」確認姓名及播放樣本；按鈕停用本身不能證明儲存成功或失敗。前端變更需要部署最新 commit 並重新載入頁面。

### LiteLLM 初始化與錯誤日誌

Hosted 啟動在工作佇列之前同步初始化已安裝的 LiteLLM，減少摘要／RAG worker 與 asyncio 日誌同時匯入模組造成 `_ModuleLock` deadlock 的機會；載入失敗會保留 traceback，其他功能仍可啟動。此修正沒有更換 LiteLLM 版本，不代表外部 API 連線已驗證。

若轉錄在 `append_segment` 出現 `sqlite3.OperationalError: database is locked`，代表儲存片段時有資料庫鎖競爭。Repository 的共用 Database 現在於同一 app process 內序列化寫入交易，並使用 `BEGIN IMMEDIATE` 在讀取前取得 writer，避免 WAL 讀取快照升級寫入失敗。讀取仍使用獨立連線；保留 WAL、SQLite 格式及 migrations，不重跑模型／API。這不是跨容器鎖，仍只允許單一 replica。不要靠刪除 SQLite、WAL 或 volume 處理鎖競爭。

若 traceback 出現 `litellm/_logging.py` → `rust_bridge/diagnostics.py` → `KeyError: 'litellm'`，表示日誌過濾器本身失敗，這段 traceback 不能單獨證明影片解碼、轉錄、OOM 或原生推論當機。摘要與 RAG 共用的 LiteLLM 載入入口會保留初始化例外的 chained traceback，將此次失敗新增的 LiteLLM 過濾器替換成安全診斷過濾器；正常初始化的密鑰遮蔽不變。安全過濾器只記錄例外類型及堆疊位置，省略可能含密鑰／逐字稿的訊息與附加欄位。此修正不自動重送 API 請求，也不恢復中斷工作。

Docker build 另以非 root 使用者檢查 LiteLLM 初始化、completion／embedding 入口及 asyncio logging。檢查只使用套件內建 cost map（`LITELLM_LOCAL_MODEL_COST_MAP=True` 僅用於此 build 步驟），不呼叫 API、不下載模型。不應以 `LITELLM_DISABLE_REDACT_SECRETS=true` 規避錯誤。實際 API 連線仍須部署後驗證。

管理者請從 **應用容器的 Terminal** 取得 `/data/logs/meet2notes.log`、輪替檔與 `/data/logs/native-fault.log` 中當機時間前後的記錄；VM 主機的 `/data` 不是容器的 named volume 路徑。對照 `Started ... job`、`Job ... failed` 的 UUID，以及 Coolify 容器重啟時間／退出狀態。`OOMKilled=true` 才是明確的容器 OOM 證據，退出碼 137 單獨不能確定 OOM；SIGKILL 不會留下 Python fatal traceback。可先用以下指令取得版本與檔案末段，不需提供金鑰：

```sh
python -c "from importlib.metadata import version; print('litellm:', version('litellm'))"
tail -n 300 /data/logs/meet2notes.log
tail -n 100 /data/logs/native-fault.log
```

無法辨認原始工作階段時，先保留上述記錄，避免反覆 Rebuild 掩蓋時間線。這段日誌相容性修正尚不能證明 40 分鐘影片的實際故障原因已排除。

## 7. 遠端 MCP

保留原有 read-only stdio MCP gateway。桌面客戶端須本地安裝此版本 Meet2Notes 的 MCP runtime，設定：

```text
M2N_MCP_BASE_URL=https://meet2notes.ncdrcc.com
M2N_MCP_ALLOW_REMOTE=1
M2N_MCP_AUTH_USERNAME=<登入帳號>
M2N_MCP_AUTH_PASSWORD=<登入密碼>
```

命令為本地 Python `-m local_meeting_ai.mcp.server`，不是容器內 Python 路徑。Hosted Settings 會產生使用 `python` 與遠端變數的 JSON/TOML 範本；先在桌面安裝此版本，再將 command 改為該本地環境的 Python 完整路徑並填入登入資訊。畫面不顯示正式密碼。MCP access 仍由 Settings 開關控制。

## 8. 管理者上線核對

- 確認 branch/commit、Domain、三個 secrets，並確認不將 secret 設為 build arguments。
- 確認 named volumes、8 CPU／32g 起始限制符合平台配額，持久化 UID 正確。
- 確認 Cloudflare Proxy/DNS only 選擇及大檔上傳；Traefik/SSE 串流可用。
- 確認 HTTPS 無登入不可讀取資料，登入後匯入、轉錄、講者、摘要、RAG、匯出正常。
- 驗證重新部署、四個工作、取消、模型 persistence、加密 credential persistence、備份與還原。
- 容器 logs `10m × 3`，應用檔案 logs `5 MiB × 4` 輪替。
- 本機尚無 Docker CLI，必須在有 Docker 的驗證環境執行 `docker compose config`、image build 與 container smoke test，成功後才正式上線。

本次僅準備 repository，不代表正式部署完成。

## 9. 本次驗證結果（2026-10-05）

- API、migration、storage、paths 與加密 credential 這輪測試：85 passed、1 deselected。
- MCP 與 Hosted／credential／storage 專項：20 passed（部分與上一輪重疊）；另外確認 3 MiB multipart 檔案可在不限大小設定下匯入。
- 前端：20 passed，包含 Hosted 選檔與桌面麥克風預設操作的回歸測試；修改的 JavaScript 語法檢查通過。
- `ruff check .` 通過；`mypy src` 通過（117 source files）；介面翻譯目錄與 `git diff --check` 通過。
- Compose／workflow YAML 可解析，CI 內嵌 Python 可編譯；三個 named volumes、無 host ports／危險 mounts 及變更檔案未含私有資料／模型的檢查通過。
- 1 項既有 FastEmbed 原生模型目錄測試未執行：本機未安裝完整 ONNX Runtime／Pillow／tokenizers 執行依賴；未下載模型權重。
- 本機無 Docker CLI／daemon，未執行 `docker compose config`、Docker image build 或容器 smoke test。新增的 `hosted-container` GitHub Actions 將驗證這些項目，以及 container restart 後的資料、model volume 與加密 credential persistence；目前尚未執行該 workflow。
- 正式 Coolify／DNS／HTTPS、大檔案端到端上傳、四路實際模型推論、資源峰值與備份／還原仍由管理者於上線前驗證。
