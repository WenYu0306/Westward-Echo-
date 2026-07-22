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
   LAYOUT — centered, breathing
   ═══════════════════════════════════════════════════════════════ */
.app{max-width:920px;margin:0 auto;padding:0 24px}

/* ═══════════════════════════════════════════════════════════════
   NAVBAR — subtle top bar
   ═══════════════════════════════════════════════════════════════ */
.nav{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 0;margin-bottom:8px;
}
.nav-brand{font-family:"Noto Serif SC",serif;font-size:18px;font-weight:700;letter-spacing:1px;color:#1c1c1e}
.nav-brand span{font-weight:400;color:#8e8e93;margin:0 3px;font-family:Inter,sans-serif;font-size:14px}
.nav-badge{
  font-size:11px;font-weight:500;color:#0071e3;
  padding:4px 10px;border-radius:12px;background:rgba(0,113,227,.08);
}

/* ═══════════════════════════════════════════════════════════════
   HERO
   ═══════════════════════════════════════════════════════════════ */
.hero{
  text-align:center;padding:48px 0 56px;
  background:linear-gradient(180deg,transparent 0%,rgba(0,113,227,.03) 100%);
  border-radius:20px;margin-bottom:40px;
}
.hero h1{
  font-family:"Noto Serif SC",serif;font-size:40px;font-weight:700;
  letter-spacing:3px;color:#1c1c1e;margin-bottom:8px;
}
.hero h1 .slash{font-weight:300;color:#c7c7cc;margin:0 6px;font-family:Inter,sans-serif}
.hero h1 .en{font-family:Inter,sans-serif;font-weight:300;font-size:36px;letter-spacing:-.5px;color:#8e8e93}
.hero p{font-size:15px;color:#6e6e73;max-width:460px;margin:12px auto 0;line-height:1.5}

/* ═══════════════════════════════════════════════════════════════
   TWO-COLUMN LAYOUT — config left, preview right
   ═══════════════════════════════════════════════════════════════ */
.main-layout{display:flex;gap:32px;align-items:flex-start}
.main-left{flex:0 0 340px}
.main-right{flex:1;min-width:0}

@media(max-width:780px){
  .main-layout{flex-direction:column}
  .main-left{flex:1 1 auto;width:100%}
}

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

/* Chapter log scrollbar */
.chapter-log::-webkit-scrollbar{width:4px}
.chapter-log::-webkit-scrollbar-track{background:transparent}
.chapter-log::-webkit-scrollbar-thumb{background:#e5e5ea;border-radius:2px}

/* ═══════════════════════════════════════════════════════════════
   PREVIEW PANEL — the hero of the page
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
.preview-tabs{display:flex;border-bottom:1px solid #f0f0f5;padding:0 20px}
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

/* ═══════════════════════════════════════════════════════════════
   TOAST — for errors
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

/* ═══════════════════════════════════════════════════════════════
   FOOTER
   ═══════════════════════════════════════════════════════════════ */
.ft{text-align:center;padding:60px 0 40px;font-size:12px;color:#aeaeb2}
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<div class="app">

  <!-- Nav -->
  <nav class="nav">
    <div class="nav-brand">西渡 <span>/</span> Westward Echo</div>
    <div class="nav-badge">LangGraph + DeepSeek V4</div>
  </nav>

  <!-- Hero -->
  <header class="hero">
    <h1>西渡<span class="slash"> / </span><span class="en">Westward Echo</span></h1>
    <p>AI 驱动的网文翻译引擎 &mdash; 术语全本统一，文化自适应适配，覆盖英语、西班牙语、阿拉伯语</p>
  </header>

  <!-- Main: config left + preview right -->
  <div class="main-layout">

    <!-- ==== LEFT: CONFIG ==== -->
    <div class="main-left">

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

        <div class="field">
          <label>目标语言</label>
          <select id="target-lang">
            <option value="en-US">English（英语）</option>
            <option value="es-ES">Espa&ntilde;ol（西班牙语）</option>
            <option value="ar-SA">&#1575;&#1604;&#1593;&#1585;&#1576;&#1610;&#1577;（阿拉伯语）</option>
          </select>
        </div>

        <div class="field">
          <label>翻译模型</label>
          <div class="toggle-group" id="tgl-model">
            <label class="on"><input type="radio" name="model" value="flash" checked>V4 Flash</label>
            <label><input type="radio" name="model" value="pro">V4 Pro</label>
          </div>
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

    <!-- ==== RIGHT: PREVIEW ==== -->
    <div class="main-right">
      <div class="preview-panel" id="preview-panel">

        <!-- Idle state -->
        <div class="preview-empty" id="preview-idle">
          <div class="pe-icon">&#128214;</div>
          <div class="pe-text">上传小说开始翻译</div>
        </div>

        <!-- Translating state -->
        <div class="preview-translating" id="preview-translating" style="display:none">
          <div class="pt-icon"><span class="spinner" style="width:24px;height:24px;border-width:3px;margin:0"></span></div>
          <div class="pt-text">翻译进行中...</div>
        </div>

        <!-- Error state -->
        <div class="preview-error" id="preview-error" style="display:none">
          <div class="pe-icon">&#9888;</div>
          <div class="pe-text">翻译失败</div>
          <div class="pe-detail" id="preview-error-msg"></div>
          <button class="pe-retry-btn" id="preview-retry-btn">重试</button>
        </div>

        <!-- Tabs (hidden until done) -->
        <div class="preview-tabs" id="preview-tabs" style="display:none">
          <button class="preview-tab sel" data-tab="translation">译文</button>
          <button class="preview-tab" data-tab="glossary">术语表</button>
          <button class="preview-tab" data-tab="report">质量报告</button>
        </div>

        <div class="preview-body" id="body-translation"><div id="out-translation"></div></div>
        <div class="preview-body" id="body-glossary"><div id="out-glossary"></div></div>
        <div class="preview-body" id="body-report"><div id="out-report"></div></div>

      </div>
    </div>

  </div>

  <footer class="ft">Westward Echo &middot; LangGraph + DeepSeek V4</footer>
</div>

<script>
const $=s=>document.querySelector(s),$$=s=>document.querySelectorAll(s);

// elements
const dropZone=$('#drop-zone'),fileInput=$('#file-input'),dzFilename=$('#dz-filename');
const startBtn=$('#start-btn'),progressWrap=$('#progress-wrap'),progressFill=$('#progress-fill'),progressText=$('#progress-text');
const chapterLog=$('#chapter-log'),progressActions=$('#progress-actions'),errorActions=$('#error-actions');
const cancelBtn=$('#cancel-btn'),retryBtn=$('#retry-btn');
const previewIdle=$('#preview-idle'),previewTranslating=$('#preview-translating'),previewError=$('#preview-error'),previewTabs=$('#preview-tabs');
const previewErrorMsg=$('#preview-error-msg'),previewRetryBtn=$('#preview-retry-btn');
const rangeInput=$('#qa-interval'),rangeLabel=$('#range-label');
const toast=$('#toast');

let selectedFile=null;
let activeJobId=null;       // current job id, null when idle
let activeForm=null;        // saved FormData for retry
let cancelRequested=false;  // flag to break poll loop
let chapterEntries=[];      // [{num,title,status:'done'|'fail'}]
let retryCount=0;

// ── visual state machine for preview panel ──
function setPreviewState(state){
  previewIdle.style.display=state==='idle'?'':'none';
  previewTranslating.style.display=state==='translating'?'':'none';
  previewError.style.display=state==='error'?'':'none';
  previewTabs.style.display=state==='complete'?'flex':'none';
}
function resetPreview(){
  setPreviewState('idle');
  $$('.preview-body').forEach(x=>x.classList.remove('show'));
}

// ── reset UI to config mode ──
function resetConfigUI(){
  startBtn.disabled=false;startBtn.textContent='开始翻译';
  progressWrap.style.display='none';progressFill.style.width='0%';
  progressText.textContent='';
  chapterLog.style.display='none';chapterLog.innerHTML='';
  chapterEntries=[];
  progressActions.style.display='none';
  errorActions.style.display='none';
  cancelRequested=false;
  activeJobId=null;activeForm=null;
  retryCount=0;
}

// ── file handling ──
dropZone.addEventListener('click',()=>fileInput.click());
dropZone.addEventListener('dragover',e=>{e.preventDefault();dropZone.classList.add('drag')});
dropZone.addEventListener('dragleave',()=>dropZone.classList.remove('drag'));
dropZone.addEventListener('drop',e=>{
  e.preventDefault();dropZone.classList.remove('drag');
  if(e.dataTransfer.files.length)setFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change',()=>{if(fileInput.files.length)setFile(fileInput.files[0])});

function setFile(f){selectedFile=f;dzFilename.textContent=f.name}

// ── toggle groups ──
$$('.toggle-group').forEach(g=>{
  g.querySelectorAll('label').forEach(l=>{
    l.addEventListener('click',()=>{
      g.querySelectorAll('label').forEach(x=>x.classList.remove('on'));
      l.classList.add('on');
    });
  });
});

// ── range slider ──
rangeInput.addEventListener('input',()=>{rangeLabel.textContent=rangeInput.value});

// ── tabs ──
$$('.preview-tab').forEach(t=>{
  t.addEventListener('click',()=>{
    $$('.preview-tab').forEach(x=>x.classList.remove('sel'));
    t.classList.add('sel');
    $$('.preview-body').forEach(x=>x.classList.remove('show'));
    $('#body-'+t.dataset.tab).classList.add('show');
  });
});

// ── toast ──
function showToast(msg){toast.textContent=msg;toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),3000)}

// ── build form data from current settings ──
function buildForm(){
  const form=new FormData();
  form.append('file',new Blob([selectedFile.text_],{type:'text/plain'}),selectedFile.name||'novel.txt');
  form.append('target_lang',$('#target-lang').value);
  form.append('translate_mode',document.querySelector('input[name="model"]:checked').value);
  form.append('qa_interval',rangeInput.value);
  return form;
}

// ── update chapter log (last 5 entries) ──
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

// ── cancel translation ──
function abortTranslation(){
  cancelRequested=true;activeJobId=null;
  resetConfigUI();resetPreview();
  showToast('翻译已取消');
}

// ── show error state ──
function showErrorState(message,jobId,form){
  activeJobId=jobId;activeForm=form;
  progressActions.style.display='none';
  errorActions.style.display='block';
  progressText.textContent='错误: '+message;
  setPreviewState('error');
  previewErrorMsg.textContent=message;
  showToast(message);
}

// ── retry translation ──
async function retryTranslation(){
  if(!activeForm){return}
  retryCount++;
  errorActions.style.display='none';
  progressText.textContent='重试中...';
  setPreviewState('translating');
  doTranslate(activeForm);
}

// cancel button
cancelBtn.addEventListener('click',abortTranslation);

// retry buttons
retryBtn.addEventListener('click',retryTranslation);
previewRetryBtn.addEventListener('click',retryTranslation);

// ── core: submit & poll ──
async function doTranslate(form){
  // Submit translation job
  const res=await fetch('/api/translate',{method:'POST',body:form});
  if(!res.ok){
    showToast('提交失败: '+(await res.text()).slice(0,100));
    resetConfigUI();resetPreview();
    return;
  }
  const job=await res.json();
  const jobId=job.job_id;
  const total=job.total_chapters;
  activeJobId=jobId;activeForm=form;

  // Poll for progress
  const poll=async()=>{
    if(cancelRequested)return;
    if(!activeJobId || activeJobId!==jobId)return;

    try{
      const r=await fetch('/api/translate/'+jobId);
      if(!r.ok){setTimeout(poll,2000);return}
      const s=await r.json();

      if(s.status==='translating'){
        const pct=Math.round(s.current/s.total*100);
        progressFill.style.width=pct+'%';
        progressText.innerHTML='<span class="spinner"></span>第 '+s.current+'/'+s.total+' 章 &mdash; '+s.chapter_title;

        // Track chapter progress
        if(s.current>chapterEntries.length){
          for(let c=chapterEntries.length+1;c<=s.current;c++){
            chapterEntries.push({num:c,title:c===s.current?s.chapter_title:'第'+c+'章',status:'done'});
          }
          updateChapterLog();
        }

        setTimeout(poll,1500);
      }else if(s.status==='complete'){
        progressFill.style.width='100%';progressText.textContent='翻译完成 — 共 '+total+' 章';
        chapterEntries=[];updateChapterLog();
        progressActions.style.display='none';errorActions.style.display='none';
        setPreviewState('complete');

        // Fetch translation
        const tr=await fetch('/api/translation/'+jobId);
        const td=await tr.json();
        $('#out-translation').innerHTML=marked.parse(td.text||'*无内容*');

        // Fetch glossary
        const gr=await fetch('/api/glossary/'+jobId);
        const gd=await gr.json();
        if(!gd.error){
          let g='<table class="glossary-table"><thead><tr><th>中文</th><th>英文</th></tr></thead><tbody>';
          Object.entries(gd).sort().forEach(([cn,en])=>{g+='<tr><td>'+cn+'</td><td>'+en+'</td></tr>'});
          g+='</tbody></table>';
          $('#out-glossary').innerHTML=g;
        }

        $('#out-report').innerHTML=marked.parse('## 翻译完成\n\n| 指标 | 数值 |\n|------|------|\n| 章节数 | **'+total+'** |\n| 术语数 | **'+(Object.keys(gd).length||0)+'** |');

        // Show results
        $$('.preview-body').forEach(x=>x.classList.remove('show'));
        $('#body-translation').classList.add('show');
        $$('.preview-tab').forEach(x=>x.classList.remove('sel'));
        $$('.preview-tab')[0].classList.add('sel');

        startBtn.disabled=false;startBtn.textContent='开始翻译';
        activeJobId=null;activeForm=null;
      }else if(s.status==='error'){
        showErrorState(s.message||'翻译失败',jobId,form);
      }else{setTimeout(poll,2000)}
    }catch(e){
      // Network error — show error state with retry
      showErrorState('网络请求失败，请检查连接后重试',jobId,form);
    }
  };
  setTimeout(poll,1000);
}

// ── start translation ──
startBtn.addEventListener('click',async()=>{
  if(!selectedFile){showToast('请先选择 .txt 文件');return}

  startBtn.disabled=true;startBtn.textContent='翻译中...';
  progressWrap.style.display='block';
  progressActions.style.display='block';
  chapterEntries=[];
  updateChapterLog();
  setPreviewState('translating');

  // Cache file text for retry
  selectedFile.text_=await selectedFile.text();
  const form=buildForm();

  doTranslate(form);
});
</script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</body>
</html>"""


# ── Routes ───────────────────────────────────────────────

# Routes served by src/main.py (not a separate FastAPI app)
