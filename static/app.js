const app = document.querySelector('#app');
const esc = x => String(x).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const adminView = location.pathname === '/admin';
const sessionKey = adminView ? 'socrate-host-session' : 'socrate-player-session';
let adminToken = sessionStorage.getItem('socrate-admin-token') || '';
let session = JSON.parse(sessionStorage.getItem(sessionKey) || 'null');
function nav() {
  document.querySelector('.brand').href = adminView ? '/admin' : '/play';
  document.querySelector('.local').innerHTML = adminView ? '<a href="/play">PLAYER VIEW ↗</a>' : '<a href="/admin">ADMIN SIGN IN ↗</a>';
}
nav();
let poll, draft, selected = [], lastKey = '', current, timerEnd = 0;
function updateTimer() {
  const timer = document.querySelector('#timer');
  if(!timer) return;
  const remaining = Math.max(0, (timerEnd - performance.now()) / 1000);
  timer.textContent = `${Math.ceil(remaining)}s`;
  document.querySelectorAll('#answer input, #answer button').forEach(el => el.disabled = remaining <= 0);
}
setInterval(updateTimer, 100);
function notice(message) { const n = document.querySelector('#notice'); n.textContent = message; n.hidden = false; setTimeout(() => n.hidden = true, 5000); }
async function api(path, data, method) {
  const response = await fetch('/api/' + path, {method: method || (data === undefined ? 'GET' : 'POST'), headers: {'Content-Type':'application/json', 'Authorization':session?.token || '', 'X-Admin-Token':adminView ? adminToken : ''}, body:data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json(); if (!response.ok) throw Error(result.error || 'Request failed'); return result;
}
function run(fn) { return async event => { try { await fn(event); } catch(e) { notice(e.message); } }; }
function remember(s) { session = s; sessionStorage.setItem(sessionKey, JSON.stringify(s)); lastKey = ''; play(); }
function leave() { clearTimeout(poll); session = null; sessionStorage.removeItem(sessionKey); home(); }
function joinForm() {
  return `<form class="card join" id="join"><span class="eyebrow">PLAYER VIEW</span><h2>Join the fun.</h2><p>Your host has a game code. You bring the answers.</p><label for="code">Game code</label><input id="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="123456" required><label for="name">Your nickname</label><input id="name" maxlength="30" placeholder="Curious mind" required><button>Join game →</button><p class="muted center">Connect to the same Wi-Fi as your host.</p></form>`;
}
function login() {
  app.innerHTML = `<div class="editor"><span class="eyebrow">ADMIN VIEW</span><h1 style="font-size:48px">Your quiz studio.</h1><form class="card" id="login"><h2>Admin sign in</h2><p>Enter the admin access key shown in the server terminal. Players don’t need this key.</p><label for="key">Admin access key</label><input id="key" type="password" autocomplete="current-password" required><button style="margin-top:20px">Sign in →</button></form><p><a href="/play">Go to player view</a></p></div>`;
  document.querySelector('#login').onsubmit = run(async e => { e.preventDefault(); const result = await api('admin/login', {key:document.querySelector('#key').value}); adminToken = result.token; sessionStorage.setItem('socrate-admin-token', adminToken); await home(); });
}
async function home() {
  clearTimeout(poll);
  if(!adminView) {
    app.innerHTML = `<section class="hero"><div><span class="eyebrow">A LITTLE CURIOSITY GOES A LONG WAY</span><h1>Ready, set.<br>Get curious.</h1><p>Join your host’s game, put your knowledge to the test, and climb the leaderboard.</p><p class="muted">No account needed. Just a code and a curious mind.</p></div>${joinForm()}</section>`;
    document.querySelector('#join').onsubmit = run(async e => { e.preventDefault(); const code = document.querySelector('#code').value.trim(); remember(await api(`games/${code}/join`, {name:document.querySelector('#name').value})); });
    return;
  }
  if(!adminToken) return login();
  try { await api('admin/session'); } catch(e) { adminToken=''; sessionStorage.removeItem('socrate-admin-token'); login(); return; }
  app.innerHTML = `<section><span class="eyebrow">ADMIN STUDIO</span><h1>Good questions.<br>Great company.</h1><p>Create quizzes and host games. Share the player address with your audience.</p><div class="row"><button id="create">＋ Create a quiz</button><a class="button secondary" href="/play" target="_blank" rel="noopener">Open player view ↗</a><button id="logout" class="ghost">Sign out</button></div><p class="muted">Player address: <strong id="player-address"></strong></p></section><div class="section-title"><div><span class="eyebrow">ADMIN ONLY</span><h2>Your quiz collection</h2></div><span class="muted">Saved on this computer</span></div><section class="grid" id="quizzes">Loading your quizzes…</section>`;
  document.querySelector('#player-address').textContent = location.origin + '/play';
  document.querySelector('#create').onclick = () => editor();
  document.querySelector('#logout').onclick = run(async()=>{await api('admin/logout',{});adminToken='';sessionStorage.removeItem('socrate-admin-token');login();});
  try {
    const quizzes = await api('quizzes');
    document.querySelector('#quizzes').innerHTML = quizzes.length ? quizzes.map(q => `<article class="card quiz"><span class="badge">${q.questions.length} QUESTIONS</span><h3>${esc(q.title)}</h3><p>One room. Plenty of bright ideas.</p><div class="row"><button data-host="${q.id}">Host game →</button><button class="ghost" data-copy="${q.id}">Duplicate & edit</button><button class="danger" data-delete="${q.id}">Delete</button></div></article>`).join('') : '<div class="empty">Your next great quiz starts here. Create your first one above.</div>';
    document.querySelectorAll('[data-host]').forEach(b => b.onclick = run(async () => remember(await api('games', {quiz_id:b.dataset.host}))));
    document.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => editor(structuredClone(quizzes.find(q => q.id === b.dataset.copy))));
    document.querySelectorAll('[data-delete]').forEach(b => b.onclick = run(async () => {
      const quiz = quizzes.find(q => q.id === b.dataset.delete);
      if(!confirm(`Delete “${quiz.title}”? This cannot be undone. Games already running will continue.`)) return;
      b.disabled = true;
      try { await api(`quizzes/${quiz.id}`, undefined, 'DELETE'); await home(); }
      finally { b.disabled = false; }
    }));
  } catch(e) { notice(e.message); }
}
function fresh() { return {type:'single',text:'',seconds:20,options:['','','',''],answers:[0]}; }
function editor(quiz) { draft = quiz || {title:'',questions:[fresh()]}; renderEditor(); }
function capture() {
  draft.title = document.querySelector('#title').value;
  document.querySelectorAll('[data-question]').forEach((el,i) => {
    const q = draft.questions[i]; q.text = el.querySelector('.qtext').value; q.seconds = Number(el.querySelector('.duration').value);
    if(q.type === 'text') q.answers = el.querySelector('.accepted').value.split('|').map(x=>x.trim()).filter(Boolean);
    else { q.options = [...el.querySelectorAll('.option-text')].map(x=>x.value); q.answers = [...el.querySelectorAll('.correct')].flatMap((x,i)=>x.checked?[i]:[]); }
  });
}
function renderEditor() {
  app.innerHTML = `<div class="editor"><button class="ghost" id="back">← Collection</button><h1 style="font-size:44px">Make curiosity<br>contagious.</h1><label for="title">Quiz title</label><input id="title" maxlength="120" placeholder="Friday’s big brain challenge" value="${esc(draft.title)}">${draft.questions.map((q,i)=>`<section class="card question" data-question="${i}"><div class="section-title"><h3>Question ${i+1}</h3><button class="danger remove" data-i="${i}" ${draft.questions.length===1?'disabled':''}>Remove</button></div><div class="row"><div style="flex:2"><label>Question type<select class="type" data-i="${i}">${[['single','Single choice'],['multiple','Multiple answers'],['boolean','True / False'],['text','Short answer']].map(([v,t])=>`<option value="${v}" ${q.type===v?'selected':''}>${t}</option>`).join('')}</select></label></div><div style="flex:1"><label>Seconds<input class="duration" type="number" min="5" max="120" value="${q.seconds}"></label></div></div><label>Question<textarea class="qtext" placeholder="What would you like to ask?">${esc(q.text)}</textarea></label>${q.type==='text'?`<label>Accepted answers (separate alternatives with |)<input class="accepted" value="${esc(q.answers.join(' | '))}" placeholder="Paris | paris"></label><p class="muted">Answers ignore letter case and surrounding spaces.</p>`:`<p class="muted">Mark ${q.type==='multiple'?'all correct answers':'the correct answer'}.</p>${q.options.map((o,j)=>`<label class="option-edit"><input aria-label="Option ${j+1} is correct" class="correct" type="${q.type==='multiple'?'checkbox':'radio'}" name="correct-${i}" ${q.answers.includes(j)?'checked':''}><input aria-label="Option ${j+1}" class="option-text" value="${esc(o)}" placeholder="Answer ${j+1}" ${q.type==='boolean'?'readonly':''}></label>`).join('')}`}</section>`).join('')}<div class="row"><button class="secondary" id="add">＋ Add question</button><button id="save">Save quiz →</button></div><p class="muted">Quizzes stay on this computer. Games are live until the server restarts.</p></div>`;
  document.querySelector('#back').onclick = () => { if(confirm('Leave the editor? Unsaved changes will be lost.')) home(); };
  document.querySelectorAll('.type').forEach(el=>el.onchange=()=>{capture(); const q=draft.questions[el.dataset.i]; q.type=el.value; q.options=q.type==='boolean'?['True','False']:['','','','']; q.answers=q.type==='text'?[]:[0]; renderEditor();});
  document.querySelectorAll('.remove').forEach(el=>el.onclick=()=>{capture();draft.questions.splice(Number(el.dataset.i),1);renderEditor();});
  document.querySelector('#add').onclick=()=>{capture();draft.questions.push(fresh());renderEditor();document.querySelectorAll('.question')[draft.questions.length-1].scrollIntoView({behavior:'smooth'});};
  document.querySelector('#save').onclick=run(async()=>{capture();await api('quizzes',draft);await home();});
}
async function play() {
  clearTimeout(poll);
  if(!session) return;
  try {
    const requestStarted = performance.now();
    current = await api(`games/${session.code}`);
    timerEnd = performance.now() + Math.max(0, (current.remaining || 0) * 1000 - (performance.now() - requestStarted) / 2);
    const g = current;
    const key = `${g.phase}-${g.index}-${g.submitted}`;
    if(key!==lastKey) { lastKey=key; selected=[]; renderGame(g); }
    updateTimer();
    const count=document.querySelector('#count'); if(count) count.textContent=`${g.answered} / ${g.players.length} answered`;
    const people=document.querySelector('#people'); if(people) people.innerHTML=g.players.map(p=>`<span class="chip">${esc(p.name)}</span>`).join('') || '<p>Waiting for your first player…</p>';
    poll=setTimeout(play,300);
  } catch(e) { notice(e.message); app.innerHTML='<div class="card center"><h2>Game unavailable</h2><p>The server may have restarted. Return home and start or join a new game.</p><button id="exit">Return home</button></div>'; document.querySelector('#exit').onclick=leave; }
}
function ranking(g) { return `<div class="card">${g.players.map((p,i)=>`<div class="score"><span>${i+1}. ${esc(p.name)}</span><strong>${p.score} pts</strong></div>`).join('')||'<p>No players joined.</p>'}</div>`; }
function renderGame(g) {
  let body='';
  if(g.phase==='lobby') body=`<span class="eyebrow">${esc(g.title)}</span><h1 style="margin-left:auto;margin-right:auto">The room is yours.</h1><p>Join on the same server address and enter this code:</p><div class="code">${g.code}</div><div class="players" id="people"></div>${g.host?'<button id="next">Start game →</button>':'<p>Get comfortable. Your host will start the game shortly.</p>'}`;
  if(g.phase==='question') body=`<div class="row" style="justify-content:space-between"><span class="eyebrow">QUESTION ${g.index+1} OF ${g.total}</span><span class="timer" id="timer"></span></div><h2>${esc(g.question.text)}</h2><p id="count"></p><p class="muted">Correct answers earn 500 points + up to 500 for speed. Incorrect or missed answers earn 0.</p>${g.host?`<div class="answers">${g.question.options.map(o=>`<div class="answer">${esc(o)}</div>`).join('')}</div><p>Everyone has the same timer. Results appear when time is up.</p>`:g.submitted?`<div class="answers">${g.question.options.map(o=>`<div class="answer">${esc(o)}</div>`).join('')}</div><div class="card"><h2>Answer locked in ✓</h2><p>Waiting for the shared timer to finish…</p></div>`:`<form id="answer">${g.question.type==='text'?'<label for="written">Your answer</label><input id="written" maxlength="300" autocomplete="off" required>':`<p>${g.question.type==='multiple'?'Select all correct answers.':'Select one answer.'}</p><div class="answers">${g.question.options.map((o,i)=>`<button type="button" class="answer" data-answer="${i}" aria-pressed="false">${esc(o)}</button>`).join('')}</div>`}<button style="margin-top:16px">Lock in answer →</button></form>`}`;
  if(g.phase==='results') { const q=g.question; const correct=q.type==='text'?q.answers.join(' / '):q.answers.map(i=>q.options[i]).join(', '); body=`<span class="eyebrow">QUESTION ${g.index+1} · RESULTS</span><h2>${esc(q.text)}</h2><div class="card"><span class="eyebrow">CORRECT ANSWER${q.answers.length>1?'S':''}</span><h2>${esc(correct)}</h2>${g.host?'':`<p>${g.correct?'Correct!':'Incorrect or no answer.'} <strong>+${g.points} points</strong>${g.response_seconds == null ? '' : ` · Answered in ${g.response_seconds.toFixed(2)}s`}</p>`}</div><h2>Leaderboard</h2>${ranking(g)}${g.host?`<button style="margin-top:25px" id="next">${g.index+1===g.total?'Finish game':'Next question'} →</button>`:'<p>Waiting for your host…</p>'}`; }
  if(g.phase==='finished') body=`<span class="eyebrow">THAT’S A WRAP</span><h1 style="margin-left:auto;margin-right:auto">${g.players.length?esc(g.players[0].name)+' wins!':'Thanks for playing!'}</h1><p>Good company makes every answer count.</p>${ranking(g)}<button style="margin-top:25px" id="done">${adminView?'Back to collection':'Join another game'}</button>`;
  app.innerHTML=`<section class="game center"><div class="section-title"><span class="muted">${esc(g.title)} · ${g.code}</span><button class="ghost" id="exit">Leave game</button></div>${body}</section>`;
  document.querySelector('#exit').onclick=()=>{if(confirm('Leave this game?')) leave();};
  const next=document.querySelector('#next'); if(next) next.onclick=run(async()=>{next.disabled=true;try{await api(`games/${g.code}/next`,{});await play();}finally{next.disabled=false;}});
  const done=document.querySelector('#done'); if(done) done.onclick=leave;
  document.querySelectorAll('[data-answer]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.answer);selected=g.question.type==='multiple'?(selected.includes(i)?selected.filter(x=>x!==i):[...selected,i]):[i];document.querySelectorAll('[data-answer]').forEach(x=>{const active=selected.includes(Number(x.dataset.answer));x.classList.toggle('selected',active);x.setAttribute('aria-pressed',String(active));});});
  const form=document.querySelector('#answer'); if(form) form.onsubmit=run(async e=>{e.preventDefault();const answer=g.question.type==='text'?document.querySelector('#written').value:selected;if(g.question.type!=='text'&&!answer.length)throw Error('Choose an answer first.');await api(`games/${g.code}/answer`,{index:g.index,answer});await play();});
}
if(session) play(); else home();
