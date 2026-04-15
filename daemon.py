import sys, re, time, requests, os, threading, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

try:
    with open(os.path.expanduser('~/.rom_build_config'), 'r') as f:
        config = f.read()
        TOKEN = re.search(r'TG_TOKEN="([^"]+)"', config).group(1)
        CHAT_ID = re.search(r'TG_CHAT_ID="([^"]+)"', config).group(1)
except Exception:
    print("Ошибка чтения ~/.rom_build_config")
    sys.exit(1)

state_lock = threading.Lock()
state = {
    "stage": "Инициализация...",
    "percent": 0,
    "status": "running", 
    "logs": deque(maxlen=200),
    "confirmation": None,
    "error_msg": ""
}

re_build = re.compile(r'\[\s*(\d+)%\s+\d+/\d+\]')
re_sync = re.compile(r'Syncing:\s*(\d+)%')

def tg_sender_worker():
    msg_id = None
    last_pct = -1
    last_stage = ""
    last_status = ""
    
    def req(method, **kwargs):
        try: return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=kwargs, timeout=5).json()
        except: return {}

    while True:
        with state_lock:
            st_stage, st_pct, st_status, st_err = state["stage"], state["percent"], state["status"], state["error_msg"]

        if not msg_id:
            res = req("sendMessage", chat_id=CHAT_ID, text=f"🚀 Запуск процесса...\n🌐 Web UI: http://{requests.get('https://ifconfig.me', timeout=5).text}:8080", parse_mode="HTML")
            msg_id = res.get("result", {}).get("message_id")
            time.sleep(2)
            continue

        if st_status == "waiting" and last_status != "waiting":
            req("sendMessage", chat_id=CHAT_ID, text="❓ <b>Подготовка завершена!</b>\nНачинать компиляцию (m evolution)? Напиши '<b>да</b>' или '<b>нет</b>'.", parse_mode="HTML")
            last_status = st_status
        elif st_status == "error" and last_status != "error":
            req("sendMessage", chat_id=CHAT_ID, text=f"🚨 <b>ОШИБКА:</b> {st_stage}\n<code>{st_err}</code>\nПроверь Web UI для подробностей.", parse_mode="HTML")
            break
        elif st_status in ["success", "cancelled"]:
            req("editMessageText", chat_id=CHAT_ID, message_id=msg_id, text=f"✅ Процесс '{st_stage}' завершен со статусом: {st_status.upper()}", parse_mode="HTML")
            break
        
        if (st_pct != last_pct or st_stage != last_stage) and st_status == "running":
            bar = "█" * int(st_pct/10) + "░" * (10 - int(st_pct/10))
            req("editMessageText", chat_id=CHAT_ID, message_id=msg_id, text=f"⚙️ <b>{st_stage}</b>\nПрогресс: {st_pct}%\n<code>[{bar}]</code>", parse_mode="HTML")
            last_pct, last_stage = st_pct, st_stage

        time.sleep(15)

def tg_listener_worker():
    last_update_id = 0
    try:
        res = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=5).json()
        if res.get('result'): last_update_id = res['result'][-1]['update_id']
    except: pass

    while True:
        with state_lock: current_status = state["status"]
        if current_status in ["success", "error", "cancelled"]: break
        
        if current_status == "waiting":
            try:
                res = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_update_id+1}&timeout=10", timeout=15).json()
                for item in res.get('result', []):
                    last_update_id = item['update_id']
                    text = item.get('message', {}).get('text', '').strip().lower()
                    if text in ['yes', 'y', 'да', 'начинай', 'start']:
                        with state_lock: state["confirmation"] = "yes"
                        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": "✅ Принято: Начинаю сборку!"})
                    elif text in ['no', 'n', 'нет', 'stop', 'отмена']:
                        with state_lock: state["confirmation"] = "no"
                        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": "🛑 Принято: Сборка отменена."})
            except: pass
        time.sleep(3)

HTML_PAGE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ROM Builder Dashboard</title>
<style>
    body { background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; justify-content: center; margin: 0; padding: 20px; }
    .card { background: #1e293b; padding: 25px; border-radius: 12px; width: 100%; max-width: 900px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
    .header { display: flex; justify-content: space-between; align-items: center; }
    h1 { color: #38bdf8; margin: 0; }
    .bar-bg { background: #334155; height: 25px; border-radius: 8px; margin: 20px 0; overflow: hidden; position: relative; }
    .bar-fill { background: linear-gradient(90deg, #3b82f6, #06b6d4); height: 100%; width: 0%; transition: width 0.3s; }
    .bar-text { position: absolute; width: 100%; text-align: center; top: 3px; font-weight: bold; text-shadow: 1px 1px 2px #000; }
    .logs { background: #020617; color: #10b981; font-family: monospace; font-size: 13px; height: 400px; overflow-y: auto; padding: 15px; border-radius: 8px; white-space: pre-wrap; word-break: break-all; }
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
                with state_lock: self.wfile.write(json.dumps({"stage": state["stage"], "percent": state["percent"], "status": state["status"], "logs": list(state["logs"])}).encode())
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
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
            
            clean = line.strip()
            if not clean: continue
            
            with state_lock:
                state["logs"].append(clean)
                if "FAILED:" in line or "ninja: build stopped" in line:
                    state["status"] = "error"
                    state["error_msg"] = clean
                
                if match := re_build.search(line): state["percent"] = int(match.group(1))
                elif match := re_sync.search(line): state["percent"] = int(match.group(1))

threading.Thread(target=tg_sender_worker, daemon=True).start()
threading.Thread(target=tg_listener_worker, daemon=True).start()
threading.Thread(target=tail_worker, daemon=True).start()

HTTPServer(('0.0.0.0', 8080), WebHandler).serve_forever()
