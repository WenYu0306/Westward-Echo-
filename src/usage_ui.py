"""Usage analytics page — error tracking, editor behavior, and translation health.

Served at ``GET /usage`` (no auth).  Auto-refreshes every 30 seconds.

【中文·这个文件是干什么的】
这是「用量分析」页面，用来追踪翻译过程中的报错情况和人工编辑行为。
你在浏览器打开 http://localhost:8000/usage 就能看到。
和看板一样不设身份校验，方便随时查看。

【中文·页面结构导览】
整个页面由一个 Python 字符串常量 USAGE_PAGE 和一个数据函数组成：
  · USAGE_PAGE：完整的 HTML/CSS/JS 网页代码，存成 Python 字符串。
    页面大致分五块：
    ① 顶部导航栏 —— 链接到 Dashboard、Usage、Home 三个页面
    ② 错误汇总卡片 —— 最近 7 天的总错误数、各类错误数量
    ③ 错误类型柱状图 —— 用纯 CSS 画的横向条形图，展示前 5 种最多的错误
    ④ 最近问题表格 —— 最近 20 条错误事件的时间、类型、详情、关联任务
    ⑤ 编辑行为卡片 —— 已编辑章节数、术语确认/拒绝数、批量替换次数
    ⑥ 翻译健康表格 —— 每个任务的章节数和警告率
  · get_usage_data()：真正的 Python 函数，从错误追踪器读取所有统计数据。
    页面每 30 秒自动调一次 /api/usage/events 来获取最新数据并刷新。

【中文·这个页面解决什么问题】
翻译几千章的过程中，某些类型的错误可能反复出现（比如 JSON 解析失败、
熔断器触发、AI 输出为空）。这个页面帮你发现"哪类问题最多""哪个任务最不健康"，
从而有针对性地排查和改进。
"""

# 【中文·下面这些 import 是 get_usage_data() 要用到的数据来源】
# error_tracker 模块负责记录和查询翻译过程中的各类事件和错误。
from .error_tracker import (
    get_all_jobs_health,   # 每个任务的翻译健康度（章节数、警告率）
    get_editor_stats,      # 人工编辑行为统计（编辑章节数、术语确认/拒绝数）
    get_event_summary,     # 最近 N 天的错误类型汇总
    get_recent_issues,     # 最近的错误事件列表
)

# 【中文】下面是完整的用量分析网页 HTML/CSS/JS 代码，存成 Python 字符串。
# ⚠️ 字符串内部的内容会被浏览器当作网页渲染，不能加中文注释。
# 页面每 30 秒自动向 /api/usage/events 发请求刷新数据。
USAGE_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Usage Analytics — Westward Echo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400;14..32,500;14..32,600;14..32,700&family=Noto+Serif+SC:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;-webkit-font-smoothing:antialiased}
body{
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:13px;line-height:1.5;
  color:#e5e5ea;background:#0d0d0d;
  min-height:100vh;
}
::selection{background:rgba(0,122,255,.3)}

/* ── HEADER ── */
.header{
  display:flex;align-items:center;justify-content:space-between;
  padding:14px 24px;
  border-bottom:1px solid #1c1c1e;
  background:#0d0d0d;position:sticky;top:0;z-index:10;
}
.header-left{
  display:flex;align-items:center;gap:12px;
}
.brand{
  font-family:"Noto Serif SC",serif;font-size:16px;font-weight:700;
  color:#fff;letter-spacing:1px;
}
.nav-link{
  font-size:12px;color:#636366;text-decoration:none;padding:4px 10px;
  border-radius:6px;transition:all .15s;
}
.nav-link:hover{color:#e5e5ea;background:rgba(255,255,255,.06)}
.nav-link.active{color:#0a84ff;background:rgba(10,132,255,.1)}
.refresh-note{font-size:11px;color:#636366}

/* ── CARD GRID ── */
.card-grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(180px,1fr));
  gap:12px;padding:20px 24px;
}

/* ── CARD ── */
.card{
  background:#1c1c1e;border:1px solid #2c2c2e;border-radius:12px;
  padding:16px;
  transition:border-color .2s;
}
.card:hover{border-color:#48484a}
.card-label{
  font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.5px;color:#8e8e93;margin-bottom:6px;
}
.card-value{
  font-size:28px;font-weight:700;color:#fff;line-height:1.2;
}
.card-sub{
  font-size:12px;color:#636366;margin-top:4px;
}

/* ── SECTION ── */
.section{
  padding:0 24px 20px;
}
.section-title{
  font-size:13px;font-weight:600;text-transform:uppercase;
  letter-spacing:.6px;color:#8e8e93;margin-bottom:10px;
}

/* ── BAR CHART (pure CSS) ── */
.bar-chart{
  display:flex;flex-direction:column;gap:6px;
}
.bar-row{
  display:flex;align-items:center;gap:10px;
}
.bar-label{
  width:140px;text-align:right;font-size:12px;font-weight:500;
  color:#e5e5ea;flex-shrink:0;
}
.bar-track{
  flex:1;height:22px;background:#2c2c2e;border-radius:6px;overflow:hidden;
}
.bar-fill{
  height:100%;background:linear-gradient(90deg,#0a84ff,#5e5ce6);
  border-radius:6px;min-width:4px;
  transition:width .4s ease;
}
.bar-count{
  width:40px;font-size:12px;font-weight:600;color:#8e8e93;flex-shrink:0;
}

/* ── TABLE ── */
.table-wrap{overflow-x:auto}
table{
  width:100%;border-collapse:collapse;font-size:12px;
}
th{
  text-align:left;font-weight:600;color:#8e8e93;
  padding:6px 12px;border-bottom:1px solid #2c2c2e;
  text-transform:uppercase;letter-spacing:.4px;font-size:11px;
}
td{
  padding:8px 12px;border-bottom:1px solid #1c1c1e;
  color:#e5e5ea;
}
tr:hover{background:rgba(255,255,255,.02)}
.mono{font-family:"SF Mono","Fira Code","Cascadia Code",monospace;font-size:11px}
.warn-high{color:#ff3b30;font-weight:600}
.warn-mid{color:#ff9500;font-weight:600}
.warn-low{color:#34c759}
.text-muted{color:#636366}
.empty-state{
  text-align:center;padding:24px;color:#636366;font-size:13px;
}

/* ── TAG ── */
.tag{
  display:inline-block;padding:2px 8px;border-radius:4px;
  font-size:11px;font-weight:600;letter-spacing:.2px;
}
.tag-guard{background:rgba(255,149,0,.15);color:#ff9500}
.tag-parse{background:rgba(10,132,255,.15);color:#0a84ff}
.tag-circuit{background:rgba(255,59,48,.15);color:#ff3b30}
.tag-empty{background:rgba(142,142,147,.2);color:#8e8e93}
.tag-chatter{background:rgba(175,82,222,.15);color:#af52de}
.tag-qa{background:rgba(255,204,0,.15);color:#ffcc00}

@media(max-width:640px){
  .card-grid{grid-template-columns:1fr 1fr}
  .bar-label{width:100px}
}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <span class="brand">西渡</span>
    <a href="/dashboard" class="nav-link">Dashboard</a>
    <a href="/usage" class="nav-link active">Usage</a>
    <a href="/" class="nav-link">Home</a>
  </div>
  <span class="refresh-note">Auto-refresh every 30s &middot; <span id="last-refresh">--</span></span>
</div>

<!-- ── ERROR SUMMARY CARDS ── -->
<div class="card-grid" id="summary-cards"></div>

<!-- ── TOP ERROR TYPES ── -->
<div class="section">
  <div class="section-title">Top Error Types (last 7 days)</div>
  <div id="error-bars"></div>
</div>

<!-- ── RECENT ISSUES TABLE ── -->
<div class="section">
  <div class="section-title">Recent Issues</div>
  <div class="table-wrap">
    <table><thead><tr>
      <th>Time</th><th>Type</th><th>Detail</th><th>Job</th><th>Ch</th>
    </tr></thead><tbody id="recent-table"></tbody></table>
  </div>
</div>

<!-- ── EDITOR BEHAVIOR ── -->
<div class="section">
  <div class="section-title">Editor Activity</div>
  <div class="card-grid" id="editor-cards"></div>
</div>

<!-- ── TRANSLATION HEALTH ── -->
<div class="section">
  <div class="section-title">Translation Health (per job)</div>
  <div class="table-wrap" id="health-table-wrap">
    <table><thead><tr>
      <th>Job ID</th><th>Chapters</th><th>With Warnings</th><th>Warning Rate</th>
    </tr></thead><tbody id="health-table"></tbody></table>
  </div>
</div>

<script>
const API = '/api/usage/events';

function fmt(n){ return n != null ? Number(n).toLocaleString() : '—'; }

function tagForType(t){
  var m = {
    'guard_warning':'tag-guard',
    'parse_fallback':'tag-parse',
    'circuit_breaker':'tag-circuit',
    'empty_output':'tag-empty',
    'chatter_detected':'tag-chatter',
    'qa_low_score':'tag-qa'
  };
  return m[t] || 'tag-guard';
}

function warnClass(rate){
  if(rate>=30) return 'warn-high';
  if(rate>=10) return 'warn-mid';
  return 'warn-low';
}

function render(data){
  document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();

  // ── Summary cards ──
  var s = data.summary || {};
  var total = s.total || 0;
  var cards = document.getElementById('summary-cards');
  cards.innerHTML = [
    {label:'Total Errors (7d)',value:fmt(total),sub:''},
    {label:'Guard Warnings',value:fmt(s.guard_warning||0),sub:''},
    {label:'Parse Fallbacks',value:fmt(s.parse_fallback||0),sub:'JSON parsing degraded'},
    {label:'Circuit Trips',value:fmt(s.circuit_breaker||0),sub:'Breaker OPEN events'},
  ].map(function(c){
    return '<div class="card"><div class="card-label">'+c.label+'</div><div class="card-value">'+c.value+'</div><div class="card-sub">'+c.sub+'</div></div>';
  }).join('');

  // ── Top error types bar chart ──
  var bars = document.getElementById('error-bars');
  var types = Object.entries(s).filter(function(e){return e[0]!=='total';});
  types.sort(function(a,b){return b[1]-a[1];});
  var top5 = types.slice(0,5);
  if(top5.length===0){
    bars.innerHTML = '<div class="empty-state">No errors recorded in the last 7 days.</div>';
  }else{
    var maxCount = top5[0][1];
    bars.innerHTML = top5.map(function(e){
      var pct = maxCount>0 ? (e[1]/maxCount*100) : 0;
      return '<div class="bar-row">'+
        '<span class="bar-label">'+e[0].replace(/_/g,' ')+'</span>'+
        '<div class="bar-track"><div class="bar-fill" style="width:'+pct+'%"></div></div>'+
        '<span class="bar-count">'+fmt(e[1])+'</span>'+
        '</div>';
    }).join('');
  }

  // ── Recent issues table ──
  var tbody = document.getElementById('recent-table');
  var recent = data.recent || [];
  if(recent.length===0){
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No events recorded yet.</td></tr>';
  }else{
    tbody.innerHTML = recent.map(function(r){
      var time = r.created_at ? r.created_at.replace('T',' ').substring(0,19) : '—';
      return '<tr>'+
        '<td class="text-muted">'+time+'</td>'+
        '<td><span class="tag '+tagForType(r.event_type)+'">'+r.event_type.replace(/_/g,' ')+'</span></td>'+
        '<td>'+r.detail+'</td>'+
        '<td class="mono text-muted">'+(r.job_id||'—')+'</td>'+
        '<td class="text-muted">'+(r.chapter_number!=null?r.chapter_number:'—')+'</td>'+
        '</tr>';
    }).join('');
  }

  // ── Editor behavior cards ──
  var ecards = document.getElementById('editor-cards');
  var ed = data.editor || {};
  ecards.innerHTML = [
    {label:'Chapters Edited',value:fmt(ed.edited_chapters),sub:fmt(ed.jobs_edited)+' jobs touched'},
    {label:'Glossary Confirmed',value:fmt(ed.glossary_confirmed),sub:'Human-approved terms'},
    {label:'Glossary Rejected',value:fmt(ed.glossary_rejected),sub:'Blocked translations'},
    {label:'Edit Operations',value:fmt(ed.batch_replace_count),sub:'Saved + batch replaces'},
  ].map(function(c){
    return '<div class="card"><div class="card-label">'+c.label+'</div><div class="card-value">'+c.value+'</div><div class="card-sub">'+c.sub+'</div></div>';
  }).join('');

  // ── Translation health table ──
  var htbody = document.getElementById('health-table');
  var health = data.jobs_health || [];
  if(health.length===0){
    htbody.innerHTML = '<tr><td colspan="4" class="empty-state">No job events recorded yet. Run a translation to populate this table.</td></tr>';
  }else{
    htbody.innerHTML = health.map(function(h){
      return '<tr>'+
        '<td class="mono">'+h.job_id+'</td>'+
        '<td>'+fmt(h.total_chapters)+'</td>'+
        '<td>'+fmt(h.chapters_with_warnings)+'</td>'+
        '<td class="'+warnClass(h.warning_rate_pct)+'">'+h.warning_rate_pct+'%</td>'+
        '</tr>';
    }).join('');
  }
}

function poll(){
  fetch(API).then(function(r){return r.json();}).then(render).catch(function(e){
    console.error('Usage poll failed:', e);
  });
}

poll();
setInterval(poll, 30000);
</script>
</body>
</html>"""


def get_usage_data() -> dict:
    """Assemble all live metrics for the usage page JSON endpoint.

    【中文】汇总所有用量分析数据，返回给页面显示。
    · 网址：GET /api/usage/events（由 main.py 注册）
    · 参数：无
    · 返回：一个大字典，包含四个板块的数据：
      ① summary：最近 7 天的错误类型汇总（每种类型出现了多少次）
      ② recent：最近 20 条错误事件详情（时间、类型、关联任务和章节）
      ③ editor：人工编辑行为统计（编辑了多少章、确认/拒绝了多少术语）
      ④ jobs_health：每个翻译任务的健康度（总章节数、有警告的章节数、警告率）
    · 为什么存在：和看板一样，页面本身只是 HTML，靠定时调这个函数获取数据。
      刷新间隔是 30 秒（比看板的 5 秒慢，因为用量数据变化没那么频繁）。
    """
    return {
        # 【中文】get_event_summary(days=7) 统计最近 7 天各类错误的出现次数。
        "summary": get_event_summary(days=7),
        # 【中文】get_recent_issues(20) 取最近 20 条错误事件。
        "recent": get_recent_issues(20),
        # 【中文】get_editor_stats() 汇总人工编辑行为数据。
        "editor": get_editor_stats(),
        # 【中文】get_all_jobs_health() 计算每个任务的翻译健康指标。
        "jobs_health": get_all_jobs_health(),
    }
