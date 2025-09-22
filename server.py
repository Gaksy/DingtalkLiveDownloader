# listen_dingtalk.py
import asyncio
import json
from playwright.async_api import async_playwright
import os
import aiohttp
import re
from urllib.parse import urlparse, parse_qs
import logging

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LIVE_SHARE_URL = "https://n.dingtalk.com/dingding/live-room/index.html?roomId=LHnQZUXktD&liveUuid=62d3f452-504d-44f0-855f-f86aa1fb3932"

# 修改INJECT_JS，增加截断长度到10000
INJECT_JS = r"""
(() => {
  if (window.__PY_MONITOR_INSTALLED__) return;
  window.__PY_MONITOR_INSTALLED__ = true;

  function sendToPy(payload) {
    try {
      if (window.pyReceive) {
        window.pyReceive(JSON.stringify(payload));
      } else if (window.__playwright_py_receive__) {
        window.__playwright_py_receive__(JSON.stringify(payload));
      }
    } catch (e) {
      console.warn("pyReceive error", e);
    }
  }

  (function() {
    const OrigXhr = window.XMLHttpRequest;
    function WrappedXhr() {
      const xhr = new OrigXhr();
      let _method = null, _url = null, _startTs = null;
      const origOpen = xhr.open;
      const origSend = xhr.send;
      xhr.open = function(method, url, ...rest) {
        _method = method;
        _url = url;
        return origOpen.call(this, method, url, ...rest);
      };
      xhr.send = function(body) {
        _startTs = Date.now();
        try {
          sendToPy({
            kind: "xhr_request",
            method: _method,
            url: _url,
            body: (typeof body === "string" ? body : (body ? "[non-string body]" : null)),
            ts: _startTs
          });
        } catch (e) {}
        this.addEventListener('loadend', () => {
          try {
            sendToPy({
              kind: "xhr_response",
              method: _method,
              url: _url,
              status: this.status,
              responseTextPreview: (typeof this.responseText === "string") ? this.responseText.slice(0, 10000) : null,
              duration_ms: Date.now() - _startTs
            });
          } catch (e) {}
        });
        return origSend.call(this, body);
      };
      return xhr;
    }
    WrappedXhr.prototype = OrigXhr.prototype;
    window.XMLHttpRequest = WrappedXhr;
  })();

  (function() {
    const origFetch = window.fetch;
    window.fetch = function(input, init) {
      const url = (typeof input === "string") ? input : (input && input.url) || "";
      const method = (init && init.method) || (input && input.method) || "GET";
      const start = Date.now();
      let bodyPreview = null;
      try {
        if (init && init.body && typeof init.body === "string") bodyPreview = init.body.slice(0, 10000);
      } catch (e) {}
      try {
        sendToPy({ kind: "fetch_request", method, url, bodyPreview, ts: start });
      } catch (e) {}
      return origFetch(input, init).then((resp) => {
        try {
          const clone = resp.clone();
          return clone.text().then(text => {
            try {
              sendToPy({
                kind: "fetch_response",
                method,
                url,
                status: resp.status,
                textPreview: (typeof text === "string") ? text.slice(0, 10000) : null,
                duration_ms: Date.now() - start
              });
            } catch (e) {}
            return resp;
          }).catch(_ => resp);
        } catch (e) {
          return resp;
        }
      });
    };
  })();

  (function() {
    const OrigWS = window.WebSocket;
    function WrappedWebSocket(url, protocols) {
      const ws = protocols ? new OrigWS(url, protocols) : new OrigWS(url);
      const createdAt = Date.now();
      try { sendToPy({ kind: "ws_created", url: String(url), protocols: protocols || null, ts: createdAt }); } catch (e) {}

      const origSend = ws.send;
      ws.send = function(data) {
        try { sendToPy({ kind: "ws_send", url: String(url), dataPreview: (typeof data === "string" ? data.slice(0,10000) : "[non-string]"), ts: Date.now() }); } catch (e) {}
        return origSend.call(this, data);
      };

      ws.addEventListener('message', function(ev) {
        try { 
          const dataStr = typeof ev.data === "string" ? ev.data : "[non-string]";
          sendToPy({ 
            kind: "ws_message", 
            url: String(url), 
            dataPreview: dataStr.slice(0, 10000),
            dataLength: dataStr.length,
            direction: "recv", 
            ts: Date.now() 
          }); 
        } catch (e) {}
      });

      ws.addEventListener('close', function(ev) {
        try { sendToPy({ kind: "ws_close", url: String(url), code: ev.code, reason: ev.reason, ts: Date.now() }); } catch(e) {}
      });
      ws.addEventListener('error', function(ev) {
        try { sendToPy({ kind: "ws_error", url: String(url), ts: Date.now() }); } catch(e) {}
      });

      return ws;
    }
    WrappedWebSocket.prototype = OrigWS.prototype;
    Object.defineProperty(WrappedWebSocket, "CONNECTING", { value: OrigWS.CONNECTING });
    Object.defineProperty(WrappedWebSocket, "OPEN", { value: OrigWS.OPEN });
    Object.defineProperty(WrappedWebSocket, "CLOSING", { value: OrigWS.CLOSING });
    Object.defineProperty(WrappedWebSocket, "CLOSED", { value: OrigWS.CLOSED });
    window.WebSocket = WrappedWebSocket;
  })();

  try {
    setInterval(() => { try { sendToPy({ kind: "ping", ts: Date.now() }); } catch(e) {} }, 30_000);
  } catch (e) {}
})();
"""


class M3U8Downloader:
    def __init__(self):
        self.downloaded_urls = set()
        self.download_dir = "downloaded_m3u8"
        os.makedirs(self.download_dir, exist_ok=True)
        logger.info(f"M3U8下载目录: {os.path.abspath(self.download_dir)}")

    def should_download(self, url):
        """检查是否应该下载这个URL"""
        if url in self.downloaded_urls:
            logger.debug(f"URL已下载过: {url}")
            return False

        # 放宽条件：只要包含m3u8就下载
        if '.m3u8' in url:
            logger.info(f"检测到m3u8 URL: {url}")
            return True

        # 检查其他可能的流媒体格式
        if any(ext in url for ext in ['.ts', '.m4s', '.mp4', '.flv']):
            logger.debug(f"检测到媒体文件: {url}")
            return True

        return False

    async def download_m3u8(self, url):
        """异步下载m3u8文件"""
        if not self.should_download(url):
            return

        self.downloaded_urls.add(url)
        logger.info(f"开始下载: {url}")

        try:
            # 从URL中提取文件名
            parsed_url = urlparse(url)
            filename = os.path.basename(parsed_url.path)
            if not filename or '.' not in filename:
                # 如果没有扩展名，添加m3u8扩展名
                filename = f"stream_{len(self.downloaded_urls)}.m3u8"
            elif not filename.endswith('.m3u8'):
                # 如果已有扩展名但不是m3u8，保留原扩展名
                pass

            filepath = os.path.join(self.download_dir, filename)

            # 设置请求头，模拟浏览器
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': 'https://n.dingtalk.com/',
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        with open(filepath, "wb") as f:
                            f.write(content)
                        logger.info(f"[SAVED] {filename} ({len(content)} bytes)")

                        # 尝试解析m3u8内容
                        try:
                            content_text = content.decode('utf-8')
                            if '#EXTM3U' in content_text:
                                logger.info(f"[M3U8] 成功下载有效的m3u8文件: {filename}")
                                # 提取所有ts文件链接
                                ts_links = re.findall(r'^[^#].*\.ts(?:\?.*)?$', content_text, re.MULTILINE)
                                if ts_links:
                                    logger.info(f"[M3U8] 包含 {len(ts_links)} 个TS片段")
                                    # 记录前几个TS链接
                                    for i, ts_link in enumerate(ts_links[:3]):
                                        logger.info(f"[TS{i + 1}] {ts_link}")
                        except UnicodeDecodeError:
                            logger.warning(f"[M3U8] 文件 {filename} 内容无法解码为UTF-8")
                        except Exception as e:
                            logger.error(f"[M3U8] 解析错误: {e}")
                    else:
                        logger.warning(f"[DOWNLOAD FAILED] HTTP {resp.status} for {url}")
        except asyncio.TimeoutError:
            logger.error(f"[DOWNLOAD TIMEOUT] {url}")
        except Exception as e:
            logger.error(f"[DOWNLOAD ERROR] {e} for {url}")


async def open_and_listen(live_share_url=LIVE_SHARE_URL, headful=True, user_data_dir=None):
    downloader = M3U8Downloader()

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp("http://localhost:9222")
            logger.info("已连接到正在运行的 Edge 浏览器实例")
        except Exception as e:
            logger.warning(f"无法连接到正在运行的 Edge 浏览器: {e}")
            logger.info("将启动新的 Edge 浏览器实例...")
            edge_paths = [
                "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
                "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/usr/bin/microsoft-edge",
                "/usr/bin/microsoft-edge-stable"
            ]
            edge_executable_path = None
            for path in edge_paths:
                if os.path.exists(path):
                    edge_executable_path = path
                    break
            if edge_executable_path:
                logger.info(f"使用 Edge 浏览器路径: {edge_executable_path}")
                browser = await pw.chromium.launch(
                    executable_path=edge_executable_path,
                    headless=not headful,
                    args=[
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--remote-debugging-port=9222",
                        "--disable-web-security",
                        "--allow-running-insecure-content"
                    ]
                )
            else:
                logger.info("未找到 Edge 浏览器，使用 Chromium")
                browser = await pw.chromium.launch(
                    headless=not headful,
                    args=[
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--remote-debugging-port=9222",
                        "--disable-web-security",
                        "--allow-running-insecure-content"
                    ]
                )

        context_args = {}
        if user_data_dir:
            context_args["user_data_dir"] = user_data_dir
            logger.info(f"使用用户数据目录: {user_data_dir}")

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        async def handle_from_page(source, payload_json_str):
            try:
                data = json.loads(payload_json_str)
            except Exception:
                data = {"raw": payload_json_str}

            try:
                kind = data.get("kind")
                url = data.get("url") or ""

                # 处理WebSocket消息
                if kind and "ws_message" in kind:
                    data_length = data.get("dataLength", 0)
                    data_preview = data.get("dataPreview", "")

                    if data_length > 0:
                        logger.debug(f"[WS] {kind} | {url[:80]} | 长度: {data_length}")

                        # 提取并下载m3u8文件
                        m3u8_patterns = [
                            r'(https?://[^\s<>"\'{}|\\^`]+?\.m3u8[^\s<>"\'{}|\\^`]*)',
                            r'(https?://[^\s<>"\'{}|\\^`]*?/live/[^\s<>"\'{}|\\^`]*\.m3u8[^\s<>"\'{}|\\^`]*)',
                            r'(http?://[^\s<>"\'{}|\\^`]+?\.m3u8[^\s<>"\'{}|\\^`]*)',
                            r'(http?://[^\s<>"\'{}|\\^`]*?/live/[^\s<>"\'{}|\\^`]*\.m3u8[^\s<>"\'{}|\\^`]*)'
                        ]

                        for pattern in m3u8_patterns:
                            matches = re.findall(pattern, data_preview, re.IGNORECASE)
                            for m3u8_url in matches:
                                logger.info(f"[M3U8 FOUND IN WS] {m3u8_url}")
                                # 立即下载，不等待
                                asyncio.create_task(downloader.download_m3u8(m3u8_url))

                # 处理其他类型的消息
                elif kind and ("xhr_response" in kind or "fetch_response" in kind):
                    url = data.get("url", "")
                    if '.m3u8' in url:
                        logger.info(f"[M3U8 RESPONSE] {kind} | {url}")
                        asyncio.create_task(downloader.download_m3u8(url))

            except Exception as e:
                logger.error(f"handle_from_page error: {e}")

        await page.expose_binding("pyReceive", handle_from_page)

        # 监听网络响应
        async def on_response(resp):
            try:
                url = resp.url
                if '.m3u8' in url:
                    logger.info(f"[M3U8 NET RESPONSE] {resp.status} {url}")
                    asyncio.create_task(downloader.download_m3u8(url))
                elif any(ext in url for ext in ['.ts', '.m4s', '.mp4']):
                    logger.debug(f"[MEDIA RESPONSE] {resp.status} {url}")
            except Exception as e:
                logger.error(f"[RESPONSE ERROR] {e}")

        page.on("response", lambda resp: asyncio.create_task(on_response(resp)))

        logger.info(f"Opening page: {live_share_url}")
        await page.goto(live_share_url, wait_until="networkidle")
        await page.add_init_script(INJECT_JS)
        await page.evaluate("() => { /* injected monitor active */ }")
        logger.info("注入完成。浏览器窗口已打开。")

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(open_and_listen(
            LIVE_SHARE_URL,
            headful=True,
            # user_data_dir="./edge_profile"  # 使用持久化用户数据
        ))
    except KeyboardInterrupt:
        logger.info("已停止监听。")