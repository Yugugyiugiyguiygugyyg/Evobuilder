import sys, re, time, os, threading, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

state_lock = threading.Lock()
state = {
    "stage": "Запуск...",
    "percent": 0,
    "status": "running",
    "logs": deque(maxlen=300), # Увеличил количество строк лога
    "confirmation": None,
    "error_msg": ""
}

# Регулярки для вытаскивания процентов из Android сборки
re_build = re.compile(r'\[\s*(\d+)%\s+\d+/\d+\]')
re_sync = re.compile(r'(\d+)%')

HTML_PAGE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ROM Dashboard</title>
<style>
    body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; margin: 0; padding: 20px; }
    .card { background: #1e293b; padding: 25px; border-radius: 12px; width: 100%; max-width: 900px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .header { display: flex; justify-content: space-between; align-items: center; }
    h1 { color: #38bdf8; margin: 0; }
    .bar-bg { background: #334155; height: 25px; border-radius: 8px; margin: 20px 0; overflow: hidden; position: relative; }
    .bar-fill { background: linear-gradient(90deg, #3b82f6, #06b6d4); height: 100%; width: 0%; transition: width 0.3s; }
    .bar-text { position: absolute; width: 100%; text-align: center; top: 3px; font-weight: bold; text-shadow: 1px 1px 2px #000; }
    .logs { background: #020617; color: #10b981; font-family: monospace; font-size: 12px; height: 500px; overflow-y: auto; padding: 15px; border-radius: 8px; white-space: pre-wrap; word-break: break-all; }
    .confirm-box { display: none; background: #334155; padding: 20px; border-radius: 8px; text-align: center; margin-bottom: 20px; border: 2px solid #fbbf24; }
    button { padding: 10px 25px; font-size: 16px; border: none; border-radius: 6px; cursor: pointer; margin: 0 10px; font-weight: bold; }
    .btn-yes { background: #10b981; color: white; } .btn-no { background: #ef4444; color: white; }
    .badge { padding: 5px 10px; border-radius: 20px; font-weight: bold; font-size: 14px; }
    .s-running { background: #3b82f6; } .s-waiting { background: #fbbf24; color: black; } .s-success { background: #10b981; } .s-error { background: #ef4444; }
</style></head><body>
<div class="card">
    <div class="header">
        <h1 id="stage">Запуск...</h1>
        <div id="status" class="badge s-running">Подключение...</div>
    </div>
    <div class="bar-bg"><div class="bar-fill" id="bar"></div><div class="bar-text" id="pct">0%</div></div>
    
    <div id="confirm-box" class="confirm-box">
        <h2 style="margin-top:0; color: #fbbf24;">❓ Подготовка завершена</h2>
        <p>Начать компиляцию (m evolution)?</p>
        <button class="btn-yes" onclick="sendAnswer('yes')">ДА, НАЧАТЬ</button>
        <button class="btn-no" onclick="sendAnswer('no')">НЕТ, ОТМЕНА</button>
    </div>

    <div class="logs" id="logs"></div>
</div>
<script>
    const logsEl = document.getElementById('logs');
    let autoScroll = true;
    logsEl.addEventListener('scroll', () => { autoScroll = (logsEl.scrollHeight - logsEl.scrollTop <= logsEl.clientHeight + 50); });

    async function fetchData() {
        try {
            const r = await fetch('/api/data'); const d = await r.json();
            document.getElementById('stage').innerText = d.stage;
            document.getElementById('bar').style.width = d.percent + '%';
            document.getElementById('pct').innerText = d.percent + '%';
            logsEl.innerText = d.logs.join('\\n');
            if(autoScroll) logsEl.scrollTop = logsEl.scrollHeight;

            let stEl = document.getElementById('status');
            stEl.className = 'badge s-' + d.status;
            stEl.innerText = d.status.toUpperCase();
            
            if (d.status === 'error') { logsEl.innerText += '\\n\\n🚨 ОШИБКА: ' + d.error_msg; }
            document.getElementById('confirm-box').style.display = (d.status === 'waiting') ? 'block' : 'none';
        } catch(e) {}
    }
    function sendAnswer(ans) { fetch('/api/answer', {method: 'POST', body: ans}); }
    setInterval(fetchData, 1000); fetchData();
</script></body></html>
"""

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/api/data':
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                with state_lock: self.wfile.write(json.dumps({"stage": state["stage"], "percent": state["percent"], "status": state["status"], "error_msg": state["error_msg"], "logs": list(state["logs"])}).encode())
            elif self.path == '/api/wait_confirm':
                with state_lock: state["status"] = "waiting"
                while True:
                    with state_lock: ans = state["confirmation"]
                    if ans: break
                    time.sleep(1)
                self.send_response(200); self.end_headers()
                self.wfile.write(ans.encode())
            else:
                self.send_response(200); self.send_header('Content-type', 'text/html; charset=utf-8'); self.end_headers()
                self.wfile.write(HTML_PAGE.encode('utf-8'))
        except: pass

    def do_POST(self):
        try:
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len).decode('utf-8') if content_len else ""
            self.send_response(200); self.end_headers()
            with state_lock:
                if self.path == '/api/stage': state["stage"] = body
                elif self.path == '/api/answer': state["confirmation"] = body; state["status"] = "running"
                elif self.path == '/api/success': state["status"] = "success"; state["percent"] = 100
                elif self.path == '/api/error': state["status"] = "error"; state["error_msg"] = body
        except: pass
    def log_message(self, format, *args): pass

def tail_worker():
    log_path = os.path.expanduser('~/build_full.log')
    open(log_path, 'a').close()
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        f.seek(0, 2)
        while True:
            # Читаем посимвольно, чтобы не пропускать апдейты repo sync (\r)
            chunk = f.read(1024)
            if not chunk:
                time.sleep(0.1)
                continue
            
            # Разбиваем и по \n, и по \r
            lines = re.split(r'[\r\n]+', chunk)
            for line in lines:
                clean = line.strip()
                if not clean: continue
                
                with state_lock:
                    state["logs"].append(clean)
                    if "FAILED:" in line or "ninja: build stopped" in line:
                        state["status"] = "error"
                        state["error_msg"] = clean
                    
                    if match := re_build.search(line): state["percent"] = int(match.group(1))
                    elif match := re_sync.search(line): state["percent"] = int(match.group(1))

threading.Thread(target=tail_worker, daemon=True).start()
print("Web-сервер запущен на порту 8080...")
HTTPServer(('0.0.0.0', 8080), WebHandler).serve_forever()
