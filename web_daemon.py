import sys, re, time, os, threading, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import deque

state_lock = threading.Lock()
state = {
    "stage": "Запуск сервера...",
    "percent": 0,
    "status": "running",
    "logs": deque(maxlen=400),
    "confirmation": None,
    "error_msg": ""
}

re_build = re.compile(r'\[\s*(\d+)%\s+\d+/\d+\]')
re_sync = re.compile(r'(\d+)%')

HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EvoBuilder Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            /* Material 3 Dark Theme Colors */
            --md-sys-color-background: #141218;
            --md-sys-color-surface: #211F26;
            --md-sys-color-surface-variant: #49454F;
            --md-sys-color-primary: #D0BCFF;
            --md-sys-color-on-primary: #381E72;
            --md-sys-color-primary-container: #4F378B;
            --md-sys-color-on-primary-container: #EADDFF;
            --md-sys-color-error: #FFB4AB;
            --md-sys-color-on-error: #690005;
            --md-sys-color-success: #81C995;
            --md-sys-color-on-success: #00391C;
            --md-sys-color-on-surface: #E6E0E9;
            --md-sys-color-on-surface-variant: #CAC4D0;
        }
        body {
            background-color: var(--md-sys-color-background);
            color: var(--md-sys-color-on-surface);
            font-family: 'Google Sans', sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 24px;
            margin: 0;
            height: 100vh;
            box-sizing: border-box;
        }
        .card {
            background-color: var(--md-sys-color-surface);
            border-radius: 28px;
            padding: 32px;
            width: 100%;
            max-width: 900px;
            height: 90vh;
            display: flex;
            flex-direction: column;
            gap: 24px;
            box-shadow: 0px 8px 24px rgba(0, 0, 0, 0.4);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        h1 {
            margin: 0;
            font-size: 28px;
            font-weight: 500;
            letter-spacing: 0px;
            color: var(--md-sys-color-on-surface);
        }
        .status-chip {
            background-color: var(--md-sys-color-primary-container);
            color: var(--md-sys-color-on-primary-container);
            padding: 8px 16px;
            border-radius: 12px;
            font-weight: 500;
            font-size: 14px;
            transition: all 0.3s ease;
        }
        .status-chip.s-success { background: var(--md-sys-color-success); color: var(--md-sys-color-on-success); }
        .status-chip.s-error { background: var(--md-sys-color-error); color: var(--md-sys-color-on-error); }
        .status-chip.s-waiting { background: #FFD073; color: #3E2D00; animation: pulse 2s infinite; }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.7; }
            100% { opacity: 1; }
        }

        .progress-block {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .progress-header {
            display: flex;
            justify-content: space-between;
            font-size: 14px;
            font-weight: 500;
            color: var(--md-sys-color-on-surface-variant);
        }
        .progress-track {
            background-color: var(--md-sys-color-surface-variant);
            height: 12px;
            border-radius: 999px;
            overflow: hidden;
        }
        .progress-fill {
            background-color: var(--md-sys-color-primary);
            height: 100%;
            width: 0%;
            border-radius: 999px;
            transition: width 0.6s cubic-bezier(0.2, 0, 0, 1);
        }

        .actions {
            display: none;
            flex-direction: row;
            gap: 12px;
            justify-content: flex-end;
            background: var(--md-sys-color-background);
            padding: 16px;
            border-radius: 20px;
        }
        .btn {
            border: none;
            border-radius: 999px;
            padding: 12px 24px;
            font-size: 14px;
            font-weight: 500;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.2, 0, 0, 1);
        }
        .btn-primary {
            background-color: var(--md-sys-color-primary);
            color: var(--md-sys-color-on-primary);
        }
        .btn-primary:hover { opacity: 0.85; transform: scale(1.02); }
        
        .console-container {
            background-color: #0F0D13;
            border-radius: 20px;
            padding: 20px;
            flex-grow: 1;
            overflow-y: auto;
            font-family: 'JetBrains Mono', 'Roboto Mono', monospace;
            font-size: 13px;
            color: #A8C7FA;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-all;
            box-shadow: inset 0px 4px 8px rgba(0,0,0,0.3);
        }
        .console-container::-webkit-scrollbar { width: 8px; }
        .console-container::-webkit-scrollbar-track { background: transparent; }
        .console-container::-webkit-scrollbar-thumb { background: var(--md-sys-color-surface-variant); border-radius: 10px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1 id="stage">Сборка системы...</h1>
            <div id="status" class="status-chip s-running">В процессе</div>
        </div>
        
        <div class="progress-block">
            <div class="progress-header">
                <span>Прогресс</span>
                <span id="pct" style="color: var(--md-sys-color-primary);">0%</span>
            </div>
            <div class="progress-track">
                <div class="progress-fill" id="bar"></div>
            </div>
        </div>

        <div id="actions" class="actions">
            <span style="align-self: center; color: var(--md-sys-color-on-surface-variant); font-weight: 500;">Подготовка завершена. Начать компиляцию?</span>
            <button class="btn btn-primary" onclick="sendAnswer('yes')">Запустить (m evolution)</button>
        </div>

        <div class="console-container" id="logs">Подключение к логам сервера...</div>
    </div>

    <script>
        const logsEl = document.getElementById('logs');
        let autoScroll = true;
        
        logsEl.addEventListener('scroll', () => { 
            autoScroll = (logsEl.scrollHeight - logsEl.scrollTop <= logsEl.clientHeight + 50); 
        });

        async function fetchData() {
            try {
                const r = await fetch('/api/data'); 
                const d = await r.json();
                
                document.getElementById('stage').innerText = d.stage;
                document.getElementById('bar').style.width = d.percent + '%';
                document.getElementById('pct').innerText = d.percent + '%';
                
                logsEl.innerText = d.logs.join('\\n');
                if(autoScroll) logsEl.scrollTop = logsEl.scrollHeight;

                let stEl = document.getElementById('status');
                stEl.className = 'status-chip s-' + d.status;
                
                const statusMap = {
                    'running': 'В процессе',
                    'success': 'Готово!',
                    'error': 'Ошибка сбоки',
                    'waiting': 'Ожидание действий'
                };
                stEl.innerText = statusMap[d.status] || d.status;
                
                if (d.status === 'error') { 
                    logsEl.innerText += '\\n\\n🚨 КРИТИЧЕСКАЯ ОШИБКА: ' + d.error_msg; 
                }
                
                document.getElementById('actions').style.display = (d.status === 'waiting') ? 'flex' : 'none';
            } catch(e) {}
        }

        function sendAnswer(ans) { 
            fetch('/api/answer', {method: 'POST', body: ans}); 
            document.getElementById('actions').style.display = 'none';
        }
        
        setInterval(fetchData, 1000); 
        fetchData();
    </script>
</body>
</html>
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
            chunk = f.read(1024)
            if not chunk:
                time.sleep(0.1)
                continue
            
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
print("Material UI запущен на порту 8080...")
HTTPServer(('0.0.0.0', 8080), WebHandler).serve_forever()
