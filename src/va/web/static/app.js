/* va web UI — vanilla JS, no build step.
 *
 * Player selection per catalog row:
 *   youtube      -> IFrame API embed (source_key IS the 11-char YouTube id)
 *   has_media    -> <video src=/api/media/{id}> (server's ingested copy)
 *   http(s) uri  -> <video src={source_uri}> (direct media URL, best-effort)
 * Clicking any search hit seeks the player (switching video first if needed).
 */

const $ = (id) => document.getElementById(id);

let videos = [];        // catalog rows from /api/videos
let selected = null;    // currently selected row
let ytPlayer = null, ytReady = false;
let html5Video = null;
let pendingSeek = null; // seconds to seek once the player is ready

// --- api ---------------------------------------------------------------

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const body = await r.text();
    let msg = body;
    try { msg = JSON.parse(body).detail || body; } catch { /* not JSON */ }
    throw new Error(`${r.status} ${msg}`);
  }
  return r.json();
}

// --- video list / dropdown ----------------------------------------------

async function loadVideos(selectId = null) {
  videos = await api('/api/videos');
  const sel = $('video-select');
  const keep = selectId || sel.value;
  sel.innerHTML = '<option value="">— select a video —</option>';
  for (const v of videos) {
    const o = document.createElement('option');
    o.value = v.id;
    const name = v.title || v.source_key;
    o.textContent = `${name} — ${v.source_uri} [${v.ingest_status}]`;
    sel.appendChild(o);
  }
  if (keep && videos.some((v) => v.id === keep)) {
    sel.value = keep;
    if (selectId || !selected || selected.id !== keep) selectVideo(keep);
  }
}

function selectVideo(id) {
  selected = videos.find((v) => v.id === id) || null;
  renderPlayer();
}

// --- player --------------------------------------------------------------

function renderPlayer() {
  const host = $('player-host');
  ytPlayer = null; ytReady = false; html5Video = null;
  host.innerHTML = '';
  if (!selected) {
    host.innerHTML = '<p class="placeholder">Select or ingest a video</p>';
    return;
  }
  if (selected.source_type === 'youtube') {
    const div = document.createElement('div');
    div.id = 'yt-player';
    host.appendChild(div);
    ensureYT(() => {
      ytPlayer = new YT.Player('yt-player', {
        videoId: selected.source_key,
        playerVars: { rel: 0 },
        events: {
          onReady: () => {
            ytReady = true;
            if (pendingSeek != null) { doSeek(pendingSeek); pendingSeek = null; }
          },
        },
      });
    });
  } else if (selected.has_media) {
    html5Video = document.createElement('video');
    html5Video.controls = true;
    html5Video.src = `/api/media/${selected.id}`;
    host.appendChild(html5Video);
    applyPendingSeekHtml5();
  } else if (/^https?:/i.test(selected.source_uri)) {
    html5Video = document.createElement('video');
    html5Video.controls = true;
    html5Video.src = selected.source_uri;
    host.appendChild(html5Video);
    applyPendingSeekHtml5();
  } else {
    host.innerHTML = '<p class="placeholder">No playable media for this video</p>';
  }
}

function applyPendingSeekHtml5() {
  if (pendingSeek == null || !html5Video) return;
  const t = pendingSeek;
  pendingSeek = null;
  html5Video.addEventListener('loadedmetadata', () => {
    html5Video.currentTime = t;
    html5Video.play();
  }, { once: true });
}

let ytApiRequested = false;
const ytApiCallbacks = [];
function ensureYT(cb) {
  if (window.YT && window.YT.Player) return cb();
  ytApiCallbacks.push(cb);
  if (!ytApiRequested) {
    ytApiRequested = true;
    window.onYouTubeIframeAPIReady = () => ytApiCallbacks.splice(0).forEach((f) => f());
    const s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(s);
  }
}

function doSeek(t) {
  if (ytPlayer) {
    if (ytReady) { ytPlayer.seekTo(t, true); ytPlayer.playVideo(); }
    else pendingSeek = t;
  } else if (html5Video) {
    html5Video.currentTime = t;
    html5Video.play();
  }
}

function jumpTo(videoId, t) {
  if (!selected || selected.id !== videoId) {
    pendingSeek = t;
    $('video-select').value = videoId;
    selectVideo(videoId); // consumes pendingSeek once the new player is ready
  } else {
    doSeek(t);
  }
}

// --- ingest --------------------------------------------------------------

async function submitIngest() {
  const uri = $('uri').value.trim();
  if (!uri) return;
  const pill = $('job-status');
  pill.classList.remove('hidden');
  pill.className = 'pill queued';
  pill.textContent = 'submitting…';
  try {
    const { job_id } = await api('/api/videos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uri }),
    });
    pollJob(job_id);
  } catch (e) {
    pill.className = 'pill failed';
    pill.textContent = `submit failed: ${e.message}`;
  }
}

function pollJob(jobId) {
  const pill = $('job-status');
  const timer = setInterval(async () => {
    let j;
    try { j = await api(`/api/jobs/${jobId}`); }
    catch (e) { clearInterval(timer); pill.className = 'pill failed'; pill.textContent = e.message; return; }
    pill.className = `pill ${j.state}`;
    if (j.state === 'failed') {
      pill.textContent = `failed: ${j.error}`;
      clearInterval(timer);
      loadVideos(); // the catalog row (status=failed) still appears in the list
    } else if (j.state === 'done') {
      const r = j.result || {};
      pill.textContent = r.deduped
        ? 'done (already ingested)'
        : `done — ${r.frames_indexed} frames, ${r.segments} segments, `
          + `${r.transcript_lines} transcript lines, ${r.detections} detections`;
      clearInterval(timer);
      loadVideos(j.video_id); // refresh dropdown + auto-select the new video
    } else {
      pill.textContent = j.state;
    }
  }, 2000);
}

// --- search ----------------------------------------------------------------

const fmtT = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

async function runSearch() {
  const q = $('query').value.trim();
  if (!q) return;
  const k = parseInt($('k').value, 10) || 5;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}&k=${k}`);
  $('results').classList.remove('hidden');
  for (const mode of ['visual', 'caption', 'transcript', 'objects']) {
    renderColumn(mode, res[mode]);
  }
}

function renderColumn(mode, data) {
  const ul = document.querySelector(`#col-${mode} ul`);
  ul.innerHTML = '';
  if (!data.hits.length) {
    const li = document.createElement('li');
    li.className = 'note';
    li.textContent = data.note || 'no hits';
    ul.appendChild(li);
    return;
  }
  for (const h of data.hits) {
    const li = document.createElement('li');
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = h.score.toFixed(3);
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = fmtT(h.t);
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = h.label;
    li.append(score, t, label);
    li.onclick = () => jumpTo(h.video_id, h.t);
    ul.appendChild(li);
  }
}

// --- ask (Role 11) -----------------------------------------------------------

// Escape, then turn the rendered answer's markdown links ([m:ss](https://...))
// into anchors. External links (YouTube &t= deep links) open in a new tab.
function renderAnswerHtml(text) {
  const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return esc.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

// Asks run on a server-side job queue (a deep-scan ask can take minutes):
// submit returns an ask_id immediately, then we poll like the ingest box does.
async function runAsk() {
  const q = $('ask-input').value.trim();
  if (!q) return;
  const btn = $('ask-btn');
  if (btn.disabled) return; // ask in flight — Enter key must not re-trigger
  const pill = $('ask-status');
  btn.disabled = true;
  pill.classList.remove('hidden');
  pill.className = 'pill running';
  pill.textContent = 'submitting…';
  let askId;
  try {
    ({ ask_id: askId } = await api('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, k: parseInt($('k').value, 10) || 5 }),
    }));
  } catch (e) {
    pill.className = 'pill failed';
    pill.textContent = `ask failed: ${e.message}`;
    btn.disabled = false;
    return;
  }
  pollAsk(askId, pill, btn);
}

function pollAsk(askId, pill, btn) {
  const started = Date.now();
  const timer = setInterval(async () => {
    let j;
    try { j = await api(`/api/asks/${askId}`); }
    catch (e) {
      clearInterval(timer);
      pill.className = 'pill failed';
      pill.textContent = `ask failed: ${e.message}`;
      btn.disabled = false;
      return;
    }
    if (j.state === 'failed') {
      clearInterval(timer);
      pill.className = 'pill failed';
      pill.textContent = `ask failed: ${j.error}`;
      btn.disabled = false;
    } else if (j.state === 'done') {
      clearInterval(timer);
      pill.className = 'pill done';
      pill.textContent = 'answered';
      renderAskResult(j.result);
      btn.disabled = false;
    } else {
      const s = Math.round((Date.now() - started) / 1000);
      pill.textContent = `${j.state}… ${s}s (deep scans can take a few minutes)`;
    }
  }, 2000);
}

function renderAskResult(res) {
  $('ask-result').classList.remove('hidden');
  $('ask-answer').innerHTML = renderAnswerHtml(res.rendered);
  // notes carry e.g. "self-escalation: ..." — they say WHY an ask took minutes
  const notes = $('ask-notes');
  notes.textContent = (res.notes || []).join('; ');
  notes.classList.toggle('hidden', !notes.textContent);
  const ul = document.querySelector('#ask-evidence ul');
  ul.innerHTML = '';
  const rows = res.evidence || [];
  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'note';
    li.textContent = 'no evidence gathered';
    ul.appendChild(li);
  }
  for (const ev of rows) {
    const li = document.createElement('li');
    const mod = document.createElement('span');
    mod.className = 'modality';
    mod.textContent = ev.modality;
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = fmtT(ev.t);
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = ev.content;
    li.append(mod, t, label);
    if (ev.video_id) li.onclick = () => jumpTo(ev.video_id, ev.t);
    else li.classList.add('note');
    ul.appendChild(li);
  }
}

// --- wiring -----------------------------------------------------------------

$('ingest-btn').onclick = submitIngest;
$('uri').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitIngest(); });
$('search-btn').onclick = runSearch;
$('query').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSearch(); });
$('ask-btn').onclick = runAsk;
$('ask-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') runAsk(); });
$('refresh-btn').onclick = () => loadVideos();
$('video-select').onchange = (e) => selectVideo(e.target.value);

loadVideos();
