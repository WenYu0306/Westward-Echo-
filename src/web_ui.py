"""Web UI — single-page app HTML template. No Gradio, no JS framework."""

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>西渡 / Westward Echo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400;14..32,500;14..32,600;14..32,700&family=Noto+Serif+SC:wght@400;600;700&display=swap" rel="stylesheet">
<style>
/* ═══════════════════════════════════════════════════════════════
   RESET & BASE
   ═══════════════════════════════════════════════════════════════ */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
body{
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-weight:400;font-size:14px;line-height:1.6;
  color:#1c1c1e;background:#f5f5f7;
}
::selection{background:rgba(0,102,204,.18)}

/* ═══════════════════════════════════════════════════════════════
   LAYOUT — full-width app shell with fixed sidebar
   ═══════════════════════════════════════════════════════════════ */
.app-shell{display:flex;height:100vh;overflow:hidden}

/* ── SIDEBAR ── */
.sidebar{
  width:280px;min-width:280px;background:#fff;
  border-right:1px solid #e5e5ea;
  display:flex;flex-direction:column;
  overflow:hidden;
}
.sidebar-header{
  padding:20px 20px 16px;border-bottom:1px solid #f0f0f5;
}
.sidebar-brand{
  font-family:"Noto Serif SC",serif;font-size:17px;font-weight:700;
  letter-spacing:1.5px;color:#1c1c1e;margin-bottom:14px;
}
.sidebar-brand .en{
  font-family:Inter,sans-serif;font-weight:400;font-size:12px;
  color:#8e8e93;letter-spacing:0;display:block;margin-top:1px;
}
.btn-new-translation{
  width:100%;padding:10px 0;font-family:Inter,sans-serif;font-size:13px;font-weight:600;
  color:#fff;background:#0071e3;border:none;border-radius:8px;cursor:pointer;
  transition:all .15s;letter-spacing:.1px;
}
.btn-new-translation:hover{background:#0077ed;transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,113,227,.25)}
.btn-new-translation:active{transform:scale(.98)}

.btn-cms-import{
  width:100%;padding:8px 0;margin-top:8px;
  font-family:Inter,sans-serif;font-size:12px;font-weight:500;
  color:#0071e3;background:rgba(0,113,227,.06);
  border:1px solid rgba(0,113,227,.15);border-radius:8px;cursor:pointer;
  transition:all .15s;letter-spacing:.1px;
}
.btn-cms-import:hover{background:rgba(0,113,227,.12);border-color:rgba(0,113,227,.3)}
.btn-cms-import:active{transform:scale(.98)}

/* CMS import modal */
.cms-modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,.35);
  display:none;align-items:center;justify-content:center;z-index:1000;
}
.cms-modal-overlay.open{display:flex}
.cms-modal{
  background:#fff;border-radius:16px;padding:28px 32px;
  width:420px;max-width:90vw;
  box-shadow:0 20px 60px rgba(0,0,0,.15);
}
.cms-modal h3{
  font-size:16px;font-weight:600;color:#1c1c1e;margin-bottom:6px;
}
.cms-modal .sub{font-size:12px;color:#8e8e93;margin-bottom:20px}
.cms-modal .field{margin-bottom:16px}
.cms-modal .field label{display:block;font-size:12px;font-weight:500;color:#3a3a3c;margin-bottom:5px}
.cms-modal .field input,.cms-modal .field select{
  width:100%;padding:9px 12px;font-size:13px;font-family:Inter,sans-serif;
  border:1px solid #d2d2d7;border-radius:8px;outline:none;color:#1c1c1e;
  transition:border-color .15s;
}
.cms-modal .field input:focus,.cms-modal .field select:focus{
  border-color:#0071e3;box-shadow:0 0 0 3px rgba(0,113,227,.12);
}
.cms-modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:8px}
.cms-modal .btn-cancel{
  padding:8px 20px;font-family:Inter,sans-serif;font-size:12px;font-weight:500;
  color:#6e6e73;background:#f0f0f5;border:1px solid #e5e5ea;border-radius:8px;
  cursor:pointer;transition:all .15s;
}
.cms-modal .btn-cancel:hover{background:#e5e5ea}
.cms-modal .btn-submit{
  padding:8px 20px;font-family:Inter,sans-serif;font-size:12px;font-weight:600;
  color:#fff;background:#0071e3;border:none;border-radius:8px;cursor:pointer;
  transition:all .15s;
}
.cms-modal .btn-submit:hover{background:#0077ed}
.cms-modal .btn-submit:disabled{background:#aeaeb2;cursor:not-allowed}

/* CMS source dropdown in modal */
.cms-source-list{max-height:160px;overflow-y:auto;margin-top:4px;border:1px solid #e5e5ea;border-radius:8px}
.cms-source-item{
  padding:8px 12px;font-size:13px;cursor:pointer;transition:background .12s;
  display:flex;align-items:center;justify-content:space-between;
}
.cms-source-item:hover{background:#f5f5f7}
.cms-source-item.sel{background:rgba(0,113,227,.06);color:#0071e3;font-weight:500}
.cms-loading-sources{font-size:12px;color:#aeaeb2;padding:12px;text-align:center}

.sidebar-jobs{
  flex:1;overflow-y:auto;padding:8px 0;
}
.sidebar-jobs-label{
  font-size:10px;font-weight:600;color:#aeaeb2;text-transform:uppercase;
  letter-spacing:.8px;padding:12px 20px 6px;
}
.sidebar-jobs::-webkit-scrollbar{width:4px}
.sidebar-jobs::-webkit-scrollbar-track{background:transparent}
.sidebar-jobs::-webkit-scrollbar-thumb{background:#e5e5ea;border-radius:2px}

/* Job item in sidebar */
.job-item{
  display:flex;align-items:flex-start;gap:10px;
  padding:10px 20px;cursor:pointer;transition:background .12s;
  border-left:3px solid transparent;
}
.job-item:hover{background:#f5f5f7}
.job-item.active{background:rgba(0,113,227,.06);border-left-color:#0071e3}
.job-item-icon{font-size:18px;flex-shrink:0;margin-top:1px}
.job-item-info{flex:1;min-width:0}
.job-item-filename{
  font-size:12px;font-weight:500;color:#1c1c1e;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.job-item-meta{font-size:10px;color:#aeaeb2;margin-top:1px}

/* Status badges */
.status-badge{
  font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;
  flex-shrink:0;line-height:1.4;margin-top:2px;
}
.status-badge.queued{background:rgba(142,142,147,.12);color:#8e8e93}
.status-badge.translating{background:rgba(0,113,227,.1);color:#0071e3}
.status-badge.complete{background:rgba(52,199,89,.1);color:#34c759}
.status-badge.failed{background:rgba(255,59,48,.1);color:#ff3b30}

/* Empty sidebar */
.sidebar-empty{
  text-align:center;padding:32px 20px;font-size:12px;color:#aeaeb2;
}

/* ── Project group in sidebar ── */
.project-group{margin-bottom:2px}
.project-group-header{
  display:flex;align-items:center;gap:6px;
  padding:8px 20px;cursor:pointer;transition:background .12s;
  border-left:3px solid transparent;
}
.project-group-header:hover{background:#f5f5f7}
.project-group-toggle{
  font-size:10px;color:#aeaeb2;transition:transform .2s;width:14px;text-align:center;
}
.project-group-toggle.open{transform:rotate(90deg)}
.project-group-name{
  font-size:12px;font-weight:600;color:#1c1c1e;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;
}
.project-group-badge{
  font-size:9px;font-weight:600;padding:2px 6px;border-radius:6px;
  background:rgba(142,142,147,.1);color:#8e8e93;flex-shrink:0;
}
.project-group-body{display:none}
.project-group-body.open{display:block}

/* ── Multi-lang selector in form ── */
.multi-lang-chips{display:flex;flex-wrap:wrap;gap:6px}
.lang-chip{
  padding:6px 12px;font-size:11px;font-weight:500;
  color:#6e6e73;background:#f5f5f7;border:1px solid #e5e5ea;border-radius:16px;
  cursor:pointer;transition:all .15s;user-select:none;
}
.lang-chip.on{background:#0071e3;color:#fff;border-color:#0071e3}
.lang-chip:hover:not(.on){border-color:#d2d2d7}
.multi-lang-hint{font-size:10px;color:#aeaeb2;margin-top:4px}

/* Child job under project group */
.job-item.child{padding-left:36px}

/* ── MAIN CONTENT ── */
.main-content{
  flex:1;overflow-y:auto;background:#f5f5f7;
  display:flex;flex-direction:column;
}
.main-content-inner{flex:1;padding:24px 32px;max-width:900px}

/* ── VIEW: New Translation (default) ── */
.view-new-translation .hero{
  text-align:center;padding:32px 0 36px;
  background:linear-gradient(180deg,transparent 0%,rgba(0,113,227,.03) 100%);
  border-radius:20px;margin-bottom:28px;
}
.view-new-translation .hero h1{
  font-family:"Noto Serif SC",serif;font-size:32px;font-weight:700;
  letter-spacing:3px;color:#1c1c1e;margin-bottom:6px;
}
.view-new-translation .hero h1 .slash{font-weight:300;color:#c7c7cc;margin:0 6px;font-family:Inter,sans-serif}
.view-new-translation .hero h1 .en{font-family:Inter,sans-serif;font-weight:300;font-size:28px;letter-spacing:-.5px;color:#8e8e93}
.view-new-translation .hero p{font-size:14px;color:#6e6e73;max-width:420px;margin:8px auto 0;line-height:1.5}

.translation-form-layout{display:flex;gap:32px;align-items:flex-start}
.form-left{flex:0 0 340px}
.form-right{flex:1;min-width:0}

@media(max-width:820px){
  .translation-form-layout{flex-direction:column}
  .form-left{flex:1 1 auto;width:100%}
}

@media(max-width:680px){
  /* Sidebar → collapsible top bar */
  .app-shell{flex-direction:column}
  .sidebar{width:100%;min-width:unset;max-height:56px;overflow:hidden;flex-shrink:0;
    border-right:none;border-bottom:1px solid #e5e5ea;transition:max-height .25s}
  .sidebar.open{max-height:420px;overflow-y:auto}
  .sidebar-header{padding:10px 16px;display:flex;align-items:center;justify-content:space-between}
  .sidebar-brand{font-size:15px;margin-bottom:0}
  .sidebar-brand .en{display:none}
  .btn-new-translation{display:none}
  .btn-cms-import{display:none}
  .sidebar-jobs{display:none}
  .sidebar.open .sidebar-jobs{display:block}
  .sidebar-footer{display:none}
  /* Hamburger */
  .sidebar-toggle{
    display:flex!important;width:32px;height:32px;align-items:center;justify-content:center;
    border-radius:6px;cursor:pointer;border:1px solid #e5e5ea;background:#fff;
    font-size:16px;color:#3a3a3c;flex-shrink:0;
  }
  /* Main content */
  .main-content-inner{padding:16px}
  .hero h1{font-size:20px!important}
  .hero h1 .en{font-size:18px!important}
  .hero p{font-size:12px!important}
  .preview-panel{border-radius:0;margin:0 -16px}
  .card{padding:16px;border-radius:10px}
  .drop-zone{padding:20px 12px}
  /* Job detail */
  .job-detail-header{flex-direction:column;gap:8px}
  .job-detail-title{white-space:normal!important}
}

/* ── VIEW: Job Detail ── */
.view-job-detail .job-detail-header{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:20px;gap:16px;
}
.job-detail-title{font-size:17px;font-weight:600;color:#1c1c1e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job-detail-actions{display:flex;gap:6px;flex-shrink:0}
.btn-delete-job{
  padding:6px 14px;font-family:Inter,sans-serif;font-size:11px;font-weight:500;
  color:#ff3b30;background:rgba(255,59,48,.06);border:1px solid rgba(255,59,48,.15);
  border-radius:6px;cursor:pointer;transition:all .15s;
}
.btn-delete-job:hover{background:rgba(255,59,48,.12)}

/* ═══════════════════════════════════════════════════════════════
   CARD — unified look
   ═══════════════════════════════════════════════════════════════ */
.card{
  background:#fff;border-radius:14px;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.02);
  padding:24px;margin-bottom:16px;
}
.card-label{
  font-size:11px;font-weight:600;color:#8e8e93;text-transform:uppercase;
  letter-spacing:.6px;margin-bottom:14px;
}

/* ═══════════════════════════════════════════════════════════════
   FILE DROP ZONE
   ═══════════════════════════════════════════════════════════════ */
.drop-zone{
  display:flex;align-items:center;justify-content:center;
  border:2px dashed #d2d2d7;border-radius:10px;padding:28px 16px;
  cursor:pointer;transition:all .2s;text-align:center;background:#fafafa;
}
.drop-zone:hover,.drop-zone.drag{border-color:#0071e3;background:rgba(0,113,227,.03)}
.drop-zone input{display:none}
.dz-icon{font-size:28px;margin-bottom:6px}
.dz-text{font-size:13px;color:#6e6e73}
.dz-text strong{color:#0071e3;font-weight:500}
.dz-hint{font-size:11px;color:#aeaeb2;margin-top:2px}
.dz-filename{margin-top:10px;font-size:13px;font-weight:500;color:#1c1c1e;word-break:break-all}

/* ═══════════════════════════════════════════════════════════════
   FORM
   ═══════════════════════════════════════════════════════════════ */
.field{margin-bottom:14px}
.field label{display:block;font-size:12px;font-weight:500;color:#3a3a3c;margin-bottom:5px}
.field select{
  width:100%;padding:9px 12px;font-size:13px;font-family:Inter,sans-serif;
  border:1px solid #d2d2d7;border-radius:8px;background:#fff;color:#1c1c1e;
  outline:none;appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 5l3 3 3-3' fill='none' stroke='%238e8e93' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;
  padding-right:32px;transition:border-color .15s;
}
.field select:focus{border-color:#0071e3;box-shadow:0 0 0 3px rgba(0,113,227,.12)}

.toggle-group{display:flex;gap:8px}
.toggle-group label{
  flex:1;text-align:center;padding:8px 0;font-size:12px;font-weight:500;
  color:#6e6e73;cursor:pointer;border-radius:8px;
  transition:all .15s;user-select:none;
}
.toggle-group label.on{background:#0071e3;color:#fff}
.toggle-group input{display:none}

.range-wrap{display:flex;align-items:center;gap:10px}
.range-wrap input[type=range]{flex:1;accent-color:#0071e3;height:4px}
.range-val{font-size:13px;font-weight:600;color:#1c1c1e;min-width:28px;text-align:center}

/* ═══════════════════════════════════════════════════════════════
   BUTTON
   ═══════════════════════════════════════════════════════════════ */
.btn{
  width:100%;padding:13px 0;font-family:Inter,sans-serif;font-size:14px;font-weight:600;
  color:#fff;background:#0071e3;border:none;border-radius:10px;cursor:pointer;
  transition:all .15s;letter-spacing:.1px;
}
.btn:hover{background:#0077ed;transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,113,227,.25)}
.btn:active{transform:scale(.99)}
.btn:disabled{background:#aeaeb2;cursor:not-allowed;transform:none;box-shadow:none}

/* ═══════════════════════════════════════════════════════════════
   PROGRESS
   ═══════════════════════════════════════════════════════════════ */
.progress-wrap{margin-bottom:16px}
.progress-bar{height:4px;background:#e5e5ea;border-radius:2px;overflow:hidden;margin-bottom:8px}
.progress-bar .fill{height:100%;width:0;background:#0071e3;border-radius:2px;transition:width .3s ease}
.progress-text{font-size:12px;color:#8e8e93;text-align:center;margin-bottom:4px}

/* Progress actions — cancel + retry */
.progress-actions{display:flex;gap:8px;margin-top:8px}
.btn-cancel{
  padding:7px 18px;font-family:Inter,sans-serif;font-size:12px;font-weight:500;
  color:#6e6e73;background:#f0f0f5;border:1px solid #e5e5ea;border-radius:8px;
  cursor:pointer;transition:all .15s;letter-spacing:.1px;width:100%;text-align:center;
}
.btn-cancel:hover{background:#e5e5ea;color:#3a3a3c}
.btn-cancel:active{transform:scale(.98)}
.btn-retry{
  padding:7px 18px;font-family:Inter,sans-serif;font-size:12px;font-weight:500;
  color:#fff;background:#0071e3;border:none;border-radius:8px;
  cursor:pointer;transition:all .15s;letter-spacing:.1px;width:100%;text-align:center;
}
.btn-retry:hover{background:#0077ed}
.btn-retry:active{transform:scale(.98)}

/* Chapter progress log */
.chapter-log{
  margin-top:8px;max-height:140px;overflow-y:auto;
  font-size:11px;font-family:"SF Mono","Fira Code","Cascadia Code",monospace;
  color:#8e8e93;line-height:1.6;
  background:#fafafa;border:1px solid #f0f0f5;border-radius:8px;padding:8px 12px;
}
.chapter-log .ch-entry{display:flex;align-items:center;gap:6px}
.chapter-log .ch-done{color:#34c759;font-size:11px}
.chapter-log .ch-fail{color:#ff3b30;font-size:11px}
.chapter-log .ch-num{color:#aeaeb2;font-size:10px;min-width:28px}
.chapter-log .ch-title{color:#6e6e73;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.chapter-log::-webkit-scrollbar{width:4px}
.chapter-log::-webkit-scrollbar-track{background:transparent}
.chapter-log::-webkit-scrollbar-thumb{background:#e5e5ea;border-radius:2px}

/* ═══════════════════════════════════════════════════════════════
   PREVIEW PANEL — in form-right or job-detail view
   ═══════════════════════════════════════════════════════════════ */
.preview-panel{
  background:#fff;border-radius:14px;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.02);
  min-height:400px;overflow:hidden;
}
.preview-empty{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:400px;color:#c7c7cc;
}
.preview-empty .pe-icon{font-size:48px;margin-bottom:12px}
.preview-empty .pe-text{font-size:14px;color:#aeaeb2}

/* Preview state: translating */
.preview-translating{display:flex;flex-direction:column;align-items:center;justify-content:center;height:400px}
.preview-translating .pt-icon{font-size:36px;margin-bottom:8px}
.preview-translating .pt-text{font-size:13px;color:#8e8e93;margin-bottom:6px}

/* Preview state: error */
.preview-error{display:flex;flex-direction:column;align-items:center;justify-content:center;height:400px;background:#fef2f2}
.preview-error .pe-icon{font-size:40px;margin-bottom:10px}
.preview-error .pe-text{font-size:14px;color:#dc2626;margin-bottom:4px;text-align:center;max-width:300px}
.preview-error .pe-detail{font-size:12px;color:#ef4444;margin-bottom:16px;text-align:center;max-width:360px}
.preview-error .pe-retry-btn{
  padding:8px 24px;font-family:Inter,sans-serif;font-size:13px;font-weight:600;
  color:#fff;background:#dc2626;border:none;border-radius:8px;cursor:pointer;
  transition:all .15s;letter-spacing:.1px;
}
.preview-error .pe-retry-btn:hover{background:#b91c1c;transform:translateY(-1px);box-shadow:0 4px 12px rgba(220,38,38,.2)}
.preview-error .pe-retry-btn:active{transform:scale(.98)}

/* Tabs */
.preview-tabs{display:flex;border-bottom:1px solid #f0f0f5;padding:0 20px;align-items:center}
.preview-tab{
  padding:14px 18px;font-size:13px;font-weight:500;color:#8e8e93;
  background:none;border:none;border-bottom:2px solid transparent;
  cursor:pointer;transition:all .15s;
}
.preview-tab:hover{color:#3a3a3c}
.preview-tab.sel{color:#1c1c1e;border-bottom-color:#0071e3}

/* Translation output */
.preview-body{padding:40px 48px;display:none}
.preview-body.show{display:block}

#out-translation{
  font-family:"Noto Serif SC","Georgia",serif;font-size:16px;line-height:1.9;
  color:#2c2c2e;max-width:640px;margin:0 auto;
}
#out-translation h1{font-size:24px;font-weight:700;margin:0 0 24px;color:#1c1c1e}
#out-translation h2{font-size:18px;font-weight:600;margin:32px 0 12px;color:#1c1c1e}
#out-translation p{margin-bottom:14px}
#out-translation hr{margin:36px 0;border:none;border-top:1px solid #e5e5ea}

/* Glossary table */
#out-glossary{max-width:640px;margin:0 auto}
.glossary-table{width:100%;border-collapse:collapse;font-size:14px}
.glossary-table th{
  font-size:11px;font-weight:600;color:#8e8e93;text-transform:uppercase;
  letter-spacing:.5px;padding:10px 14px;border-bottom:2px solid #e5e5ea;text-align:left;
}
.glossary-table td{padding:10px 14px;border-bottom:1px solid #f5f5f7;color:#3a3a3c}
.glossary-table tr:hover td{background:#fafafa}

/* Quality report */
#out-report{font-size:14px;line-height:1.8;max-width:640px;margin:0 auto;color:#3a3a3c}
#out-report h2{font-size:17px;margin-bottom:10px;color:#1c1c1e}
#out-report table{width:100%;border-collapse:collapse;margin:14px 0}
#out-report th,#out-report td{padding:8px 14px;border:1px solid #e5e5ea;font-size:13px;text-align:left}
#out-report th{background:#fafafa;font-weight:600}

/* EPUB download button (in tab bar) */
.epub-dl-btn{
  display:inline-block;padding:8px 16px;font-family:Inter,sans-serif;font-size:12px;font-weight:600;
  color:#0071e3;background:rgba(0,113,227,.08);border-radius:8px;text-decoration:none;
  transition:all .15s;line-height:1;white-space:nowrap;
}
.epub-dl-btn:hover{background:rgba(0,113,227,.16);color:#0077ed}

/* Job detail info cards */
.job-detail-cards{display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap}
.job-detail-card{
  background:#fff;border-radius:10px;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.02);
  padding:16px 20px;flex:1;min-width:140px;
}
.job-detail-card-label{font-size:10px;font-weight:600;color:#8e8e93;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.job-detail-card-value{font-size:16px;font-weight:600;color:#1c1c1e}

/* ═══════════════════════════════════════════════════════════════
   TOAST
   ═══════════════════════════════════════════════════════════════ */
.toast{
  position:fixed;top:24px;left:50%;transform:translateX(-50%);
  padding:12px 24px;border-radius:10px;font-size:13px;font-weight:500;
  color:#fff;background:#ff3b30;box-shadow:0 8px 24px rgba(255,59,48,.25);
  z-index:999;opacity:0;pointer-events:none;transition:opacity .3s;
}
.toast.show{opacity:1}

/* ═══════════════════════════════════════════════════════════════
   SPINNER
   ═══════════════════════════════════════════════════════════════ */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #d2d2d7;border-top-color:#0071e3;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px;position:relative;top:-1px}

/* Glyph link in sidebar footer */
.sidebar-footer{
  padding:12px 20px;border-top:1px solid #f0f0f5;
}
.sidebar-footer a{
  font-size:11px;color:#8e8e93;text-decoration:none;
}
.sidebar-footer a:hover{color:#0071e3}
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<!-- ═════════════ CMS IMPORT MODAL ═════════════ -->
<div class="cms-modal-overlay" id="cms-modal-overlay">
  <div class="cms-modal">
    <h3>从 CMS 导入小说</h3>
    <p class="sub">从文件系统或 CMS 中选取小说，自动开始翻译</p>

    <div class="field">
      <label>来源标识 (source_id)</label>
      <input type="text" id="cms-source-id" placeholder="输入文件名或 CMS ID">
    </div>
    <div class="field">
      <label>或从已有来源中选择</label>
      <div class="cms-source-list" id="cms-source-list">
        <div class="cms-loading-sources">加载中...</div>
      </div>
    </div>
    <div class="cms-modal-actions">
      <button class="btn-cancel" id="cms-modal-cancel">取消</button>
      <button class="btn-submit" id="cms-modal-submit">导入并翻译</button>
    </div>
  </div>
</div>

<div class="app-shell">

  <!-- ═════════════ SIDEBAR ═════════════ -->
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-brand">
        西渡
        <span class="en">Westward Echo</span>
      </div>
      <button class="sidebar-toggle" id="sidebar-toggle" style="display:none" onclick="document.getElementById('sidebar').classList.toggle('open')">&#9776;</button>
      <button class="btn-new-translation" id="btn-new-translation">+ 新建翻译</button>
      <button class="btn-cms-import" id="btn-cms-import">&larr; 从 CMS 导入</button>
      <button class="btn-cms-import" id="btn-multi-translate" style="margin-top:4px">+ 多语种翻译</button>
    </div>

    <div class="sidebar-jobs" id="sidebar-jobs">
      <div class="sidebar-jobs-label">翻译记录</div>
      <div id="job-list"></div>
    </div>

    <div class="sidebar-footer">
      <a href="/review">术语审核 &rarr;</a>
    </div>
  </aside>

  <!-- ═════════════ MAIN CONTENT ═════════════ -->
  <main class="main-content" id="main-content">
    <div class="main-content-inner">

      <!-- ═══ VIEW: New Translation ═══ -->
      <div class="view-new-translation" id="view-new">

        <header class="hero">
          <h1>西渡<span class="slash"> / </span><span class="en">Westward Echo</span></h1>
          <p>四 Agent 读者视角多语种翻译 &mdash; READ·WRITE·READBACK·FIX 协作，术语全本统一，感官画面重建，冷读质量验证</p>
        </header>

        <div class="translation-form-layout">
          <div class="form-left">
            <div class="card">
              <div class="card-label">1. 上传小说</div>
              <div class="drop-zone" id="drop-zone">
                <div>
                  <div class="dz-icon">&#128196;</div>
                  <div class="dz-text">拖拽 <strong>.txt</strong> 到此处，或 <strong>点击选择</strong></div>
                  <div class="dz-hint">自动按章节拆分 &middot; 中文网文专用</div>
                  <input type="file" id="file-input" accept=".txt">
                  <div class="dz-filename" id="dz-filename"></div>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-label">2. 翻译设置</div>

              <div class="field" id="field-target-lang">
                <label>目标语言</label>
                <select id="target-lang">
                  <option value="en-US">English（英语）</option>
                  <option value="es-ES">Espa&ntilde;ol（西班牙语）</option>
                  <option value="de">Deutsch（德语）</option>
                  <option value="fr">Fran&ccedil;ais（法语）</option>
                </select>
              </div>
              <div class="field" id="field-multi-langs" style="display:none">
                <label>多语种选择</label>
                <div class="multi-lang-chips" id="multi-lang-chips">
                  <span class="lang-chip on" data-lang="en-US">English</span>
                  <span class="lang-chip on" data-lang="es-ES">Espa&ntilde;ol</span>
                  <span class="lang-chip on" data-lang="de">Deutsch</span>
                  <span class="lang-chip on" data-lang="fr">Fran&ccedil;ais</span>
                </div>
                <div class="multi-lang-hint">点击语言标签选择/取消。所选语言将同时翻译。</div>
              </div>

              <div class="field">
                <label>DeepSeek API Key（可选）</label>
                <input type="password" id="api-key" placeholder="留空使用服务器默认 Key" style="width:100%;padding:8px 12px;border:1px solid #d2d2d7;border-radius:6px;font-size:13px;">
                <div style="font-size:11px;color:#8e8e93;margin-top:4px">内测阶段可不填。有自己 Key 的填自己的。</div>
              </div>

              <div class="field">
                <label>翻译模式</label>
                <div class="toggle-group-display" style="background:#f5f5f7;border-radius:6px;padding:10px 14px;font-size:13px;color:#1c1c1e;">
                  四 Agent 协作翻译 &mdash; READ / WRITE / READBACK / FIX<br>
                  <span style="color:#8e8e93;font-size:11px;">文化缺口检测 &middot; 感官画面重建 &middot; 冷读盲评</span>
                </div>
              </div>

              <div class="field">
                <label>小说类型</label>
                <select id="genre">
                  <option value="romance_ceo">现代言情 / 总裁 (romance_ceo)</option>
                  <option value="xianxia">仙侠 / 修真 (xianxia)</option>
                  <option value="urban">都市 / 现实 (urban)</option>
                  <option value="scifi">科幻 / 机甲 (sci-fi)</option>
                  <option value="folk_religion">民间信仰 / 出马 (folk)</option>
                </select>
              </div>

              <div class="field">
                <label>术语表预设</label>
                <select id="glossary-preset">
                  <option value="">（无 - 从头开始）</option>
                </select>
              </div>

              <div class="field">
                <label>质检频率（每 <span id="range-label">20</span> 章）</label>
                <div class="range-wrap">
                  <span style="font-size:12px;color:#aeaeb2">5</span>
                  <input type="range" id="qa-interval" min="5" max="50" step="5" value="20">
                  <span style="font-size:12px;color:#aeaeb2">50</span>
                </div>
              </div>
            </div>

            <button class="btn" id="start-btn">开始翻译</button>

            <div class="progress-wrap" id="progress-wrap" style="display:none">
              <div class="progress-bar"><div class="fill" id="progress-fill"></div></div>
              <div class="progress-text" id="progress-text"></div>
              <div class="chapter-log" id="chapter-log" style="display:none"></div>
              <div class="progress-actions" id="progress-actions" style="display:none">
                <button class="btn-cancel" id="cancel-btn">取消翻译</button>
              </div>
              <div class="progress-actions" id="error-actions" style="display:none">
                <button class="btn-retry" id="retry-btn">重试</button>
              </div>
            </div>
          </div>

          <div class="form-right">
            <div class="preview-panel" id="preview-panel">
              <div class="preview-empty" id="preview-idle">
                <div class="pe-icon">&#128214;</div>
                <div class="pe-text">上传小说开始翻译</div>
              </div>
              <div class="preview-translating" id="preview-translating" style="display:none">
                <div class="pt-icon"><span class="spinner" style="width:24px;height:24px;border-width:3px;margin:0"></span></div>
                <div class="pt-text">翻译进行中...</div>
              </div>
              <div class="preview-error" id="preview-error" style="display:none">
                <div class="pe-icon">&#9888;</div>
                <div class="pe-text">翻译失败</div>
                <div class="pe-detail" id="preview-error-msg"></div>
                <button class="pe-retry-btn" id="preview-retry-btn">重试</button>
              </div>
              <div class="preview-tabs" id="preview-tabs" style="display:none">
                <button class="preview-tab sel" data-tab="translation">译文</button>
                <button class="preview-tab" data-tab="glossary">术语表</button>
                <button class="preview-tab" data-tab="report">质量报告</button>
                <span style="flex:1"></span>
                <a class="epub-dl-btn" id="epub-dl-btn" href="#" title="下载 EPUB 电子书">下载 EPUB</a>
              </div>
              <div class="preview-body" id="body-translation"><div id="out-translation"></div></div>
              <div class="preview-body" id="body-glossary"><div id="out-glossary"></div></div>
              <div class="preview-body" id="body-report"><div id="out-report"></div></div>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══ VIEW: Job Detail ═══ -->
      <div class="view-job-detail" id="view-job" style="display:none"></div>
    </div>
  </main>
</div>

<script>
const $=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);

// ── DOM refs ──
const sidebarJobs=$('#sidebar-jobs'),jobList=$('#job-list');
const viewNew=$('#view-new'),viewJob=$('#view-job');

// New-translation form elements
const dropZone=$('#drop-zone'),fileInput=$('#file-input'),dzFilename=$('#dz-filename');
const startBtn=$('#start-btn'),progressWrap=$('#progress-wrap'),progressFill=$('#progress-fill'),progressText=$('#progress-text');
const chapterLog=$('#chapter-log'),progressActions=$('#progress-actions'),errorActions=$('#error-actions');
const cancelBtn=$('#cancel-btn'),retryBtn=$('#retry-btn');
const previewIdle=$('#preview-idle'),previewTranslating=$('#preview-translating'),previewError=$('#preview-error'),previewTabs=$('#preview-tabs');
const previewErrorMsg=$('#preview-error-msg'),previewRetryBtn=$('#preview-retry-btn');
const epubDlBtn=$('#epub-dl-btn');
const rangeInput=$('#qa-interval'),rangeLabel=$('#range-label');
const toast=$('#toast');

// ── State ──
let selectedFile=null;
let activeJobId=null;
let activeForm=null;
let cancelRequested=false;
let chapterEntries=[];
let retryCount=0;
let currentView='new';        // 'new' | 'job'
let selectedJobId=null;      // job currently displayed in detail view
let pollTimer=null;
let jobsCache=[];
let projectsCache=[];
let isMultiLangs=false;      // whether we're in multi-language mode
let expandedProjects={};     // which project groups are expanded in sidebar

// ── Show a specific view ──
function showView(name){
  currentView=name;
  viewNew.style.display=name==='new'?'':'none';
  viewJob.style.display=name==='job'?'':'none';
}

// ── Load sidebar job list ──
async function loadJobList(){
  try{
    const r=await fetch('/api/jobs?limit=50');
    const allJobs=await r.json();
    const myJobs=getOwnJobs();
    jobsCache=allJobs.filter(j=>myJobs.includes(j.job_id));
    // Also fetch projects for grouping
    try{
      const pr=await fetch('/api/projects?limit=10');
      const allProjects=await pr.json();
      projectsCache=allProjects.filter(p=>p.jobs.some(j=>myJobs.includes(j.job_id)));
    }catch(e){projectsCache=[]}
    renderJobList();
  }catch(e){/* silent */}
}

// ── Load glossary presets into the dropdown ──
async function loadPresets(){
  try{
    const r=await fetch('/api/presets');
    const d=await r.json();
    const sel=$('#glossary-preset');
    // Clear all except the default option
    sel.innerHTML='<option value="">（无 - 从头开始）</option>';
    d.presets.forEach(p=>{
      const opt=document.createElement('option');
      opt.value=p.preset_name;
      opt.textContent=p.preset_name+(p.description?' — '+p.description:'');
      sel.appendChild(opt);
    });
  }catch(e){/* silent */}
}

function renderJobList(){
  // Build a set of job_ids that belong to a project group
  const groupedJobIds=new Set();
  if(projectsCache.length){
    projectsCache.forEach(p=>{p.jobs.forEach(j=>groupedJobIds.add(j.job_id))});
  }

  // Identify standalone jobs (not in any project)
  const standaloneJobs=jobsCache.filter(j=>!groupedJobIds.has(j.job_id));

  if(!jobsCache.length && !projectsCache.length){
    jobList.innerHTML='<div class="sidebar-empty">暂无翻译记录</div>';
    return;
  }

  let h='';

  // Render project groups first
  projectsCache.forEach(p=>{
    const isOpen=expandedProjects[p.project_id]!==false; // default open
    const allComplete=p.jobs.every(j=>j.status==='complete');
    const anyFailed=p.jobs.some(j=>j.status==='failed');
    const anyTranslating=p.jobs.some(j=>j.status==='translating'||j.status==='queued');
    let groupIcon='&#128193;'; // folder
    if(allComplete)groupIcon='&#128194;'; // green check style folder we approximate
    if(anyFailed&&!anyTranslating)groupIcon='&#128193;';

    const jobCount=p.jobs.length;
    const langList=p.jobs.map(j=>{
      const langLabel=j.target_lang==='en-US'?'EN':j.target_lang==='es-ES'?'ES':j.target_lang==='de'?'DE':j.target_lang==='fr'?'FR':j.target_lang;
      const icon=j.status==='complete'?'&#9989;':j.status==='failed'?'&#10060;':j.status==='translating'?'&#9881;':'&#9203;';
      return icon+' '+langLabel;
    }).join(' ');

    h+='<div class="project-group">';
    h+='<div class="project-group-header" data-project-id="'+p.project_id+'">';
    h+='<span class="project-group-toggle'+(isOpen?' open':'')+'">&#9654;</span>';
    h+='<span class="project-group-name">'+esc(p.filename||'Project')+'</span>';
    h+='<span class="project-group-badge">'+jobCount+' 语种</span>';
    h+='</div>';
    h+='<div class="project-group-body'+(isOpen?' open':'')+'" id="proj-body-'+p.project_id+'">';
    p.jobs.forEach(j=>{
      const isActive=selectedJobId===j.job_id;
      const icon=j.status==='complete'?'&#9989;':j.status==='failed'?'&#10060;':j.status==='translating'?'&#9881;':'&#128196;';
      const statusLabel={'queued':'排队中','translating':'翻译中','complete':'已完成','failed':'失败'}[j.status]||j.status;
      let langLabel=j.target_lang;
      if(j.target_lang==='en-US')langLabel='en-US';
      else if(j.target_lang==='es-ES')langLabel='es-ES';
      else if(j.target_lang==='de')langLabel='de';
      else if(j.target_lang==='fr')langLabel='fr';
      let meta=formatDate(j.created_at);
      if(j.status==='translating' && j.completed_chapters && j.total_chapters)
        meta=j.completed_chapters+'/'+j.total_chapters+' 章';
      if(j.status==='complete' && j.completed_chapters)
        meta=j.completed_chapters+' 章';
      h+='<div class="job-item child'+(isActive?' active':'')+'" data-job-id="'+j.job_id+'">';
      h+='<span class="job-item-icon">'+icon+'</span>';
      h+='<div class="job-item-info">';
      h+='<div class="job-item-filename">'+esc(langLabel)+'</div>';
      h+='<div class="job-item-meta">'+meta+'</div>';
      h+='</div>';
      h+='<span class="status-badge '+j.status+'">'+statusLabel+'</span>';
      h+='</div>';
    });
    h+='</div></div>';
  });

  // Render standalone jobs (no project)
  standaloneJobs.forEach(j=>{
    const isActive=selectedJobId===j.job_id;
    const icon=j.status==='complete'?'&#9989;':j.status==='failed'?'&#10060;':j.status==='translating'?'&#9881;':'&#128196;';
    const statusLabel={'queued':'排队中','translating':'翻译中','complete':'已完成','failed':'失败'}[j.status]||j.status;
    let meta=formatDate(j.created_at);
    if(j.status==='translating' && j.completed_chapters && j.total_chapters)
      meta=j.completed_chapters+'/'+j.total_chapters+' 章';
    if(j.status==='complete' && j.completed_chapters)
      meta=j.completed_chapters+' 章';
    h+='<div class="job-item'+(isActive?' active':'')+'" data-job-id="'+j.job_id+'">';
    h+='<span class="job-item-icon">'+icon+'</span>';
    h+='<div class="job-item-info">';
    h+='<div class="job-item-filename">'+esc(j.filename)+'</div>';
    h+='<div class="job-item-meta">'+meta+'</div>';
    h+='</div>';
    h+='<span class="status-badge '+j.status+'">'+statusLabel+'</span>';
    h+='</div>';
  });

  jobList.innerHTML=h;

  // Click handlers for project group headers (expand/collapse)
  $$('.project-group-header').forEach(el=>{
    el.addEventListener('click',()=>{
      const pid=el.dataset.projectId;
      expandedProjects[pid]=!expandedProjects[pid];
      const body=$('#proj-body-'+pid);
      const toggle=el.querySelector('.project-group-toggle');
      if(expandedProjects[pid]){
        body.classList.add('open');
        toggle.classList.add('open');
      }else{
        body.classList.remove('open');
        toggle.classList.remove('open');
      }
    });
  });

  // Click handlers for job items
  $$('.job-item').forEach(el=>{
    el.addEventListener('click',(e)=>{
      e.stopPropagation();
      const jid=el.dataset.jobId;
      openJobDetail(jid);
    });
  });
}

function formatDate(s){
  if(!s)return '';
  try{
    const d=new Date(s.replace(' ','T')+'Z');
    const now=new Date();
    const diff=now-d;
    if(diff<6e4)return '刚刚';
    if(diff<36e5)return Math.floor(diff/6e4)+' 分钟前';
    if(diff<864e5)return Math.floor(diff/36e5)+' 小时前';
    return s.slice(0,10);
  }catch(e){return s.slice(0,10)}
}

function highlightSidebarItem(jobId){
  selectedJobId=jobId;
  $$('.job-item').forEach(el=>el.classList.toggle('active',el.dataset.jobId===jobId));
}

// ── Open job detail view ──
async function openJobDetail(jobId){
  // Stop any active polling on the new-translation view
  cancelRequested=true;
  if(pollTimer)clearTimeout(pollTimer);
  activeJobId=null;activeForm=null;

  selectedJobId=jobId;
  highlightSidebarItem(jobId);
  showView('job');

  // Fetch job data
  let job=null;
  try{
    const r=await fetch('/api/jobs/'+jobId);
    if(!r.ok)throw new Error('not found');
    job=await r.json();
  }catch(e){
    viewJob.innerHTML='<div class="preview-empty" style="height:300px"><div class="pe-icon">&#10060;</div><div class="pe-text">无法加载翻译记录</div></div>';
    return;
  }

  renderJobDetail(job);

  // If this job is currently translating, start polling in job-detail mode
  if(job.status==='translating' || job.status==='queued'){
    startJobDetailPolling(jobId);
  }

  // If complete, fetch and show results
  if(job.status==='complete'){
    loadJobResults(jobId, job);
  }
}

function renderJobDetail(job){
  const statusLabel={'queued':'排队中','translating':'翻译中','complete':'已完成','failed':'失败'}[job.status]||job.status;
  let extraCards='';
  if(job.status==='translating'){
    const pct=job.total_chapters?Math.round(job.completed_chapters/job.total_chapters*100):0;
    extraCards+='<div class="job-detail-card"><div class="job-detail-card-label">进度</div><div class="job-detail-card-value" id="jd-progress">'+pct+'%</div></div>';
    extraCards+='<div class="job-detail-card"><div class="job-detail-card-label">当前章节</div><div class="job-detail-card-value" id="jd-chapter" style="font-size:14px">'+(job.current_chapter_title||'—')+'</div></div>';
  }
  if(job.status==='failed' && job.error_message){
    extraCards+='<div class="job-detail-card" style="flex:2"><div class="job-detail-card-label">错误信息</div><div class="job-detail-card-value" style="font-size:13px;color:#ff3b30">'+esc(job.error_message)+'</div></div>';
  }

  let langLabel=job.target_lang;
  if(job.target_lang==='en-US')langLabel='英语';
  else if(job.target_lang==='es-ES')langLabel='西班牙语';
  else if(job.target_lang==='de')langLabel='德语';
  else if(job.target_lang==='fr')langLabel='法语';

  let h='';
  h+='<div class="job-detail-header">';
  h+='<div class="job-detail-title">'+esc(job.filename)+' <span class="status-badge '+job.status+'">'+statusLabel+'</span></div>';
  h+='<div class="job-detail-actions">';
  if(job.status==='complete')h+='<button class="btn-cancel" id="btn-save-preset" data-job-id="'+job.job_id+'" style="width:auto">保存术语表预设</button>';
  h+='<button class="btn-delete-job" id="btn-delete-job" data-job-id="'+job.job_id+'">删除</button>';
  h+='</div>';
  h+='</div>';

  h+='<div class="job-detail-cards">';
  h+='<div class="job-detail-card"><div class="job-detail-card-label">目标语言</div><div class="job-detail-card-value">'+langLabel+'</div></div>';
  h+='<div class="job-detail-card"><div class="job-detail-card-label">总章节</div><div class="job-detail-card-value">'+job.total_chapters+'</div></div>';
  if(job.glossary_count)h+='<div class="job-detail-card"><div class="job-detail-card-label">术语数</div><div class="job-detail-card-value">'+job.glossary_count+'</div></div>';
  // Show cost if token data exists
  if(job.tokens_input||job.tokens_output){
    const totalTokens=(job.tokens_input||0)+(job.tokens_output||0);
    const cost=((job.tokens_input||0)/1e6*0.14+(job.tokens_output||0)/1e6*0.28).toFixed(4);
    const kIn=Math.round((job.tokens_input||0)/1000);
    const kOut=Math.round((job.tokens_output||0)/1000);
    h+='<div class="job-detail-card" style="flex:2"><div class="job-detail-card-label">成本估算</div><div class="job-detail-card-value" style="font-size:13px">~$'+cost+' | '+totalTokens.toLocaleString()+' tokens ('+kIn+'K in / '+kOut+'K out)</div></div>';
  }
  h+='<div class="job-detail-card"><div class="job-detail-card-label">创建时间</div><div class="job-detail-card-value" style="font-size:13px">'+(job.created_at||'—')+'</div></div>';
  h+=extraCards;
  h+='</div>';

  // Progress bar for translating jobs
  if(job.status==='translating'){
    const pct=job.total_chapters?Math.round(job.completed_chapters/job.total_chapters*100):0;
    h+='<div class="progress-wrap">';
    h+='<div class="progress-bar"><div class="fill" id="jd-progress-fill" style="width:'+pct+'%"></div></div>';
    h+='<div class="progress-text" id="jd-progress-text"><span class="spinner"></span>第 '+job.completed_chapters+'/'+job.total_chapters+' 章</div>';
    h+='</div>';
  }

  // Translation output area for completed jobs
  if(job.status==='complete'){
    h+='<div class="preview-panel" id="job-preview-panel">';
    h+='<div class="preview-tabs" style="display:flex">';
    h+='<button class="preview-tab sel" data-tab="translation">译文</button>';
    h+='<button class="preview-tab" data-tab="glossary">术语表</button>';
    h+='<button class="preview-tab" data-tab="report">质量报告</button>';
    h+='<span style="flex:1"></span>';
    h+='<a class="epub-dl-btn" href="/api/epub/'+job.job_id+'" title="下载 EPUB 电子书">下载 EPUB</a>';
    h+='</div>';
    h+='<div class="preview-body show" id="job-body-translation"><div id="job-out-translation"></div></div>';
    h+='<div class="preview-body" id="job-body-glossary"><div id="job-out-glossary"></div></div>';
    h+='<div class="preview-body" id="job-body-report"><div id="job-out-report"></div></div>';
    h+='</div>';
  }

  // Loading state for queued
  if(job.status==='queued'){
    h+='<div class="preview-panel"><div class="preview-translating">';
    h+='<div class="pt-icon"><span class="spinner" style="width:24px;height:24px;border-width:3px;margin:0"></span></div>';
    h+='<div class="pt-text">等待翻译开始...</div>';
    h+='</div></div>';
  }

  viewJob.innerHTML=h;

  // Wire delete button
  const delBtn=$('#btn-delete-job');
  if(delBtn)delBtn.addEventListener('click',async()=>{
    if(!confirm('确定要删除此翻译记录吗？'))return;
    try{
      await fetch('/api/jobs/'+job.job_id,{method:'DELETE'});
      showToast('已删除');
      selectedJobId=null;
      highlightSidebarItem(null);
      showNewTranslationView();
      loadJobList();
    }catch(e){showToast('删除失败: '+e.message)}
  });

  // Wire "save as preset" button
  const savePresetBtn=$('#btn-save-preset');
  if(savePresetBtn)savePresetBtn.addEventListener('click',async()=>{
    const name=prompt('请输入预设名称（例如：斗破苍穹 术语表）');
    if(!name)return;
    try{
      const form=new FormData();
      form.append('name',name);
      form.append('description','');
      const r=await fetch('/api/presets/'+job.job_id,{method:'POST',body:form});
      if(!r.ok)throw new Error(await r.text());
      showToast('术语表已保存为预设: '+name);
      loadPresets();
    }catch(e){showToast('保存失败: '+e.message)}
  });

  // Wire tabs if complete
  if(job.status==='complete'){
    $$('#view-job .preview-tab').forEach(t=>{
      t.addEventListener('click',()=>{
        $$('#view-job .preview-tab').forEach(x=>x.classList.remove('sel'));
        t.classList.add('sel');
        $$('#view-job .preview-body').forEach(x=>x.classList.remove('show'));
        $('#job-body-'+t.dataset.tab).classList.add('show');
      });
    });
  }
}

async function loadJobResults(jobId, job){
  // Translation
  try{
    const tr=await fetch('/api/translation/'+jobId);
    const td=await tr.json();
    $('#job-out-translation').innerHTML=marked.parse(td.text||'*无内容*');
  }catch(e){/* ignore */}
  // Glossary
  try{
    const gr=await fetch('/api/glossary/'+jobId);
    const gd=await gr.json();
    if(!gd.error){
      let g='<table class="glossary-table"><thead><tr><th>中文</th><th>英文</th></tr></thead><tbody>';
      const entries=Object.entries(gd).sort();
      entries.forEach(([cn,en])=>{g+='<tr><td>'+esc(cn)+'</td><td>'+esc(en)+'</td></tr>'});
      g+='</tbody></table>';
      $('#job-out-glossary').innerHTML=g;
    }
  }catch(e){/* ignore */}
  // Report
  const glossaryCount=job.glossary_count||0;
  // Build cost line
  let costLine='';
  if(job.tokens_input||job.tokens_output){
    const totalTokens=(job.tokens_input||0)+(job.tokens_output||0);
    const cost=((job.tokens_input||0)/1e6*0.14+(job.tokens_output||0)/1e6*0.28).toFixed(2);
    const kIn=Math.round((job.tokens_input||0)/1000);
    const kOut=Math.round((job.tokens_output||0)/1000);
    costLine='<tr><td>成本估算</td><td><strong>~$'+cost+'</strong> | '+totalTokens.toLocaleString()+' tokens ('+kIn+'K in / '+kOut+'K out)</td></tr>';
  }
  $('#job-out-report').innerHTML='<h2>翻译概况</h2><table><tr><th>指标</th><th>数值</th></tr><tr><td>总章节</td><td><strong>'+job.total_chapters+'</strong></td></tr><tr><td>术语数</td><td><strong>'+glossaryCount+'</strong></td></tr><tr><td>目标语言</td><td>'+job.target_lang+'</td></tr><tr><td>完成时间</td><td>'+(job.completed_at||'—')+'</td></tr>'+costLine+'</table>';
}

// Polling while viewing a job (for in-progress jobs)
async function startJobDetailPolling(jobId){
  const poll=async()=>{
    if(currentView!=='job' || selectedJobId!==jobId)return;
    try{
      const r=await fetch('/api/jobs/'+jobId);
      if(!r.ok){pollTimer=setTimeout(poll,3000);return}
      const job=await r.json();
      if(job.status==='translating'){
        // Update progress bar
        const pct=job.total_chapters?Math.round(job.completed_chapters/job.total_chapters*100):0;
        const fill=$('#jd-progress-fill');
        if(fill)fill.style.width=pct+'%';
        const txt=$('#jd-progress-text');
        if(txt)txt.innerHTML='<span class="spinner"></span>第 '+job.completed_chapters+'/'+job.total_chapters+' 章';
        const cp=$('#jd-chapter');
        if(cp)cp.textContent=job.current_chapter_title||'—';
        const prog=$('#jd-progress');
        if(prog)prog.textContent=pct+'%';
        // Refresh sidebar
        loadJobList();
        pollTimer=setTimeout(poll,2000);
      }else if(job.status==='complete'){
        loadJobList();
        renderJobDetail(job);
        loadJobResults(jobId, job);
      }else if(job.status==='failed'){
        loadJobList();
        renderJobDetail(job);
      }else{
        pollTimer=setTimeout(poll,3000);
      }
    }catch(e){
      pollTimer=setTimeout(poll,3000);
    }
  };
  pollTimer=setTimeout(poll,1500);
}

// ── "New Translation" button ──
$('#btn-new-translation').addEventListener('click',()=>{
  if(pollTimer)clearTimeout(pollTimer);
  cancelRequested=true;
  activeJobId=null;activeForm=null;
  showNewTranslationView();
});

// ═══════════════════════════════════════════════════════════════
// NEW-TRANSLATION FORM LOGIC (same as before, plus job sidebar)
// ═══════════════════════════════════════════════════════════════

function setPreviewState(state){
  previewIdle.style.display=state==='idle'?'':'none';
  previewTranslating.style.display=state==='translating'?'':'none';
  previewError.style.display=state==='error'?'':'none';
  previewTabs.style.display=state==='complete'?'flex':'none';
}
function resetPreview(){
  setPreviewState('idle');
  $$('#preview-panel .preview-body').forEach(x=>x.classList.remove('show'));
}

function resetConfigUI(){
  startBtn.disabled=false;
  startBtn.textContent=isMultiLangs?'多语种并行翻译':'开始翻译';
  progressWrap.style.display='none';progressFill.style.width='0%';
  progressText.textContent='';
  chapterLog.style.display='none';chapterLog.innerHTML='';
  chapterEntries=[];
  progressActions.style.display='none';
  errorActions.style.display='none';
  cancelRequested=false;
  activeJobId=null;activeForm=null;
  retryCount=0;
  epubDlBtn.href='#';
}

// File handling
dropZone.addEventListener('click',()=>fileInput.click());
dropZone.addEventListener('dragover',e=>{e.preventDefault();dropZone.classList.add('drag')});
dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('drag'));
dropZone.addEventListener('drop',e=>{
  e.preventDefault();dropZone.classList.remove('drag');
  if(e.dataTransfer.files.length)setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change',()=>{if(fileInput.files.length)setFile(fileInput.files[0])});

function setFile(f){selectedFile=f;dzFilename.textContent=f.name}

// Toggle groups
$$('.toggle-group').forEach(g=>{
  g.querySelectorAll('label').forEach(l=>{
    l.addEventListener('click',()=>{
      g.querySelectorAll('label').forEach(x=>x.classList.remove('on'));
      l.classList.add('on');
    });
  });
});

// Range slider
rangeInput.addEventListener('input',()=>{rangeLabel.textContent=rangeInput.value});

// Tabs (in new-translation view)
$$('#preview-tabs .preview-tab').forEach(t=>{
  t.addEventListener('click',()=>{
    $$('#preview-panel .preview-tab').forEach(x=>x.classList.remove('sel'));
    t.classList.add('sel');
    $$('#preview-panel .preview-body').forEach(x=>x.classList.remove('show'));
    $('#body-'+t.dataset.tab).classList.add('show');
  });
});

function showToast(msg){toast.textContent=msg;toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),3000)}

// ── Per-browser job isolation ──
function getOwnJobs(){try{return JSON.parse(localStorage.getItem('westward_my_jobs')||'[]')}catch(e){return[]}}
function saveOwnJob(id){const jobs=getOwnJobs();if(!jobs.includes(id)){jobs.push(id);localStorage.setItem('westward_my_jobs',JSON.stringify(jobs))}}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

function buildForm(){
  const form=new FormData();
  form.append('file',new Blob([selectedFile.text_],{type:'text/plain'}),selectedFile.name||'novel.txt');
  if(isMultiLangs){
    const langs=[];
    $$('.lang-chip.on').forEach(c=>langs.push(c.dataset.lang));
    form.append('target_langs',langs.join(','));
  }else{
    form.append('target_lang',$('#target-lang').value);
  }
  form.append('translate_mode','flash');
  form.append('qa_interval',rangeInput.value);
  form.append('genre',$('#genre').value);
  form.append('glossary_preset',$('#glossary-preset').value);
  form.append('api_key',$('#api-key').value);
  return form;
}

// ── Multi-language toggle ──
function toggleMultiLangs(enable){
  isMultiLangs=enable;
  if(enable){
    $('#field-target-lang').style.display='none';
    $('#field-multi-langs').style.display='';
    startBtn.textContent='多语种并行翻译';
  }else{
    $('#field-target-lang').style.display='';
    $('#field-multi-langs').style.display='none';
    startBtn.textContent='开始翻译';
  }
}

function getSelectedLangs(){
  const langs=[];
  $$('.lang-chip.on').forEach(c=>langs.push(c.dataset.lang));
  return langs;
}

// Multi-lang chip click
$$('.lang-chip').forEach(chip=>{
  chip.addEventListener('click',()=>{
    chip.classList.toggle('on');
    // At least one language must be selected
    if(!$$('.lang-chip.on').length)chip.classList.add('on');
  });
});

function updateChapterLog(){
  if(!chapterEntries.length){chapterLog.style.display='none';return}
  chapterLog.style.display='block';
  let h='';
  for(let i=Math.max(0,chapterEntries.length-5);i<chapterEntries.length;i++){
    const e=chapterEntries[i];
    const icon=e.status==='done'?'<span class="ch-done">&#10003;</span>':'<span class="ch-fail">&#10007;</span>';
    h+='<div class="ch-entry">'+icon+'<span class="ch-num">#'+e.num+'</span><span class="ch-title">'+e.title+'</span></div>';
  }
  chapterLog.innerHTML=h;
  chapterLog.scrollTop=chapterLog.scrollHeight;
}

function abortTranslation(){
  cancelRequested=true;activeJobId=null;
  if(pollTimer)clearTimeout(pollTimer);
  resetConfigUI();resetPreview();
  showToast('翻译已取消');
  loadJobList();
}

function showErrorState(message,jobId,form){
  activeJobId=jobId;activeForm=form;
  progressActions.style.display='none';
  errorActions.style.display='block';
  progressText.textContent='错误: '+message;
  setPreviewState('error');
  previewErrorMsg.textContent=message;
  showToast(message);
}

async function retryTranslation(){
  if(!activeForm){return}
  retryCount++;
  errorActions.style.display='none';
  progressText.textContent='重试中...';
  setPreviewState('translating');
  doTranslate(activeForm);
}

cancelBtn.addEventListener('click',abortTranslation);
retryBtn.addEventListener('click',retryTranslation);
previewRetryBtn.addEventListener('click',retryTranslation);

// ── Core: submit & poll ──
async function doTranslate(form){
  cancelRequested=false;
  const endpoint=isMultiLangs?'/api/translate/multi':'/api/translate';
  const res=await fetch(endpoint,{method:'POST',body:form});
  if(!res.ok){
    showToast('提交失败: '+(await res.text()).slice(0,100));
    resetConfigUI();resetPreview();
    return;
  }
  const resp=await res.json();

  if(isMultiLangs){
    // Multi-language: resp is {project_id, jobs: [{lang, job_id, ...}]}
    const projectId=resp.project_id;
    const jobs=resp.jobs;
    const total=resp.total_chapters;
    jobs.forEach(j=>saveOwnJob(j.job_id));

    progressText.innerHTML='<span class="spinner"></span>多语种翻译进行中 — '+jobs.length+' 个语种';
    progressFill.style.width='10%';
    progressActions.style.display='block';
    setPreviewState('translating');
    loadJobList();

    // Poll each job's status periodically
    let allDone=false;
    const pollAll=async()=>{
      if(cancelRequested||allDone)return;
      try{
        const pr=await fetch('/api/projects/'+projectId);
        if(!pr.ok){pollTimer=setTimeout(pollAll,3000);return}
        const proj=await pr.json();
        const stats=proj.jobs.map(j=>j.target_lang+':'+j.status);
        const done=proj.jobs.filter(j=>j.status==='complete').length;
        const failed=proj.jobs.filter(j=>j.status==='failed').length;
        const pct=Math.round((done+failed)/proj.jobs.length*100);
        progressFill.style.width=pct+'%';
        progressText.innerHTML='<span class="spinner"></span>多语种翻译 — '+done+'/'+proj.jobs.length+' 完成 ('+stats.join(', ')+')';

        if(done+failed===proj.jobs.length){
          allDone=true;
          progressFill.style.width='100%';
          progressText.textContent='多语种翻译完成 — '+done+'/'+proj.jobs.length+' 成功';
          progressActions.style.display='none';errorActions.style.display='none';
          setPreviewState('complete');
          startBtn.disabled=false;startBtn.textContent='开始翻译';
          activeJobId=null;activeForm=null;
          loadJobList();
        }else{
          loadJobList();
          pollTimer=setTimeout(pollAll,2000);
        }
      }catch(e){
        pollTimer=setTimeout(pollAll,3000);
      }
    };
    pollTimer=setTimeout(pollAll,2000);
    return;
  }

  // Single-language path (existing)
  const jobId=resp.job_id;
  const total=resp.total_chapters;
  activeJobId=jobId;activeForm=form;
  saveOwnJob(jobId);

  // Refresh sidebar
  loadJobList();

  const poll=async()=>{
    if(cancelRequested)return;
    if(!activeJobId || activeJobId!==jobId)return;

    try{
      const r=await fetch('/api/translate/'+jobId);
      if(!r.ok){pollTimer=setTimeout(poll,2000);return}
      const s=await r.json();

      if(s.status==='translating'){
        const pct=Math.round(s.current/s.total*100);
        progressFill.style.width=pct+'%';
        progressText.innerHTML='<span class="spinner"></span>第 '+s.current+'/'+s.total+' 章 &mdash; '+s.chapter_title;

        if(s.current>chapterEntries.length){
          for(let c=chapterEntries.length+1;c<=s.current;c++){
            chapterEntries.push({num:c,title:c===s.current?s.chapter_title:'第'+c+'章',status:'done'});
          }
          updateChapterLog();
        }
        // Refresh sidebar for progress
        loadJobList();
        pollTimer=setTimeout(poll,1500);
      }else if(s.status==='complete'){
        progressFill.style.width='100%';progressText.textContent='翻译完成 — 共 '+total+' 章';
        chapterEntries=[];updateChapterLog();
        progressActions.style.display='none';errorActions.style.display='none';
        setPreviewState('complete');

        const tr=await fetch('/api/translation/'+jobId);
        const td=await tr.json();
        $('#out-translation').innerHTML=marked.parse(td.text||'*无内容*');

        const gr=await fetch('/api/glossary/'+jobId);
        const gd=await gr.json();
        if(!gd.error){
          let g='<table class="glossary-table"><thead><tr><th>中文</th><th>英文</th></tr></thead><tbody>';
          Object.entries(gd).sort().forEach(([cn,en])=>{g+='<tr><td>'+esc(cn)+'</td><td>'+esc(en)+'</td></tr>'});
          g+='</tbody></table>';
          $('#out-glossary').innerHTML=g;
        }
        $('#out-report').innerHTML=marked.parse('## 翻译完成\n\n| 指标 | 数值 |\n|------|------|\n| 章节数 | **'+total+'** |\n| 术语数 | **'+(Object.keys(gd).length||0)+'** |');

        epubDlBtn.href='/api/epub/'+jobId;

        $$('#preview-panel .preview-body').forEach(x=>x.classList.remove('show'));
        $('#body-translation').classList.add('show');
        $$('#preview-panel .preview-tab').forEach(x=>x.classList.remove('sel'));
        $$('#preview-panel .preview-tab')[0].classList.add('sel');

        startBtn.disabled=false;startBtn.textContent='开始翻译';
        activeJobId=null;activeForm=null;
        loadJobList();
      }else if(s.status==='error'){
        showErrorState(s.message||'翻译失败',jobId,form);
        loadJobList();
      }else{pollTimer=setTimeout(poll,2000)}
    }catch(e){
      showErrorState('网络请求失败，请检查连接后重试',jobId,form);
    }
  };
  pollTimer=setTimeout(poll,1000);
}

// ── Start translation ──
startBtn.addEventListener('click',async()=>{
  if(!selectedFile){showToast('请先选择 .txt 文件');return}
  if(isMultiLangs && getSelectedLangs().length===0){showToast('请至少选择一种语言');return}

  startBtn.disabled=true;startBtn.textContent=isMultiLangs?'多语种翻译中...':'翻译中...';
  progressWrap.style.display='block';
  progressActions.style.display='block';
  chapterEntries=[];
  updateChapterLog();
  setPreviewState('translating');

  selectedFile.text_=await selectedFile.text();
  const form=buildForm();
  doTranslate(form);
});

// ── Multi-language mode toggle button ──
$('#btn-multi-translate').addEventListener('click',()=>{
  toggleMultiLangs(!isMultiLangs);
});

// Scroll to form-left to see multi-lang UI when toggled
function showNewTranslationView(){
  selectedJobId=null;
  highlightSidebarItem(null);
  showView('new');
  resetConfigUI();
  resetPreview();
  toggleMultiLangs(false);
  loadPresets();
}

// ═══════════════════════════════════════════════════════════════
// CMS Import modal
// ═══════════════════════════════════════════════════════════════

const cmsModalOverlay=$('#cms-modal-overlay');
const cmsSourceId=$('#cms-source-id');
const cmsSourceList=$('#cms-source-list');
const cmsModalSubmit=$('#cms-modal-submit');

function openCmsModal(){
  cmsModalOverlay.classList.add('open');
  cmsSourceId.value='';
  cmsModalSubmit.disabled=true;
  loadCmsSources();
}

function closeCmsModal(){
  cmsModalOverlay.classList.remove('open');
}

$('#btn-cms-import').addEventListener('click',openCmsModal);
$('#cms-modal-cancel').addEventListener('click',closeCmsModal);
cmsModalOverlay.addEventListener('click',e=>{if(e.target===cmsModalOverlay)closeCmsModal()});

cmsSourceId.addEventListener('input',()=>{
  cmsModalSubmit.disabled=!cmsSourceId.value.trim();
  // Deselect list items when typing
  $$('.cms-source-item').forEach(x=>x.classList.remove('sel'));
});

async function loadCmsSources(){
  try{
    const r=await fetch('/api/cms/sources');
    const d=await r.json();
    if(!d.sources.length){
      cmsSourceList.innerHTML='<div class="cms-loading-sources">暂无可用来源</div>';
      return;
    }
    let h='';
    d.sources.forEach(s=>{
      h+='<div class="cms-source-item" data-source-id="'+esc(s)+'">'+esc(s)+'</div>';
    });
    cmsSourceList.innerHTML=h;

    $$('.cms-source-item').forEach(el=>{
      el.addEventListener('click',()=>{
        $$('.cms-source-item').forEach(x=>x.classList.remove('sel'));
        el.classList.add('sel');
        cmsSourceId.value=el.dataset.sourceId;
        cmsModalSubmit.disabled=false;
      });
    });
  }catch(e){
    cmsSourceList.innerHTML='<div class="cms-loading-sources">加载失败，请手动输入 source_id</div>';
  }
}

cmsModalSubmit.addEventListener('click',async()=>{
  const sourceId=cmsSourceId.value.trim();
  if(!sourceId)return;

  cmsModalSubmit.disabled=true;
  cmsModalSubmit.textContent='导入中...';

  try{
    const form=new FormData();
    form.append('source_type','file');
    form.append('source_id',sourceId);
    form.append('job_title',sourceId);

    const r=await fetch('/api/cms/import',{method:'POST',body:form});
    if(!r.ok){
      const err=await r.json();
      showToast('CMS 导入失败: '+(err.error||err.detail||'unknown'));
      cmsModalSubmit.disabled=false;
      cmsModalSubmit.textContent='导入并翻译';
      return;
    }

    const data=await r.json();
    closeCmsModal();

    // Switch to the new translation view and start polling this job
    cancelRequested=true;
    if(pollTimer)clearTimeout(pollTimer);
    showNewTranslationView();
    resetConfigUI();

    // Show progress for the CMS-imported job
    activeJobId=data.job_id;
    startBtn.disabled=true;startBtn.textContent='翻译中...';
    progressWrap.style.display='block';
    progressActions.style.display='block';
    chapterEntries=[];
    updateChapterLog();
    setPreviewState('translating');

    // Create a dummy form for retry (CMS jobs re-import on retry)
    const dummyForm=new FormData();
    dummyForm.append('source_type','file');
    dummyForm.append('source_id',sourceId);
    dummyForm.append('job_title',sourceId);
    activeForm=dummyForm;

    // Poll using the same mechanism
    loadJobList();
    doCmsPoll(data.job_id);
  }catch(e){
    showToast('CMS 导入失败: '+e.message);
    cmsModalSubmit.disabled=false;
    cmsModalSubmit.textContent='导入并翻译';
  }finally{
    cmsModalSubmit.disabled=false;
    cmsModalSubmit.textContent='导入并翻译';
  }
});

async function doCmsPoll(jobId){
  const poll=async()=>{
    if(cancelRequested)return;
    if(!activeJobId || activeJobId!==jobId)return;

    try{
      const r=await fetch('/api/translate/'+jobId);
      if(!r.ok){pollTimer=setTimeout(poll,2000);return}
      const s=await r.json();

      if(s.status==='translating'){
        const pct=Math.round(s.current/s.total*100);
        progressFill.style.width=pct+'%';
        progressText.innerHTML='<span class="spinner"></span>第 '+s.current+'/'+s.total+' 章 &mdash; '+s.chapter_title;

        if(s.current>chapterEntries.length){
          for(let c=chapterEntries.length+1;c<=s.current;c++){
            chapterEntries.push({num:c,title:c===s.current?s.chapter_title:'第'+c+'章',status:'done'});
          }
          updateChapterLog();
        }
        loadJobList();
        pollTimer=setTimeout(poll,1500);
      }else if(s.status==='complete'){
        progressFill.style.width='100%';progressText.textContent='翻译完成 — 共 '+s.total+' 章';
        chapterEntries=[];updateChapterLog();
        progressActions.style.display='none';errorActions.style.display='none';
        setPreviewState('complete');

        const tr=await fetch('/api/translation/'+jobId);
        const td=await tr.json();
        $('#out-translation').innerHTML=marked.parse(td.text||'*无内容*');

        const gr=await fetch('/api/glossary/'+jobId);
        const gd=await gr.json();
        if(!gd.error){
          let g='<table class="glossary-table"><thead><tr><th>中文</th><th>英文</th></tr></thead><tbody>';
          Object.entries(gd).sort().forEach(([cn,en])=>{g+='<tr><td>'+esc(cn)+'</td><td>'+esc(en)+'</td></tr>'});
          g+='</tbody></table>';
          $('#out-glossary').innerHTML=g;
        }
        $('#out-report').innerHTML=marked.parse('## 翻译完成\n\n| 指标 | 数值 |\n|------|------|\n| 章节数 | **'+s.total+'** |\n| 术语数 | **'+(Object.keys(gd).length||0)+'** |');

        epubDlBtn.href='/api/epub/'+jobId;

        $$('#preview-panel .preview-body').forEach(x=>x.classList.remove('show'));
        $('#body-translation').classList.add('show');
        $$('#preview-panel .preview-tab').forEach(x=>x.classList.remove('sel'));
        $$('#preview-panel .preview-tab')[0].classList.add('sel');

        startBtn.disabled=false;startBtn.textContent='开始翻译';
        activeJobId=null;activeForm=null;
        loadJobList();
      }else if(s.status==='error'){
        showErrorState(s.message||'翻译失败',jobId,null);
        loadJobList();
      }else{pollTimer=setTimeout(poll,2000)}
    }catch(e){
      showErrorState('网络请求失败，请检查连接后重试',jobId,null);
    }
  };
  pollTimer=setTimeout(poll,1000);
}

// ── Init ──
loadJobList();
showNewTranslationView();
</script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# REVIEW PAGE — glossary curation tool
# ═══════════════════════════════════════════════════════════════

REVIEW_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>术语审核 / Glossary Review — Westward Echo</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:16px;-webkit-font-smoothing:antialiased}
body{
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:14px;line-height:1.6;color:#1c1c1e;background:#f5f5f7;
}
::selection{background:rgba(0,102,204,.18)}

.app{max-width:720px;margin:0 auto;padding:0 24px}

/* Nav */
.nav{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 0;margin-bottom:8px;border-bottom:1px solid #e5e5ea;
}
.nav-brand{font-size:14px;font-weight:600;color:#1c1c1e}
.nav-back{font-size:12px;color:#0071e3;text-decoration:none}
.nav-back:hover{text-decoration:underline}

/* Header */
.header{text-align:center;padding:32px 0 28px}
.header h1{font-size:24px;font-weight:700;color:#1c1c1e;margin-bottom:4px}
.header p{font-size:13px;color:#8e8e93}
.header .stats{font-size:12px;color:#aeaeb2;margin-top:6px}

/* Filter bar */
.filter-bar{display:flex;gap:4px;margin-bottom:24px}
.filter-btn{
  flex:1;padding:9px 0;font-size:13px;font-weight:500;color:#6e6e73;
  background:#fff;border:1px solid #e5e5ea;border-radius:8px;cursor:pointer;
  transition:all .15s;text-align:center;
}
.filter-btn:hover{background:#f5f5f7}
.filter-btn.active{background:#0071e3;color:#fff;border-color:#0071e3;font-weight:600}

/* Term cards */
.term-list{display:flex;flex-direction:column;gap:10px;margin-bottom:40px}
.term-card{
  background:#fff;border-radius:12px;padding:18px 20px;
  box-shadow:0 1px 3px rgba(0,0,0,.04);transition:all .3s;
}
.term-card.fading{opacity:0;transform:translateX(40px)}

.term-row{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
.term-cn{font-size:16px;font-weight:700;color:#1c1c1e}
.term-arrow{color:#c7c7cc;font-size:14px}
.term-en{font-size:15px;color:#0071e3;font-weight:500}

.term-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.badge{
  font-size:10px;font-weight:600;padding:2px 8px;border-radius:10px;
  text-transform:uppercase;letter-spacing:.3px;
}
.badge-character{background:rgba(255,59,48,.1);color:#ff3b30}
.badge-location{background:rgba(52,199,89,.1);color:#34c759}
.badge-technique{background:rgba(175,82,222,.1);color:#af52de}
.badge-culture{background:rgba(0,122,255,.1);color:#007aff}
.badge-item{background:rgba(255,149,0,.1);color:#ff9500}
.badge-era{background:rgba(142,142,147,.15);color:#8e8e93}
.badge-status-pending{background:rgba(255,149,0,.12);color:#ff9500;font-weight:600}
.badge-status-confirmed{background:rgba(52,199,89,.12);color:#34c759;font-weight:600}

.term-context{font-size:12px;color:#8e8e93;line-height:1.5;margin-bottom:4px;font-style:italic}
.term-chapter{font-size:11px;color:#aeaeb2}

.term-actions{margin-top:10px;display:flex;gap:8px}
.btn-confirm,.btn-reject{
  padding:6px 18px;font-size:12px;font-weight:600;border:none;border-radius:8px;
  cursor:pointer;transition:all .15s;
}
.btn-confirm{background:#34c759;color:#fff}
.btn-confirm:hover{background:#30b350;transform:translateY(-1px);box-shadow:0 3px 8px rgba(52,199,89,.3)}
.btn-confirm:active{transform:scale(.97)}
.btn-reject{background:#ff3b30;color:#fff}
.btn-reject:hover{background:#e6352b;transform:translateY(-1px);box-shadow:0 3px 8px rgba(255,59,48,.3)}
.btn-reject:active{transform:scale(.97)}

/* Empty state */
.empty-state{text-align:center;padding:60px 20px;color:#aeaeb2}
.empty-icon{font-size:40px;margin-bottom:10px;opacity:.5}
.empty-text{font-size:14px}

/* Loading */
.loading{text-align:center;padding:40px;color:#8e8e93}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid #e5e5ea;border-top-color:#0071e3;border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* Toast */
.toast{
  position:fixed;top:24px;left:50%;transform:translateX(-50%);
  padding:10px 22px;border-radius:10px;font-size:13px;font-weight:500;
  color:#fff;background:#1c1c1e;box-shadow:0 8px 24px rgba(0,0,0,.15);
  z-index:999;opacity:0;pointer-events:none;transition:opacity .3s;
}
.toast.show{opacity:1}

/* Footer */
.ft{text-align:center;padding:40px 0;font-size:12px;color:#aeaeb2}
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<div class="app">
  <nav class="nav">
    <div class="nav-brand">西渡 / Westward Echo</div>
    <a class="nav-back" href="/">&larr; 返回翻译</a>
  </nav>

  <div class="header">
    <h1>术语审核 / Glossary Review</h1>
    <p>确认或拒绝机器提取的术语译文，确保全本术语统一</p>
    <div class="stats" id="stats"></div>
  </div>

  <div class="filter-bar" id="filter-bar">
    <button class="filter-btn active" data-filter="all">全部</button>
    <button class="filter-btn" data-filter="pending_review">待审核</button>
    <button class="filter-btn" data-filter="confirmed">已确认</button>
  </div>

  <div class="loading" id="loading"><div class="spinner"></div></div>

  <div class="term-list" id="term-list"></div>
  <div class="empty-state" id="empty" style="display:none">
    <div class="empty-icon">&#128076;</div>
    <div class="empty-text">暂无术语数据</div>
  </div>

  <footer class="ft">Westward Echo &middot; 术语审核</footer>
</div>

<script>
const termList=document.getElementById('term-list');
const loading=document.getElementById('loading');
const empty=document.getElementById('empty');
const stats=document.getElementById('stats');
const toast=document.getElementById('toast');

let allTerms=[];
let currentFilter='all';

// Category labels
const catLabels={'character':'角色','location':'地点','technique':'功法','culture':'文化','item':'物品','era':'年代'};

function showToast(msg){
  toast.textContent=msg;toast.classList.add('show');
  setTimeout(()=>toast.classList.remove('show'),2000);
}

async function fetchTerms(){
  loading.style.display='block';
  termList.innerHTML='';empty.style.display='none';
  try{
    const url=currentFilter==='all'?'/api/review/terms':'/api/review/terms?status='+currentFilter;
    const r=await fetch(url);
    const d=await r.json();
    allTerms=d.terms;
    stats.textContent=d.count+' 个术语';
    renderTerms();
  }catch(e){
    showToast('加载失败: '+e.message);
  }
  loading.style.display='none';
}

function renderTerms(){
  if(!allTerms.length){empty.style.display='block';termList.innerHTML='';return}
  empty.style.display='none';
  let h='';
  allTerms.forEach(t=>{
    const catLabel=catLabels[t.category]||t.category;
    const isPending=t.status==='pending_review';
    const statusLabel=isPending?'待审核':'已确认';
    const statusClass=isPending?'badge-status-pending':'badge-status-confirmed';
    h+='<div class="term-card" id="card-'+encodeURIComponent(t.term_cn)+'">';
    h+='<div class="term-row">';
    h+='<span class="term-cn">'+esc(t.term_cn)+'</span>';
    h+='<span class="term-arrow">&rarr;</span>';
    h+='<span class="term-en">'+esc(t.term_en)+'</span>';
    h+='</div>';
    h+='<div class="term-meta">';
    h+='<span class="badge badge-'+t.category+'">'+catLabel+'</span>';
    h+='<span class="badge '+statusClass+'">'+statusLabel+'</span>';
    if(t.chapter_first_seen)h+='<span class="term-chapter">第'+t.chapter_first_seen+'章</span>';
    h+='</div>';
    if(t.context)h+='<div class="term-context">&ldquo;'+esc(t.context)+'&rdquo;</div>';
    if(isPending){
      h+='<div class="term-actions">';
      h+='<button class="btn-confirm" onclick="confirmTerm(\''+escAttr(t.term_cn)+'\')">&#9989; 确认</button>';
      h+='<button class="btn-reject" onclick="rejectTerm(\''+escAttr(t.term_cn)+'\')">&#10060; 拒绝</button>';
      h+='</div>';
    }
    h+='</div>';
  });
  termList.innerHTML=h;
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function escAttr(s){return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/>/g,'&gt;')}

async function confirmTerm(cn){
  try{
    const r=await fetch('/api/review/terms/'+encodeURIComponent(cn)+'/confirm',{method:'POST'});
    if(!r.ok)throw new Error(await r.text());
    showToast('已确认: '+cn);
    const card=document.getElementById('card-'+encodeURIComponent(cn));
    if(card){card.classList.add('fading');setTimeout(()=>card.remove(),350)}
  }catch(e){showToast('操作失败: '+e.message)}
}

async function rejectTerm(cn){
  if(!confirm('确定要删除术语 "'+cn+'" 吗？此操作不可撤销。'))return;
  try{
    const r=await fetch('/api/review/terms/'+encodeURIComponent(cn)+'/reject',{method:'POST'});
    if(!r.ok)throw new Error(await r.text());
    showToast('已删除: '+cn);
    const card=document.getElementById('card-'+encodeURIComponent(cn));
    if(card){card.classList.add('fading');setTimeout(()=>card.remove(),350)}
  }catch(e){showToast('操作失败: '+e.message)}
}

// Filter buttons
document.querySelectorAll('.filter-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter=btn.dataset.filter;
    fetchTerms();
  });
});

// Load on start
fetchTerms();
</script>
</body>
</html>"""


# ── Routes ───────────────────────────────────────────────

# Routes served by src/main.py (not a separate FastAPI app)
