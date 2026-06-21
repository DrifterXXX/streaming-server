# 自动下载模块审计报告

> 审计对象：`stream_server_v3_dl.py` 及关联模块（`cloud_disk_dl.py`、`pan_api.py`、`router.py`、`cloud_download.py`）  
> 审计时间：2026-06-05  
> 服务状态：streaming-server 在 launchd 下运行（port 9878，PID 2050）

---

## 一、问题速览（严重程度汇总）

| # | 严重程度 | 位置 | 问题简述 |
|---|---------|------|---------|
| 1 | 🔴 高 | `stream_server_v3_dl.py:182-184` | `_set_item` 锁释放后 `_save_queue`，写操作在锁外，队列文件可能被并发写入损坏 |
| 2 | 🔴 高 | `stream_server_v3.py:130-158` + `stream_server_v3_dl.py:67` | **双重下载子系**：`DownloadManager._start_worker` 启动原生线程，同时 `_download_executor` 也提交 `execute_download`；信号量 `_download_semaphore` 被两边共享，可能导致信号量泄漏（异常路径未统一释放） |
| 3 | 🔴 高 | `stream_server_v3.py:265-288` + `stream_server_v3_dl.py:428` | **active_downloads 类型不一致**：`cancel/remove` 期望 `_DownloadHandle`（有 `.kill()`），但 `cloud_download.py` 和 `YtDlpDownloader` 存入 `subprocess.Popen`；调用 `.kill()` 时对象类型未知，**取消下载功能实际不可靠** |
| 4 | 🟠 中高 | `cloud_download.py:84-90` | `_cloud_dl_worker_sem` 的 `_CLOUD_DL_SEMAPHORE` 与主 `_download_semaphore` 完全独立；网盘下载未经主信号量保护，可同时跑 5 个云盘任务 + 3 个 yt-dlp 任务，**磁盘空间与并发上限均失控** |
| 5 | 🟠 中高 | `stream_server_v3_dl.py:1675-1751` | `run_subscription_check` **直接读写 `download_queue.json` 而非通过 `DownloadManager`**；同一文件同时被 HTTP handler 和订阅线程无锁访问，**队列文件损坏风险高** |
| 6 | 🟠 中高 | `cloud_disk_dl.py:44` + `stream_server_v3.py:37` | **循环导入风险**：`dl_enhancer` 在 `stream_server_v3.py` 第 37 行导入，而 `dl_enhancer` 在 `stream_server_v3.py:64` 从主模块导入 `_download_semaphore`；已部分缓解但仍有脆弱性 |
| 7 | 🟠 中高 | `stream_server_v3.py:140-148` | 下载 `worker` 循环中**同一任务可能被标记 `queued` 然后又被标记 `pending`（153-156 行）**，逻辑返回到初始状态，再启动新线程；如果 `_download` 也恰好拿到信号量，**任务可能被多次提交给线程池/线程** |
| 8 | 🟡 中 | `pan_api.py:101-136` | `AliyunAPI.get_share_files` 调用 `get_shared_by_me`（用途为"我分享的文件"，非法）并传入空 `share_token`；分页逻辑死板（第二页直接 break），**第一页失败时无回退策略** |
| 9 | 🟡 中 | `pan_api.py:508-520` | `BaiduAPI.get_download_url` 中先调用 `rapidupload`（通常用于秒传，非下载曲链），失败后再调用 `/api/download`；rapidupload 失败时的错误响应{"error":...} 会被当作"有数据"处理，**下载链接逻辑混乱** |
| 10 | 🟡 中 | `cloud_disk_dl.py:231, 427, 577, 796` | 阿里云盘/夸克/天翼/迅雷**保存后仅固定 sleep(1) 等待同步**，未验证文件是否已出现在用户根目录；**网络慢时会导致"保存成功但找不到文件"误报失败** |
| 11 | 🟡 中 | `stream_server_v3_dl.py:330-331` + `pan_api.py` 多 | `BaiduAPI.get_download_url` 使用 `referer`，但 `_http_request` 通用函数**未透传 referer 头**；部分网盘 API 会因为没有 referer 拒绝 |
| 12 | 🟡 中 | `stream_server_v3_dl.py:723-776` | `handle_auto_download` 和 `handle_auto_find` 中的 `auto_search_sources` + `test_all_sources` **主 HTTP 线程阻塞 15~20 秒等待子线程完成**；`t.join(timeout=20)` 使请求处理阻塞，影响并发响应，且线程创建垃圾 |
| 13 | 🟡 中 | `cloud_disk_dl.py:268-289` | `_download_direct` 使用 `urlretrieve` 无 timeout 参数，无重试逻辑；**网速慢或服务器无响应时会永久阻塞** |
| 14 | 🟡 中 | `stream_server_v3_dl.py:108, 147` | `match_local_files` 对每个剧集文件都遍历 `NUM_EPS × FILE_COUNT`；电影 76 部，集数文件 440+，**O(N*M) 效率差（76*440~33500 次字符串匹配）**；电视剧每集也做完整匹配 |
| 15 | 🟡 中 | `stream_server_v3_dl.py` 多处 | **密码提取二义性**：`extract_password` 的 `?pwd=xxx` 正则无法匹配 `&pwd=xxx`（百度常见），与原有的 `?pwd=` 存在覆盖风险；`auto_search_sources` 里也有类似的 URL 拼接 bug |
| 16 | 🟡 中 | 全文件 | **硬编码路径**：`yt-dlp` 路径硬编码为 `/Users/ayong/Library/Python/3.9/bin/yt-dlp`（Python 版本升级即失效）；`aria2c` 未检查是否存在 |
| 17 | 🟡 中 | `stream_server_v3_dl.py:334-341` | `PATH` 环境变量前缀有重复追加风险（先检查再拼接），如果多次调用 `_do_download` 会重复插入 `extra_paths`（实际上每次子进程新建，影响小，但逻辑不严谨） |
| 18 | 🟡 中 | `cloud_download.py:59-71` | `_cloud_dl_worker_sem` 启动线程不持有 weakref，`download_manager` 持有全局类引用，但 CloudDiskDL 每次都新建；属性访问无线程保护 |
| 19 | 🟡 中 | `router.py:148` | 未知处理器直接 `raise KeyError`；如果路由 map 与 `stream_server_v3_dl.py` 中函数名不一致（如拼写差异），`getattr` 也 return None 走到 raise，导致整个 HTTP 500，无回退 |
| 20 | 🟡 中 | `cloud_disk_dl.py:184-226` | AliyunEngine.download **未在 API 请求前检查 token 是否过期**；只依赖 pan_login 的 `is_logged_in` 静态判断，实际 401 不会触发自动刷新 |

---

## 二、逐项详细分析

### 2.1 下载流程逻辑

#### 2.1.1 双重子系统冲突 🔴

**现状**：  
- `stream_server_v3.DownloadManager._start_worker`：后台 `while True` 循环，每 3 秒扫描队列，将 `pending→queued→pending`，然后通过 `threading.Thread(target=self._download)` 启动下载。  
- `stream_server_v3_dl.YtDlpDownloader`：独立封装，通过 `_download_executor.submit(dl.execute_download, item)` 提交到 `ThreadPoolExecutor(max_workers=5)` 执行。

**问题**：
- 两者竞争同一个 `_download_semaphore`（`MAX_CONCURRENT_DOWNLOADS=3`）。
- worker 中 140-148 行把任务标记为 `queued`，148 行调用 `_save_queue`，然后在 151-158 又把所有 `queued` 改为 `pending` 并启动**新线程**。
- 但 YtDlpDownloader 里也已直接 `submit` 到 executor（如 1016, 1037, 1281 行），**两套系统可能同时启动同一个 item**。

**建议**：
1. 统一入口：`DownloadManager._start_worker` 应完全交给 YtDlpDownloader 处理（或反过来），不要两套并存。
2. `_download_executor` 应基于 master 配置动态调整 max_workers（不超过 semaphore 值或略超）。
3. 添加 `task_id` 幂等性：`active_downloads` 以 item_id 为 key，重复提交时返回失败。

---

### 2.2 并发控制 🟠-🔴

#### 2.2.1 _set_item 锁范围不当 🔴

**代码**（`stream_server_v3_dl.py:180-184`）：
```python
def _set_item(self, item, **kwargs):
    with self.dm._queue_lock:
        item.update(kwargs)
    self.dm._save_queue()  # ← 锁已释放！
```

**问题**：`_save_queue` 读取 `self.dm.queue` 并写磁盘；如果另一个线程同时修改了 `self.dm.queue`，文件内容就是**两个版本的混合**（损坏 JSON）。多次 `_set_item` 调用（如进度回调）会在极短时间内反复触发，放大写冲突概率。

**建议**：
```python
def _set_item(self, item, **kwargs):
    with self.dm._queue_lock:
        item.update(kwargs)
        self.dm._save_queue()
```

---

#### 2.2.2 run_subscription_check 的锁误用 🔴

**代码**（`stream_server_v3_dl.py:1675-1751`）：
```python
queue_file = Path.home() / "services/streaming-server" / "download_queue.json"
try:
    if queue_file.exists():
        with _lock_subs():          # ← 用的是订阅文件锁，不是队列锁
            with open(queue_file) as f:
                queue = json.load(f)
            ...
            with open(queue_file, "w") as f:
                json.dump(queue, f, ...)
```

**问题**：`_lock_subs`（fcntl 文件锁）锁的是 `subscriptions.lock`，与队列无关。主服务 HTTP 请求线程用 `DownloadManager._queue_lock` 线程锁操作同文件，两者**互不感知**，同一文件可同时被读写。

**建议**：  
1. 订阅任务应通过 `DownloadManager.add()` 添加任务（走内存队列 + `_queue_lock`），然后由 worker 自动消化；不要绕过 manager 直接写文件。  
2. 如果必须直接写文件，应使用 queue_file 独立的 fcntl 锁。

---

#### 2.2.3 _download_semaphore 交叉释放风险 🟠

**代码**（`stream_server_v3.py:166-185`）：
```python
acquired = _download_semaphore.acquire(timeout=120)
try:
    self._do_download(item)
finally:
    if acquired:
        _download_semaphore.release()
```

而 `YtDlpDownloader.execute_download`（`stream_server_v3_dl.py:213-230`）也做了同样的事。

**问题**：如果 `DownloadManager._download` 为 item A 获取信号量，然后调 `_do_download`（它内部调了 `YtDlpDownloader.execute_download`），后者**再次 acquire**，会死锁或信号量计数耗尽。  
实际上 `_download` 的 `_do_download` 并不调用 `YtDlpDownloader.execute_download`，但 `worker` 启动新线程调 `self._download`，而 `/api/dl-url` 等又直接 submit 到 executor 调用 `dl.execute_download`——两者都对同一信号量操作，**如果任务类型不同，两个路径都会释放一次信号量，但只 acquire 一次就会多一次释放**。

**建议**：  
1. 确定单一路径（建议 executor 统一入口）；  
2. 在获取信号量前后记录 item_id + 计数，assert(release_count == acquire_count)，防止泄漏。

---

### 2.3 网盘引擎 🟠-🟡

#### 2.3.1 AliyunAPI.get_share_files 使用错误 API 🟡

**代码**（`pan_api.py:118`）：
```python
result = _http_request(
    f"{self.BASE}/adrive/v3/share_link/get_shared_by_me",
    "POST", data
)
```

`get_shared_by_me` 接口用途为"获取我分享出去的文件列表"，参数应传 share_id，但接口返回的是用户的分享，不是特定分享的文件列表。  
正确路径应为 `/v2/file/list_by_share`（代码里已作为 fallback 但优先用了错误的）。

**建议**：  
```python
# 改为：始终先尝试 list_by_share
result = _http_request(
    f"{self.BASE}/v2/file/list_by_share",
    "POST", data
)
```

---

#### 2.3.2 get_shared_by_me 分页 break 导致数据不完整 🟡

**代码**（`pan_api.py:113-115`）：
```python
if page > 1:
    # Simple pagination — just get first page for now
    break
```

对于超过 100 文件的分享，只会取到第一页。直接 break 让循环形同虚设。

---

#### 2.3.3 BaiduAPI.get_download_url 逻辑混乱 🟡

**代码**（`pan_api.py:496-520`）：
```python
result = self._request("/xpan/file?method=rapidupload", "GET", params=params)
# 然后
result = self._request("/api/download", "GET", params=params)
```

rapidupload 接口针对的是`秒传`（指定文件哈希秒级保存），不是获取下载链接。  
代码先请求 rapidupload（会触发文件匹配/保存副作用），再用同一组参数请求 /api/download，且两者共享 params 字典，**同次的 request 可能把 rapidupload 产生的错误写回 params**。

**建议**：直接用 `/api/download` 获取下载直链，或在 rapidupload 404 时为特定错误码时才 fallback。

---

#### 2.3.4 登录 Token 过期无自动刷新 🟡

**代码**（`cloud_disk_dl.py:933-936`）：
```python
# AliyunEngine 仅在 is_logged_in 时检查
if not self.is_logged_in():
    return {'success': False, 'error': '...', 'need_login': True}
```

`is_logged_in` 只检查 token 是否存在，不发送真实请求验证。  
当 token 已过期（401）时，`pan_api` 层获得 HTTPError（404/401），返回 `{"error": "HTTP 401"}`，上层 engine 看到 `"error" in result` 就把下载标记为失败，**不触发重登录流程**。

**建议**：  
1. `_http_request` 增加 401 检测；  
2. 上层 `engine.download` 在 401 时调用 `login_mgr.refresh(pan_key)` 后重试。

---

#### 2.3.5 detect_pan URL 正则重叠 🟡

**代码对比**：

- `cloud_disk_dl.py:76`：`(?:www\.)?(aliyundrive\.com|alipan\.com)/s/([a-zA-Z0-9_-]+)` 支持非分享链接（大于 `?`）？
- `cloud_disk_dl.py:77`：`(pan\.baidu\.com)/s/([a-zA-Z0-9_-]+)` → 百度分享链接
- `stream_server_v3_dl.py:72-75`：多种模式，包括 `pan.baidu.com/s/` 带可选密码 `(?:\?pwd=...)`？

实际 `PAN_PATTERNS` 里百度 URL 正则不带密码部分，而 `carryURL_PATTERNS` 里带了。  
这导致百度网盘链接带 `?pwd=xxx` 时，`PAN_PATTERNS` 的正则不匹配，**密码拼接逻辑（574-578 行）依赖已经带有 pwd 的 URL**，一旦 URL 来自外部搜索而非数据库，密码丢失导致下载失败。

---

#### 2.3.6 引擎单实例设计 🟡

**代码**（`cloud_disk_dl.py:18-23`）：
```python
_ALIYUN_ENGINE = AliyunEngine()   # 模块单例
```

同时在 `cloud_download.py:29`（每次请求新建 `CloudDiskDL()`）和 `stream_server_v3_dl.py:1012`（每次 `auto_download` 新建）也实例化。  
`_LoginMgr` 是模块级单例，但 CloudDiskDL 每次新建都会重新访问它，没有引入额外开销，但 `QuarkEngine` 和 `AliyunEngine` 各自保存 `self.bin` 状态，反复创建浪费且可能引起 path 问题。

---

### 2.4 错误处理 🟡

#### 2.4.1 _download_direct 无 timeout 且无重试 🟠

**代码**（`cloud_disk_dl.py:110-125`）：
```python
urllib.request.urlretrieve(url, str(tmp_path), report_hook)
```

`urlretrieve` 无 timeout，遇到慢速直链或下载中断会永久阻塞该线程。  
整个 `_download_executor` 的 thread 会被卡死，导致信号量永远不可用。

**建议**：改用带 `timeout` 的 `urllib.request.urlopen` + chunked 读取，或切换到 `requests`/`httpx`。

---

#### 2.4.2 磁力链 aria2c 子进程 stderr 丢弃 🟡

**代码**（`stream_server_v3_dl.py:346-349`）：
```python
process = sp.Popen(
    cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, env=env
)
```

磁力链日志完全丢弃，aria2c 错误（如 tracker 拒绝连接、端口冲突）无法诊断。  
当下载失败时返回的 error 只有 `"无做种或下载超时"`，原因完全丢失。

**建议**：stderr 重定向到文件（带按时间戳命名），失败时附带最后 200 字符。

---

#### 2.4.3 HTTP 请求未使用 retry 且 retry 逻辑分散 🟡

`pan_api._http_request` 没有重试，遇到瞬时 502/504 即失败。  
但有些地方有 sleep(1) 兜底（AliyunEngine 231 行），有些则完全没有。

**建议**：`_http_request` 内加 `@retry(attempts=3, delay=1, backoff=2)`，针对 429/502 等特定码。

---

#### 2.4.4 数据库写竞争导致丢失更新 🟡

多个端点（`update_download_urls`、`handle_save_source`、`handle_save_search_to_library`、`MediaDB.add_entry`）都**独立读取 → 修改 → 写回 `database.json`**。  
两个并发写请求会互相覆盖（后写入者覆盖前者），无声丢失数据。

**建议**：统一使用 `MediaDB._write` 或数据库级锁，引入 atomic write（`tempfile` + `os.replace`），防止崩溃时文件截断。

---

### 2.5 队列机制

#### 2.5.1 download_queue.json 线程安全不足 🔴

如上 2.2.2 所述：HTTP handler 线程（DownloadManager）用线程锁，订阅任务用 fcntl 文件锁，两者**完全不同步**，共享文件可同时被读写。

同时，`YtDlpDownloader._set_item` + `cloud_download._cloud_dl_worker` 的两个线程可在同一时刻向 `download_queue.json` 写不同版本。

**建议**：
1. 约定"单一写入者"：HTTP handler 通过 DownloadManager 写；
2. 订阅任务直接 `DownloadManager.add()` 添加，由 worker 消化；
3. 或改用 SQLite 替代 JSON 队列。

---

### 2.6 与 streaming-server 集成

#### 2.6.1 router.py dispatch 直接调用 dl_enhancer 无错误隔离 🟡

**代码**（`router.py:99-103`）：
```python
if method_name in DL_ENHANCER_MAP:
    import stream_server_v3_dl as dl_enhancer
    dl_func = getattr(dl_enhancer, DL_ENHANCER_MAP[method_name])
    dl_func(handler, params, handler.download_manager)
    return
```

`dispatch` 调用 dl_enhancer 函数时**不包装 try/except**。  
如果 `handle_auto_download` 内意外抛出未捕获的异常，整个 HTTP 连接断开并返回空响应，无 500 错误信息。

**建议**：外层包装统一错误处理。

---

#### 2.6.2 cancel/remove 设计缺陷 🔴

**代码**（`stream_server_v3.py:265-288`）：
```python
proc = self.active_downloads.pop(item_id, None)
if proc:
    proc.kill()  # 期望 _DownloadHandle
```

但实际存入的可能是 `subprocess.Popen`（有 `.kill()`）或 `_DownloadHandle`（有 `.kill()` 但只是 event.set）。  
`_DownloadHandle` 的 `.kill()` 只能设置 event，而 `_do_download` 中 `handle.cancel.is_set()` 才取消，但 **`handle_dl_cloud` 中 CloudDiskDL.download 过程完全阻塞，不检查 cancel，cancel 无效**。

---

#### 2.6.3 内存数据库脏读风险 🟡

**代码**（`handle_auto_download`、`handle_auto_find` 等多个函数）：
```python
db = get_database()   # ← 直接读 JSON 文件
media = next((m for m in db["movies"] if m["id"] == media_id), None)
```

同一时刻 `handle_auto_download`（写数据库）与 `handle_auto_find`（读）可能操作同一 `database.json`。  
MediaDB 有缓存，但未写锁，**增量更新可能丢失**。

---

### 2.7 资源管理

#### 2.7.1 ThreadPoolExecutor 未 shutdown 🟡

**代码**（`stream_server_v3_dl.py:67`）：
```python
_download_executor = ThreadPoolExecutor(max_workers=5)
```

模块导入时创建，服务于整个进程生命周期，无 `atexit` 注册 `shutdown`。  
如果服务重启（如通过 launchd 重启），旧进程的线程池仍在运行，新进程又创建新的——**旧线程池不会被回收**直到进程被 SIGTERM。

建议：
```python
import atexit
atexit.register(_download_executor.shutdown, wait=False)
```

---

#### 2.7.2 worker 线程风暴 🟠

**代码**（`stream_server_v3.py:158`）：
```python
for item in items_to_start:
    threading.Thread(target=self._download, args=(item,), daemon=True).start()
```

每次循环为每个 queued 任务创建独立线程（而非复用线程池），批量添加时瞬间创建 100+ 线程。  
这些线程阻塞在 `_download_semaphore.acquire` 等待信号量，信号量仅 3，意味着数百个线程同时竞争 3 个槽位。

**建议**：使用 executor 提交 `_download`，但需小心信号量竞争（同 2.2.3）。

---

#### 2.7.3 aria2c/test_source_availability 进程泄漏风险 🟡

`test_source_availability`（720-911）在每个 `test_source_availability` 调用中启动 `sp.run(aria2c, timeout=30)`，如果 `aria2c` 不响应 `timeout`（rare），进程可能残留。  
`sp.run` 本身应处理，但应当加 cleanup 逻辑。

---

## 三、建议优先修复（按严重程度排序）

### P0 — 立即修复

| 编号 | 建议 |
|-----|-----|
| 1 | `_set_item` 内 `_save_queue` 移入同一锁块 |
| 2 | 统一下载入口：废弃 DownloadManager._start_worker 的原生线程 pool，统一用 `_download_executor`（max_workers=3） |
| 3 | `run_subscription_check` 改为通过 `DownloadManager.add()` 入队，不应直接读写 `download_queue.json` |
| 4 | 修复 `active_downloads` 类型混用：cancel/remove的人对 subprocess.Popen 和 _DownloadHandle 做统一适配（如基类或 duck typing 包装） |

### P1 — 短期修复（1周内）

| 编号 | 建议 |
|-----|-----|
| 5 | 阿里云盘 `get_share_files` 替换正确 API，fix 分页 break |
| 6 | 百度网盘 `get_download_url` 澄清逻辑，去掉 rapidupload 副作用调用 |
| 7 | `_download_direct` 加 timeout 和 chunked 读取，换掉 `urlretrieve` |
| 8 | 订阅任务 fcntl 锁范围修正：改用 queue 锁 |
| 9 | `handle_auto_download` 等批量测试接口 **不要在 HTTP 线程阻塞 join**；改为异步返回 + SSE/轮询查询结果 |

### P2 — 中期优化

| 编号 | 建议 |
|-----|-----|
| 10 | 云盘保存后等待逻辑：轮询 `search_file` 或 `list_files` 代替固定 sleep(1) |
| 11 | 登录 token 过期检测：每请求级加 401 catch 后自动 refresh 重试 |
| 12 | 数据库写入竞争：统一 `MediaDB._write` + atomic write (tempfile + os.replace) |
| 13 | 密码提取正则修复：`?pwd=` 和 `&pwd=` 两种情况都支持 |
| 14 | 软硬路径解耦：`yt-dlp`/`aria2c` 路径从配置读取 |
| 15 | `handle_auto_download` 和 `handle_auto_find` 的 `run_tests` 使用 Promise/Future 模式异步，避免 `t.join` 阻塞 |
| 16 | 添加 `atexit.register(_download_executor.shutdown)` |
| 17 | 订阅日志文件锁：`subscription_log.json` 的读写也应加 fcntl 锁 |

### P3 — 长期重构

| 编号 | 建议 |
|-----|-----|
| 18 | 用 SQLite 替代 `download_queue.json`（SQLite 自带 WAL 模式，天然支持并发读 + 单写） |
| 19 | 引入任务幂等性（`task_id` 唯一索引，防止重复提交） |
| 20 | 集成 Sentry/structured logging 替代 `print` 散布的调试语句 |
| 21 | 添加 `_semaphore context manager` 封装，防止任何路径遗漏 release |

---

## 四、最危险代码片段示例

### 4.1 竞态写入（JSON 损坏）

```python
# ❌ stream_server_v3_dl.py:180-184
def _set_item(self, item, **kwargs):
    with self.dm._queue_lock:
        item.update(kwargs)
    self.dm._save_queue()    # ← 锁外写文件

# ✅ 修正
def _set_item(self, item, **kwargs):
    with self.dm._queue_lock:
        item.update(kwargs)
        self.dm._save_queue()
```

---

### 4.2 锁误用（订阅写入队列）

```python
# ❌ stream_server_v3_dl.py:1676-1750
queue_file = Path.home() / "services/streaming-server" / "download_queue.json"
with _lock_subs():        # ← 用的是 subscriptions.lock！
    with open(queue_file) as f:
        queue = json.load(f)
    ...
    with open(queue_file, "w") as f:
        json.dump(queue, f, ...)

# ✅ 修正（推荐方式）
item_id = download_manager.add(title, url, ...)
# 让 worker 自动处理，不写文件
```

---

### 4.3 双重下载入口（任务低效执行）

```python
# ❌ stream_server_v3.py:130-158 — 每 3 秒为每个 pending 任务创建新线程
item["status"] = "queued"
...
threading.Thread(target=self._download, args=(item,), daemon=True).start()

# 同时：
# ❌ /api/dl-url → _download_executor.submit(dl.execute_download, item)
# → 两者都可能启动，竞态

# ✅ 修正（统一用 executor）
def _start_worker(self):
    def worker():
        while True:
            with self._queue_lock:
                for item in self.queue:
                    if item["status"] == "pending":
                        item["status"] = "queued"
                        self._executor.submit(self._acquire_and_download, item)
            time.sleep(1)
```

---

## 五、关键漏洞的利用场景

1. **队列文件崩溃**：高频调用 `/api/dl-url` 时，两个线程同时进入 `_set_item`，两者先后释放锁并各自调用 `_save_queue`，后写入者的版本覆盖前者，中间状态（进度更新）丢失，**更严重的是，如果前一个 write 的 file handle 尚未 flush（磁盘 writeback），断电/崩溃可导致半写文件（JSON 截断）**。

2. **订阅任务覆写队列**：autorun 定时触发 `run_subscription_check`，读取 `download_queue.json`，在 CPU 密集的 Bing 搜索+测速（~30 秒）期间，HTTP handler 同时更新队列文件。订阅任务完成后写回文件，**HTTP handler 期间被添加的任务丢失**。

3. **取消下载失效**：用户在磁力链下载 80% 时点"取消"，`cancel()` 只杀了 `_DownloadHandle` event（不行），实际 `Popen` 仍在跑 aria2c，**磁盘持续写入，磁盘满风险**。

4. **token 过期下载失败**：阿里云 token 过期后（周末/长期运行），第一次下载失败只返回 `"error"`，上层标记 failed，用户看到错误但不知道是登录失效，需要手动重登；**没有自动重试+刷新的闭环**。

---

## 六、修复优先级总结

- **高（7天内）**：#1 `_set_item` 锁内 `_save_queue`、#3 双重下载入口统一、#4 run_subscription_check 绕过队列问题
- **中（2周内）**：#5 Aliyun API 修复、#7 urlretrieve 加 timeout、#8 锁误用修复、#10 云盘等待逻辑
- **低（迭代）**：#13 密码正则、#14 路径配置化、#18 切换到 SQLite
