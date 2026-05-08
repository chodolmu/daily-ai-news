"""
claude-mem 합성본 전용 뷰어 (neomem 스타일).

claude-mem의 /api/summaries만 가져와서 카드 형식으로 보여준다.
raw prompts/observations는 안 보여준다 — 진짜 누적된 메모리만.

실행: python scripts/memory_viewer.py
브라우저: http://localhost:37778
"""
from __future__ import annotations

import http.server
import json
import socketserver
import urllib.request
from urllib.error import URLError

PORT = 37778
CLAUDE_MEM_API = "http://127.0.0.1:37777/api/summaries"


PAGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Memory Viewer — synthesized only</title>
<style>
  :root {
    --bg: #1a1916;
    --bg-card: #252320;
    --bg-card-hover: #2d2a26;
    --border: #3a3530;
    --text: #e8e4dd;
    --text-dim: #8f8a7e;
    --text-label: #d4a72c;
    --accent: #6dabf2;
    --accent-2: #8cd0a0;
    --accent-3: #c9a8ec;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 32px;
    max-width: 1100px;
    margin: 0 auto;
    line-height: 1.6;
  }
  h1 {
    font-size: 24px;
    margin-bottom: 4px;
  }
  .subtitle {
    color: var(--text-dim);
    font-size: 14px;
    margin-bottom: 24px;
  }
  .controls {
    margin-bottom: 24px;
    display: flex;
    gap: 12px;
    align-items: center;
  }
  button {
    background: var(--bg-card);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
  }
  button:hover { background: var(--bg-card-hover); }
  input[type="text"] {
    background: var(--bg-card);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    flex: 1;
  }
  .stats {
    color: var(--text-dim);
    font-size: 13px;
    margin-left: auto;
  }
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 16px;
    transition: border-color 0.15s;
  }
  .card:hover { border-color: var(--accent); }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 12px;
    flex-wrap: wrap;
    gap: 8px;
  }
  .card-id {
    color: var(--text-dim);
    font-size: 12px;
    font-family: 'Monaco', 'Consolas', monospace;
  }
  .card-date {
    color: var(--text-dim);
    font-size: 12px;
  }
  .request {
    color: var(--accent);
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
  }
  .field {
    margin: 10px 0;
    display: flex;
    gap: 12px;
  }
  .field-label {
    color: var(--text-label);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    flex-shrink: 0;
    width: 110px;
    font-weight: 600;
    padding-top: 2px;
  }
  .field-value {
    flex: 1;
    color: var(--text);
    font-size: 14px;
  }
  .field-value.dim { color: var(--text-dim); font-style: italic; }
  .empty {
    text-align: center;
    color: var(--text-dim);
    padding: 60px 20px;
  }
  .error {
    background: #3a1f1f;
    border: 1px solid #6a3a3a;
    color: #f0c0c0;
    padding: 16px;
    border-radius: 8px;
  }
</style>
</head>
<body>
  <h1>Memory Viewer</h1>
  <div class="subtitle">claude-mem 합성본만 — raw 데이터 제외</div>

  <div class="controls">
    <button onclick="reload()">🔄 새로고침</button>
    <input type="text" id="search" placeholder="검색 (request/learned/notes 필드 매칭)" oninput="filter()">
    <span class="stats" id="stats"></span>
  </div>

  <div id="content"></div>

<script>
let allItems = [];

async function reload() {
  const content = document.getElementById('content');
  const stats = document.getElementById('stats');
  content.innerHTML = '<div class="empty">불러오는 중...</div>';
  try {
    const res = await fetch('/data');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    allItems = data.items || [];
    stats.textContent = `${allItems.length}개 합성 메모리`;
    render(allItems);
  } catch (e) {
    content.innerHTML = `<div class="error">claude-mem (port 37777) 연결 실패: ${e.message}</div>`;
  }
}

function render(items) {
  const content = document.getElementById('content');
  if (!items.length) {
    content.innerHTML = '<div class="empty">아직 합성된 메모리 없음. 대화 한 번 더 해봐.</div>';
    return;
  }
  content.innerHTML = items.map(s => {
    const date = new Date(s.created_at).toLocaleString('ko-KR');
    return `
      <div class="card">
        <div class="card-header">
          <span class="card-id">#${s.id} · ${s.project || '-'}</span>
          <span class="card-date">${date}</span>
        </div>
        <div class="request">${escape(s.request || '(no request)')}</div>
        ${field('Learned', s.learned)}
        ${field('Investigated', s.investigated)}
        ${field('Completed', s.completed)}
        ${field('Next Steps', s.next_steps)}
        ${field('Notes', s.notes)}
        ${s.files_read || s.files_edited ? `
          ${field('Read', s.files_read)}
          ${field('Edited', s.files_edited)}
        ` : ''}
      </div>
    `;
  }).join('');
}

function field(label, value) {
  if (!value || value === 'None' || value === 'null') return '';
  return `
    <div class="field">
      <div class="field-label">${label}</div>
      <div class="field-value">${escape(value)}</div>
    </div>
  `;
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function filter() {
  const q = document.getElementById('search').value.toLowerCase();
  if (!q) { render(allItems); return; }
  const filtered = allItems.filter(s => {
    return [s.request, s.learned, s.investigated, s.completed, s.next_steps, s.notes]
      .some(v => v && v.toLowerCase().includes(q));
  });
  document.getElementById('stats').textContent = `${filtered.length}/${allItems.length}개 매칭`;
  render(filtered);
}

reload();
setInterval(reload, 30000);  // 30초마다 자동 새로고침
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 콘솔 로그 silence

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(PAGE_HTML)
        elif self.path == "/data":
            try:
                with urllib.request.urlopen(CLAUDE_MEM_API, timeout=5) as r:
                    body = r.read()
                self._send_json(body)
            except URLError as e:
                self._send_json(json.dumps({"error": str(e), "items": []}).encode())
        else:
            self.send_error(404)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Memory Viewer (synthesized only) → http://localhost:{PORT}")
        print(f"Source: {CLAUDE_MEM_API}")
        print("Ctrl+C to stop.")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
