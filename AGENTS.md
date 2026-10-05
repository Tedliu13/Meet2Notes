# Meet2Notes：NCDRCC Coolify 專案規範

## 專案需求與平台邊界

- 正式網域為 `https://meet2notes.ncdrcc.com`（使用者指定，取代平台通用 apps 網域）。
- 部署於無 GPU 的共用 VM；Coolify 管理介面為 `https://deploy.ncdrcc.com`，公開流量經既有 Traefik 80/443。
- Docker root `/data/docker` 與 Coolify `/data/coolify` 位於 14 TB SSD；不得修改主機 Docker、代理、防火牆或安裝另一套代理。
- 僅修改、測試 repository。不得登入正式 VM 或直接部署。正式部署由管理者操作 Coolify 或已設定的 GitHub webhook 觸發；push 前確認使用者授權。
- 不要求管理帳密、SSH key 或 Docker socket 權限。不得提交 secrets。

## 已確認的專案選擇

- 保留 fork 的 SQLite、FTS5、RAG 向量格式與版本化 migrations；本次是既有應用容器化，不依平台「全新專案預設 PostgreSQL 18」重寫資料層。
- 個人使用、單一共用 workspace，先提供 HTTP Basic 登入。這不是多租戶；多人帳號、角色與資料隔離須另行設計及測試。
- Hosted 模式停用麥克風、系統音訊、即時錄音／轉錄及依賴它們的即時助理；保留音檔匯入、最終轉錄、講者辨識、摘要、Prompt、RAG、匯出、外掛與會後 Webhook。
- 模型不得進入 image 或 build context。部署後才下載至獨立 `/models` named volume，含模型快取及私有模型 runtimes。
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
