# Meet2Notes：NCDRCC Coolify 專案規範

## 專案需求與平台邊界

- 正式網域為 `https://meet2notes.ncdrcc.com`（使用者指定，取代平台通用 apps 網域）。
- 部署於無 GPU 的共用 VM；Coolify 管理介面為 `https://deploy.ncdrcc.com`，公開流量經既有 Traefik 80/443。
- Docker root `/data/docker` 與 Coolify `/data/coolify` 位於 14 TB SSD；不得修改主機 Docker、代理、防火牆或安裝另一套代理。
- 僅修改、測試 repository。不得登入正式 VM 或直接部署。正式部署由管理者操作 Coolify 或已設定的 GitHub webhook 觸發；push 前確認使用者授權。
- 不要求管理帳密、SSH key 或 Docker socket 權限。不得提交 secrets。

## 已確認的專案選擇

- 保留 fork 的 SQLite、FTS5、RAG 向量格式與版本化 migrations；本次是既有應用容器化，不依平台「全新專案預設 PostgreSQL 18」重寫資料層。
- Hosted 使用個人帳號與可撤銷的 HTTPS cookie session；現有 Coolify `M2N_AUTH_USERNAME`／`M2N_AUTH_PASSWORD` 僅在帳號資料庫為空時建立初始管理者，之後不覆寫資料庫密碼。Basic API 認證使用個人帳號並套用同一套權限。
- 所有已登入且啟用中的使用者可觀看全部會議、播放、匯出及使用會後問答／RAG；這是共享會議庫，不是私人資料隔離。只有管理者、建立者、共同管理者可修改會議、逐字稿、講者及 AI 筆記或取消該會議工作。設定與全域配置僅管理者可用；共同管理名單僅管理者、建立者可修改。
- 註冊無需審核，立即啟用一般帳號，不接受自行指定角色、建立者或啟用狀態。停用、刪除、重設密碼必須撤銷 sessions；刪除採帳號 tombstone 保留會議歸屬，帳號名稱不可重用。既有會議首次升級歸初始管理者。不得刪除最後管理者。
- 管理者可在每場會議／會議庫修改建立者與共同管理者；只有管理者可轉移建立者，目標須為啟用且未刪除的帳號。轉移不自動保留原建立者為共同管理者；其他共同管理者保留，新建立者的重複共同管理身分移除。共同管理者寫入時須在同一寫入交易重新確認最新建立者，避免轉移後舊權限競態。
- 新增會議可勾選已知講者的已儲存聲紋（非登入帳號或共同管理者名單），隨會議保存並供獨立／整合式及重新辨識使用。非空名單僅限其候選聲紋，空名單沿用全庫比對；刪除已選聲紋不得自動擴大比對範圍。保留既有比對門檻與全域辨識開關，不強迫分配人物、不以勾選數設定講者總數。migration 015 保存原始 profile IDs，避免最後一筆聲紋刪除後誤退回全庫模式。
- Hosted 模式停用麥克風、系統音訊、即時錄音／轉錄及依賴它們的即時助理；保留音檔匯入、最終轉錄、講者辨識、摘要、Prompt、RAG、匯出、外掛與會後 Webhook。
- 模型不得進入 image 或 build context。部署後才下載至獨立 `/models` named volume，含模型快取及私有模型 runtimes。
- VM 曾在摘要推論出現 SIGILL；Docker 的 llama-cpp-python 必須由 source build、關閉 native／進階 x86 指令集最佳化，不能在未驗證 VM CPU 能力前恢復 `-march=native` 或換用預編譯 wheel。
- `/data` named volume 保存 SQLite、錄音、聲紋、摘要、加密 secrets 與應用 logs；`/cache` 保存可重建快取及暫存上傳。
- 應用上傳大小 `0` 表示不設大小上限，不能宣稱 Cloudflare／代理／磁碟亦無上限。
- 工作佇列設四個 workers；推論並行度仍受引擎自身限制。CPU/RAM 限制必須可調，不假定 VM 容量無限。

## 修改與驗證

- 維持桌面模式相容性，Hosted 行為以設定開關隔離。
- Web 監聽 `0.0.0.0:8765`，只 expose，不映射 host ports。
- 優先 named volumes，頂層明確宣告；禁止 privileged、host network、Docker socket 或未經核准的 host bind mounts。不得 chmod 777。
- 密鑰僅由 Coolify runtime variables 注入。加密密鑰需獨立備份，不能存於 Git、image 或模型。
- 更新 `.env.example`、README、`COOLIFY_DEPLOYMENT.md`；文件涵蓋 build/start、storage、health、migration、資源、備份與還原。
- 執行相關單元／API／前端測試、lint、Docker build 與 `docker compose config`；工具不可用時明確列出未驗證事項，不宣稱成功。
- 回報 mounts 的 type/source/destination/read-only，確認無 secrets、host ports 或危險權限。
- 備份使用 SQLite backup API 或停機一致性備份；每日保存重要檔案，7–14 份保留並做異地備份。上線前管理者須測試還原。
- 不刪除正式 containers、volumes、mounts 或資料；持久化 volume 不等於備份。
