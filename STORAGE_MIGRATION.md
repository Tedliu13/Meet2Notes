# Meet2Notes 單一專案儲存遷移

目標：將 Meet2Notes 現有會議、帳號、聲紋、模型及快取，從系統碟 named volumes 複製到大容量磁碟，保留原資料及可回復來源。

2026-10-21 已由使用者授權以下三個掛載。此流程僅由管理者在 VM／Coolify 執行；代理、其他應用與主機 Docker root 均不變更。

| type | 主機 source | 容器 destination | read_only |
|---|---|---|---|
| bind | `/data/meet2notes/data` | `/data` | false |
| bind | `/data/meet2notes/models` | `/models` | false |
| bind | `/data/meet2notes/cache` | `/cache` | false |

目前已確認舊 volume 名稱前綴為 `9fusshae9rh2rgjkgtxstd2z_meet2notes-`，來源位於 `/var/lib/docker/volumes/`。主機 `/data` 為獨立 XFS 大磁碟。容器內部路徑不變，因此錄音、模型及聲紋既有絕對路徑不用改寫。

## 1. 準備及唯讀檢查

先暫停本專案自動部署／Git webhook 觸發，避免複製時被重新啟動。這不是停用共用代理。通知使用者維護時間，停止新增上傳／編輯，等待所有 queued、running、paused 工作結束。

將 repository 的 `scripts/migrate_coolify_storage.py` 複製到 VM，例如 `~/migrate_coolify_storage.py`；不需要安裝套件，使用主機 Python 3.11+、Docker CLI、GNU cp／tar／du 與 findmnt。工具不包含帳密或 API keys，不能在應用容器內執行。

在 VM 主機 Terminal 查詢最新容器 ID；後續命令的 `CONTAINER_ID` 都須換成此 ID，不要將字樣原封貼上：

```sh
sudo docker ps -a --filter name=meet2notes-9fusshae9rh2rgjkgtxstd2z --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
sudo python3 ~/migrate_coolify_storage.py check --container CONTAINER_ID
```

`check` 只讀取設定、容量及 SQLite，不建立目的地、不停止任何容器。它只接受本專案名稱與三個已知 volumes，確認 `/data` 是獨立且至少 1 TiB 的掛載，並列出帳號／會議等筆數、進行中工作與估計空間。

將檢查結果保存。`active_jobs` 必須為空。若有殘留工作，先在應用介面確認／處理，不直接改 SQL 或刪除會議。

獨立備份 Coolify 現有 `M2N_SECRETS_KEY`。保留既有所有 runtime variables，不重設密碼、不產生新加密密鑰，不將密鑰放進腳本或 Git。

## 2. 停止並複製

只停止 Meet2Notes，其他容器維持運作。可於 Coolify Stop，或由管理者在 VM 執行：

```sh
sudo docker stop --time 120 CONTAINER_ID
sudo python3 -u ~/migrate_coolify_storage.py copy --container CONTAINER_ID
```

`copy` 要求來源容器停止，沒有其他執行中的容器使用相同來源／目的地，且目的地三個目錄不存在或為空；不覆寫既有非空目錄。保持自動部署暫停與維護狀態，避免複製期間有另一個寫入者啟動。

工具依序執行：

1. 再次確認來源、獨立大磁碟、工作佇列及空間。
2. 建立 `/data/meet2notes/backups/時間戳記-隨機碼/`，以 SQLite backup API 建立資料庫備份，並以 `quick_check`／筆數驗證。
3. 建立整份 data 的 `data.tar`，串流讀回每個檔案，檢查 SHA-256 與連結，不將備份解壓到原資料。
4. 使用 `cp -a --sparse=always` 複製三個目錄，保留 UID/GID、權限、symlinks 與原 volumes。模型虛擬環境內連結保留原樣。
5. 比較來源／副本檔案內容 SHA-256、目錄與連結及數字 UID/GID／權限；驗證目標資料庫筆數。SQLite 可重建的 `app.db-shm` 不納入雜湊，`app.db` 與 `app.db-wal` 仍驗證及保存。
6. 通過後才寫入 `migration-state.json` 與三個 `.meet2notes-storage-verified.json` 標記，輸出 `VERIFIED`。

備份／manifest 使用私有權限；驗證標記不含 secrets，可供 UID 10001 讀取。工具不停止／重啟容器、不刪除資料、不修改 daemon.json，不會更改整機 Docker 的 data-root。

檔案與模型較多時，雜湊及備份可能需要數分鐘；保持終端執行完成。失敗會顯示 `STOP`，不刪除來源與部分副本。若已有非空副本而沒有驗證完成，先保留並查明原因，不要自行清空或反覆重跑覆寫。

若已產生 migration-state，可在切換前重新驗證，重新建立驗證標記：

```sh
sudo python3 -u ~/migrate_coolify_storage.py verify --container CONTAINER_ID
```

只在來源與目的地均離線時驗證。若沒有 migration-state，表示前次流程尚未完成，須先檢查中斷位置。整份 data 備份另做異地保存；持續每日備份、保留 7–14 份，密鑰獨立保存。

## 3. Coolify 切換

確認已得到 `VERIFIED`、完成備份還原內容檢查，再將經審閱的新檔案提交／推送到 Coolify 能讀取的 repository。代理執行 push 前仍須使用者授權；遷移工具本身也可先由管理者手動傳到 VM，不必提前 push。

在此 Meet2Notes resource 將 **Compose Location** 從 `/docker-compose.yaml` 改為 **`/docker-compose.storage.yaml`**，保留 domain、runtime variables、secrets 與其他資源設定，並由管理者手動部署。

獨立檔案保留原 image build、安全權限與資源設定，僅改三個掛載並增加啟動檢查。`create_host_path: false` 防止自動建立空目錄；缺少任一驗證標記或既有 app.db 時直接拒絕啟動，避免誤建全新帳號庫。

如果 Coolify 仍另外列出舊 persistent storage，不刪除舊 volumes。檢查產生的 Compose，確保每個 `/data`、`/models`、`/cache` 只出現一次掛載，且 source 是上述核准主機路徑。

### Docker Hub 中斷時重用主機既有 image

2026-10-22 已確認 VM 上存在以下 image，內容為遷移前正常使用的應用版本：

```text
9fusshae9rh2rgjkgtxstd2z_meet2notes:3fd006bd4611916946629a94a0e0ef0d4d7e4413
sha256:9751e4880475ef39be0c6e5c01a15d24c4bb26d2957ba21ad6b947e19a393f18
```

僅在複製已通過 `VERIFIED` 且尚未有新版應用修改目的資料庫時使用。這次遷移
提交僅涉及部署與文件，不需要新應用程式碼；不要將這個固定舊 image 當作日後升級方式。

1. 管理者在主機確認上述 tag 的 `docker image inspect --format '{{.Id}}' IMAGE`
   結果仍為上述 SHA-256；不符即停止。不要清理這個 image 或舊 volumes。
2. 將此 repository 的新檔案提交／推送（代理 push 仍須授權），保持自動部署暫停。
3. 在原 Coolify resource 將 Compose Location 設成 `/docker-compose.storage-local.yaml`，
   不另建 resource、不使用 Docker Image build pack、不更動既有 domain 或 runtime secrets。
4. 本檔沒有 `build`，並設 `pull_policy: never`，只使用本機快取。檢查 Coolify
   產生的 Compose 確實保留這兩項設定、原 image tag、三個唯一 bind mounts 與啟動 guard。
   若曾設定自訂 Compose build/start commands，先記錄並移除其中強制 pull/build 的選項。
   不使用 Force rebuild。管理者手動部署；如果 log 仍顯示拉取應用 image／建置 Python
   base image，停止重試並貼回 log，不要改掛載或繞過 guard。
5. 使用以下上線驗證步驟核對帳號、資料筆數與檔案。應用 image 無須拉取，但 Coolify
   的 helper image、Git checkout 等仍可能需要網路，不能保證整個部署完全離線。
6. 網路修復後，切回 `/docker-compose.storage.yaml` 建置部署。兩個設定都使用同一組
   新 bind mounts，因此回到正常建置不需重新複製資料。不可切回使用舊 named volumes
   的 `/docker-compose.yaml`，避免遺失遷移後新增的資料。

三個掛載均為 `type: bind`、`read_only: false`：
`/data/meet2notes/data -> /data`、`/data/meet2notes/models -> /models`、
`/data/meet2notes/cache -> /cache`。無 host ports、Docker socket、privileged 或 host network。

## 4. 上線驗證

部署後重新找出新容器 ID，以 `NEW_CONTAINER_ID` 代入：

```sh
sudo python3 ~/migrate_coolify_storage.py status --container NEW_CONTAINER_ID
sudo docker inspect NEW_CONTAINER_ID --format '{{range .Mounts}}{{println .Type .Source "->" .Destination "RW=" .RW}}{{end}}'
```

應顯示三個核准 bind mounts，皆 RW=true。status 以唯讀方式檢查資料庫與筆數；比對遷移前輸出。設定內 data／models／cache 所在檔案系統應接近 14 TB、剩餘約 11 TB；系統 `/` 保持約 113 GiB 屬預期。

先在維護狀態測試原有帳號登入、共同管理權限、舊會議播放／逐字稿／AI 筆記、聲紋名單、模型已安裝狀態，最後測試一個小檔匯入、轉錄與匯出。確認後恢復使用及本專案自動部署。無須重新下載模型。

## 5. 回復原掛載

舊 named volumes 全程保留。若切換失敗，且確認新路徑沒有任何新會議／上傳／編輯／帳號變更，可停止新版，在 Coolify 改回 `/docker-compose.yaml`，用相同既有 secrets 重新部署，再驗證舊資料。

只要新路徑已產生任何寫入，不能直接回舊 volumes，否則新資料不會跟著回復。先停止本應用、備份新路徑與資料庫，另行規劃反向複製並驗證。不要同時啟動兩個版本，也不要刪除或 prune 舊 volumes。

此 repository 的本機測試可驗證安全檢查、備份內容、雜湊與 Compose 結構；不代表正式 VM 複製或 Coolify 部署已完成。
