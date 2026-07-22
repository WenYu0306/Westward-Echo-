"""Observability dashboard — real-time system metrics as a dark-theme HTML page.

Served at ``GET /dashboard`` (no auth).  Auto-refreshes every 5 seconds.
"""

import json
import os

from .health import HealthChecker, check_memory
from .stats import TranslationStats
from .circuit_breaker import get_all_breakers
from .backpressure import backpressure
from .config import (
    HOST,
    API_PORT,
    RATE_LIMIT_RPM,
    RATE_LIMIT_ENABLED,
    CHAPTER_COOLDOWN_SECONDS,
)

DASHBOARD_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — Westward Echo</title>
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
  padding:16px 24px;
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
.badge{
  display:inline-flex;align-items:center;gap:5px;
  padding:3px 10px;border-radius:999px;
  font-size:11px;font-weight:600;letter-spacing:.3px;
}
.badge-healthy{background:rgba(52,199,89,.15);color:#34c759}
.badge-degraded{background:rgba(255,149,0,.15);color:#ff9500}
.badge-unhealthy{background:rgba(255,59,48,.15);color:#ff3b30}
.badge-offline{background:rgba(142,142,147,.12);color:#8e8e93}
.refresh-note{font-size:11px;color:#636366}

/* ── GRID ── */
.grid{
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
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

/* ── BREAKER CARD ── */
.breaker-grid{display:flex;flex-direction:column;gap:4px;margin-top:6px}
.breaker-row{display:flex;justify-content:space-between;align-items:center;font-size:12px}
.breaker-lang{color:#e5e5ea;font-weight:500}
.breaker-state{font-weight:600}
.state-closed{color:#34c759}.state-open{color:#ff3b30}.state-half_open{color:#ff9500}

/* ── SECTION ── */
.section{
  padding:0 24px 20px;
}
.section-title{
  font-size:13px;font-weight:600;text-transform:uppercase;
  letter-spacing:.6px;color:#8e8e93;margin-bottom:10px;
}

/* ── ERROR RATE TABLE ── */
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
.error-high{color:#ff3b30;font-weight:600}
.error-mid{color:#ff9500;font-weight:600}
.error-low{color:#34c759}

/* ── ANIMATION ── */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.pulse{animation:pulse 2s infinite}

@media(max-width:640px){
  .grid{grid-template-columns:1fr}
  .header{flex-direction:column;align-items:flex-start;gap:8px}
}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <span class="brand">西渡</span>
    <span class="badge badge-offline" id="health-badge">checking...</span>
  </div>
  <span class="refresh-note">Auto-refresh every 5s &middot; <span id="last-refresh">--</span></span>
</div>

<div class="grid" id="metric-cards"></div>

<div class="section">
  <div class="section-title">Circuit Breakers</div>
  <div id="breakers"></div>
</div>

<div class="section">
  <div class="section-title">Error Rates (last 100 API calls per language)</div>
  <div class="table-wrap">
    <table><thead><tr>
      <th>Language</th><th>Calls</th><th>Failed</th><th>Error Rate</th>
    </tr></thead><tbody id="error-table"></tbody></table>
  </div>
</div>

<script>
const API = '/api/dashboard/metrics';

function fmt(n){ return n != null ? Number(n).toLocaleString() : '—'; }
function fmtPct(n){ return n != null ? (Number(n)*100).toFixed(1)+'%' : '—'; }
function fmtDuration(s){
  if(s == null) return '—';
  s = Number(s);
  if(s < 60) return Math.floor(s)+'s';
  if(s < 3600) return Math.floor(s/60)+'m '+Math.floor(s%60)+'s';
  var h = Math.floor(s/3600);
  var m = Math.floor((s%3600)/60);
  return h+'h '+m+'m';
}

function stateClass(s){
  s = (s||'').toLowerCase();
  if(s==='closed') return 'state-closed';
  if(s==='open') return 'state-open';
  if(s==='half_open') return 'state-half_open';
  return '';
}

function errorClass(rate){
  if(rate>=0.3) return 'error-high';
  if(rate>=0.1) return 'error-mid';
  return 'error-low';
}

function render(data){
  var m = data.metrics_stats || {};
  var h = data.health || {};
  var bp = data.backpressure || {};
  var breakerList = data.breakers || {};
  var memory = data.memory || {};
  var config = data.config || {};

  // Health badge
  var badge = document.getElementById('health-badge');
  var status = (h.status||'unknown').toLowerCase();
  badge.textContent = status.toUpperCase();
  badge.className = 'badge badge-' + status;
  if(status==='healthy') badge.textContent='HEALTHY';
  else if(status==='degraded') badge.textContent='DEGRADED';
  else if(status==='unhealthy') badge.textContent='UNHEALTHY';

  document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();

  // Metric cards
  var cards = document.getElementById('metric-cards');
  cards.innerHTML = [
    {label:'Chapters Translated',value:fmt(m.chapters_translated),sub:m.chapters_failed+' failed'},
    {label:'Uptime',value:fmtDuration(m.uptime_seconds),sub:'Since '+new Date(m.session_start).toLocaleString()},
    {label:'Throughput',value:fmt(m.throughput_chapters_per_minute),sub:'chapters/min (last 5 min)'},
    {label:'API Calls',value:fmt(m.api_calls_total),sub:fmt(m.api_calls_failed)+' failed'},
    {label:'Queue Depth',value:fmt(bp.queue_depth),sub:bp.backpressured?'<span class="error-high">BACKPRESSURE</span>':'/'+fmt(bp.max_queue_depth)},
    {label:'Memory',value:memory.percent!=null?memory.percent+'%':'—',sub:memory.used_gb!=null?memory.used_gb+' / '+memory.total_gb+' GB':''},
    {label:'Rate Limit',value:config.rate_limit_enabled?'ON':'OFF',sub:config.rate_limit_rpm+' RPM'},
    {label:'Config',value:'v0.2.0',sub:'DeepSeek / Celery'},
  ].map(function(c){
    return '<div class="card"><div class="card-label">'+c.label+'</div><div class="card-value">'+c.value+'</div><div class="card-sub">'+c.sub+'</div></div>';
  }).join('');

  // Circuit breakers
  var bdiv = document.getElementById('breakers');
  if(Object.keys(breakerList).length===0){
    bdiv.innerHTML='<div class="card" style="color:#636366">No circuit breakers registered yet.</div>';
  }else{
    bdiv.innerHTML = Object.entries(breakerList).map(function(_a){
      var lang=_a[0], b=_a[1];
      return '<div class="card"><div class="card-label">'+lang+'</div>'+
        '<div class="breaker-grid">'+
        '<div class="breaker-row"><span class="breaker-lang">'+b.name+'</span><span class="breaker-state '+stateClass(b.state)+'">'+b.state.toUpperCase()+'</span></div>'+
        '<div class="breaker-row" style="font-size:11px;color:#636366">'+
          'Calls: '+fmt(b.total_calls)+' | OK: '+fmt(b.total_successes)+' | Fail: '+fmt(b.total_failures)+' | Trips: '+fmt(b.open_transitions)+
        '</div></div></div>';
    }).join('');
  }

  // Error rate table
  var tbody = document.getElementById('error-table');
  var rates = m.error_rates_per_language || {};
  var langs = Object.keys(rates);
  if(langs.length===0){
    tbody.innerHTML='<tr><td colspan="4" style="color:#636366;text-align:center;padding:16px">No API calls recorded yet.</td></tr>';
  }else{
    tbody.innerHTML = langs.map(function(l){
      var r = rates[l];
      return '<tr><td>'+l+'</td><td>'+fmt(r.total)+'</td><td>'+fmt(r.failed)+'</td>'+
        '<td class="'+errorClass(r.error_rate)+'">'+fmtPct(r.error_rate)+'</td></tr>';
    }).join('');
  }
}

function poll(){
  fetch(API).then(function(r){return r.json();}).then(render).catch(function(e){
    console.error('Dashboard poll failed:', e);
    document.getElementById('health-badge').textContent='OFFLINE';
    document.getElementById('health-badge').className='badge badge-offline';
  });
}

poll();
setInterval(poll, 5000);
</script>
</body>
</html>"""


def get_dashboard_data() -> dict:
    """Assemble all live metrics for the dashboard JSON endpoint."""
    health = HealthChecker().check_all()
    memory = check_memory()

    return {
        "metrics_stats": TranslationStats.snapshot(),
        "health": health,
        "backpressure": backpressure.snapshot(),
        "breakers": get_all_breakers(),
        "memory": {
            "used_gb": memory.get("used_gb"),
            "total_gb": memory.get("total_gb"),
            "percent": memory.get("percent"),
        },
        "config": {
            "rate_limit_rpm": RATE_LIMIT_RPM,
            "rate_limit_enabled": RATE_LIMIT_ENABLED,
            "chapter_cooldown_seconds": CHAPTER_COOLDOWN_SECONDS,
            "host": HOST,
            "port": API_PORT,
        },
    }
