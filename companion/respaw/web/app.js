const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="respaw-token"]').content;
let session = null;
let epoch = 0;
let recording = null;
let micStream = null;
let micTimer = null;
let pending = false;
let switching = true;
let sessionVersion = 0;
let voiceUrl = null;

async function api(path, data = {}) {
  const response = await fetch(`/api/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-ResPaw-Token': token },
    body: JSON.stringify({ session_id: session?.session_id, ...data }),
  });
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error), result);
  return result;
}

function notice(text = '') { $('notice').textContent = text; }
function face(expression) { $('face').className = `robot ${expression}`; }
function memoryStatus(status) {
  $('memory-status').textContent = status?.ready
    ? 'Memoria por significado disponible.'
    : 'Memoria por palabras y contexto disponible.';
  if (status?.pending) $('memory-status').textContent += ` Quedan ${status.pending} recuerdos por preparar.`;
  $('memory-status').title = status?.error || '';
}
function busy(value) {
  pending = value;
  $('send').disabled = value || switching || !session;
  $('thinking').textContent = value ? 'Un momento…' : '';
  if (value) face('thinking');
}

function message(role, text) {
  $('messages').querySelector('.welcome')?.remove();
  const element = document.createElement('div');
  element.className = `message ${role}`;
  const author = document.createElement('span');
  author.className = 'author';
  author.textContent = role === 'user' ? 'TÚ' : 'RESPAW';
  element.append(author, document.createTextNode(text));
  $('messages').append(element);
  $('messages').scrollTop = $('messages').scrollHeight;
  return element;
}

async function refreshMemories() {
  const forSession = session;
  const memories = await api('memories');
  if (session !== forSession) return;
  $('memory-count').textContent = memories.length;
  $('memories-list').replaceChildren();
  $('forget-all').disabled = !memories.length;
  if (!memories.length) {
    const text = document.createElement('p');
    text.textContent = session.guest ? 'El modo invitado no conserva recuerdos.' : 'Todavía no elegiste ningún mensaje para recordar.';
    $('memories-list').append(text);
  }
  for (const memory of memories) {
    const card = document.createElement('div');
    card.className = 'memory-card';
    const label = document.createElement('small');
    const preference = memory.kind === 'preference';
    const state = preference
      ? memory.status === 'resolved' ? 'Preferencia pausada' : 'Preferencia activa'
      : memory.status === 'resolved' ? 'Asunto resuelto' : 'Recuerdo guardado';
    label.textContent = `${new Date(memory.created_at).toLocaleDateString('es-PE')} · ${state}`;
    const kindLabel = document.createElement('label');
    kindLabel.className = 'memory-kind';
    kindLabel.append('Tipo de recuerdo');
    const kind = document.createElement('select');
    kind.append(new Option('Un episodio: algo que me pasó', 'episode'),
      new Option('Una preferencia: cómo acompañarme', 'preference'));
    kind.value = memory.kind;
    kindLabel.append(kind);
    const input = document.createElement('textarea');
    input.value = memory.quote;
    input.maxLength = 2000;
    input.setAttribute('aria-label', 'Corregir recuerdo');
    const actions = document.createElement('div');
    actions.className = 'memory-actions';
    for (const [title, change] of [
      ['Guardar cambios', () => ({ quote: input.value, kind: kind.value, status: kind.value === memory.kind ? memory.status : 'open' })],
      [preference
        ? memory.status === 'resolved' ? 'Reactivar preferencia' : 'Pausar preferencia'
        : memory.status === 'resolved' ? 'Reabrir asunto' : 'Marcar resuelto',
      () => ({ quote: input.value, status: memory.status === 'resolved' ? 'open' : 'resolved' })],
      ['Olvidar', () => ({})],
    ]) {
      const button = document.createElement('button');
      button.className = 'quiet';
      button.textContent = title;
      button.onclick = async () => {
        button.disabled = true;
        try {
          await stop();
          await api('memories/change', { memory_id: memory.id, ...change() });
          $('messages').replaceChildren();
          notice('Recuerdo actualizado. Comenzamos de nuevo para respetar el cambio.');
          await refreshMemories();
        } catch (error) { button.disabled = false; notice(error.message); }
      };
      actions.append(button);
    }
    card.append(label, kindLabel, input, actions);
    $('memories-list').append(card);
  }
}

async function stop() {
  epoch += 1;
  stopPlayback();
  if (recording?.state === 'recording') recording.stop();
  releaseMic();
  busy(false);
  face('neutral');
  if (session) await api('stop');
}

function stopPlayback() {
  const player = $('voice-audio');
  player.pause();
  player.removeAttribute('src');
  player.hidden = true;
  if (voiceUrl) URL.revokeObjectURL(voiceUrl);
  voiceUrl = null;
}

async function readReply(current) {
  const result = await api('speak');
  if (current !== epoch || !$('voice').checked || !result.audio) return;
  stopPlayback();
  const bytes = Uint8Array.from(atob(result.audio), (character) => character.charCodeAt(0));
  voiceUrl = URL.createObjectURL(new Blob([bytes], { type: result.mime }));
  const player = $('voice-audio');
  player.src = voiceUrl;
  player.hidden = false;
  try { await player.play(); }
  catch { if (current === epoch) notice('Pulsa reproducir para escuchar la respuesta.'); }
}

async function newSession() {
  const version = ++sessionVersion;
  const userId = $('profile').value || null;
  switching = true;
  busy(false);
  try {
    await stop();
    if (version !== sessionVersion) return;
    const next = await api('session', { user_id: userId });
    if (version !== sessionVersion) return;
    session = next;
    $('messages').replaceChildren();
    notice(session.guest ? 'Sesión de invitado: no se guardarán recuerdos.' : 'Puedes retomar un tema o empezar por algo nuevo.');
    await refreshMemories();
    $('message').focus();
  } catch (error) {
    if (version === sessionVersion) {
      session = null;
      $('messages').replaceChildren();
    }
    throw error;
  } finally {
    if (version === sessionVersion) {
      switching = false;
      busy(false);
    }
  }
}

$('chat-form').onsubmit = async (event) => {
  event.preventDefault();
  const text = $('message').value.trim();
  if (!text || !session || pending || switching) return;
  const current = ++epoch;
  const userElement = message('user', text);
  $('message').value = '';
  notice();
  busy(true);
  try {
    const result = await api('chat', { text });
    if (current !== epoch) return;
    memoryStatus(result.retrieval);
    const answer = message('assistant', result.reply);
    busy(false);
    face(result.expression);
    if (!session.guest) {
      const remember = document.createElement('button');
      remember.className = 'quiet';
      remember.textContent = 'Recordar este mensaje';
      remember.onclick = async () => {
        remember.disabled = true;
        try {
          await api('memories/save', { message_id: result.user_message_id });
          remember.textContent = 'Recuerdo guardado';
          remember.disabled = true;
          await refreshMemories();
        } catch (error) { remember.disabled = false; notice(error.message); }
      };
      userElement.append(document.createElement('br'), remember);
    }
    if (result.sources.length) {
      const details = document.createElement('details');
      details.className = 'sources';
      const summary = document.createElement('summary');
      summary.textContent = 'Recuerdos utilizados';
      details.append(summary);
      for (const source of result.sources) {
        const p = document.createElement('p');
        p.textContent = source.quote;
        details.append(p);
      }
      answer.append(details);
    }
    if (result.offer !== 'none') {
      const choose = document.createElement('button');
      choose.className = 'quiet';
      choose.textContent = result.offer === 'pause' ? 'Elegir una pausa breve' : 'Elegir un paso pequeño';
      choose.onclick = async () => {
        try {
          await stop();
          const activity = await api('activity', { activity: result.offer });
          message('assistant', activity.text);
        } catch (error) { notice(error.message); }
      };
      answer.append(document.createElement('br'), choose);
    }
    if ($('voice').checked) await readReply(current);
  } catch (error) {
    if (current === epoch) notice(error.message);
  } finally {
    if (current === epoch) busy(false);
  }
};

$('message').onkeydown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('chat-form').requestSubmit();
  }
};
$('stop').onclick = () => stop().catch((error) => notice(error.message));
$('voice').onchange = () => { if (!$('voice').checked) stop().catch((error) => notice(error.message)); };
$('profile').onchange = () => newSession().catch((error) => notice(error.message));
$('new-session').onclick = () => newSession().catch((error) => notice(error.message));
$('show-memories').onclick = async () => {
  try { await refreshMemories(); $('memories-dialog').showModal(); } catch (error) { notice(error.message); }
};
$('close-memories').onclick = () => $('memories-dialog').close();
$('create-profile').onclick = () => $('profile-dialog').showModal();
$('cancel-profile').onclick = () => $('profile-dialog').close();
$('profile-form').onsubmit = async (event) => {
  event.preventDefault();
  try {
    const profile = await api('profiles/create', { name: $('profile-name').value });
    await loadProfiles(profile.id);
    $('profile-dialog').close();
    $('profile-name').value = '';
    await newSession();
  } catch (error) { notice(error.message); }
};
$('forget-all').onclick = async () => {
  if (!confirm('¿Olvidar todos los recuerdos de este perfil?')) return;
  try {
    await stop();
    await api('memories/change');
    $('messages').replaceChildren();
    await refreshMemories();
    notice('Los recuerdos se han borrado.');
  } catch (error) { notice(error.message); }
};
$('measure').onclick = async () => {
  try {
    await api('measure');
    $('measurement').textContent = 'Medición solicitada. Mantén el dedo apoyado si el sensor está conectado.';
  } catch (error) { notice(error.message); }
};

async function loadProfiles(selected = '') {
  const profiles = await api('profiles');
  $('profile').replaceChildren(new Option('Invitado · sin recuerdos', ''));
  for (const profile of profiles) $('profile').append(new Option(profile.name, profile.id));
  $('profile').value = selected;
}

function releaseMic() {
  clearTimeout(micTimer);
  micStream?.getTracks().forEach((track) => track.stop());
  micStream = null;
  recording = null;
  $('mic').textContent = 'Hablar';
}

$('mic').onclick = async () => {
  if (switching || !session) return;
  if (recording?.state === 'recording') { recording.stop(); return; }
  try {
    await stop();
    const requestedEpoch = epoch;
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
    if (requestedEpoch !== epoch) { releaseMic(); return; }
    const mime = ['audio/webm', 'audio/mp4', 'audio/ogg'].find((type) => MediaRecorder.isTypeSupported(type));
    if (!mime) throw new Error('Este navegador no admite la grabación local.');
    recording = new MediaRecorder(micStream, { mimeType: mime });
    const chunks = [];
    const captureEpoch = epoch;
    recording.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recording.onstop = async () => {
      releaseMic();
      if (captureEpoch !== epoch) return;
      notice('Transcribiendo…');
      try {
        const blob = new Blob(chunks, { type: mime });
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if (captureEpoch !== epoch) return;
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        const result = await api('transcribe', { audio: btoa(binary), suffix: `.${mime.split('/')[1]}` });
        if (captureEpoch !== epoch) return;
        $('message').value = result.text;
        notice('Revisa lo transcrito y pulsa Enviar.');
      } catch (error) { if (captureEpoch === epoch) notice(error.message); }
    };
    recording.onerror = () => { releaseMic(); notice('No se pudo grabar el audio.'); };
    recording.start();
    $('mic').textContent = 'Terminar grabación';
    notice('Te escucho. Máximo 30 segundos. Revisa el texto antes de enviarlo.');
    micTimer = setTimeout(() => { if (recording?.state === 'recording') recording.stop(); }, 30000);
  } catch (error) { releaseMic(); notice(error.message); }
};

async function initialize() {
  await loadProfiles();
  await newSession();
  const status = await api('status');
  memoryStatus(status.retrieval);
  const cloud = status.model.provider === 'openai';
  $('execution-status').textContent = cloud ? '● Conversación en nube' : '● Modelo local';
  $('model-status').textContent = status.model.ready
    ? `${cloud ? 'Modelo en nube' : 'Modelo local'} listo · ${status.model.model}` : status.model.error;
  robotStatus(status.robot);
  const cloudVoice = status.speech.provider === 'cloud';
  if (cloud || cloudVoice) {
    $('privacy-notice').textContent = 'Solo guardamos los mensajes que marques «Recordar». Los proveedores en nube procesan la conversación o el audio cuando utilizas esas funciones.';
  }
  $('voice').disabled = !status.speech.tts;
  $('mic').disabled = !status.speech.stt || !navigator.mediaDevices || !window.MediaRecorder;
  if (!status.speech.stt) $('mic').title = cloudVoice
    ? 'Configura la clave de OpenAI para transcribir.' : 'Configura whisper.cpp para transcribir sin conexión.';
  await refreshMemories();
  setInterval(async () => {
    try {
      const robot = await api('robot');
      robotStatus(robot);
      if (robot.measurement) {
        $('measurement').textContent = robot.measurement.valid
          ? `${robot.measurement.bpm.toFixed(1)} BPM · RMSSD ${robot.measurement.rmssd.toFixed(1)} ms. Lectura orientativa.`
          : robot.measurement.reason === 'simulator' ? 'El simulador no genera mediciones fisiológicas.' : 'Medición insuficiente. Puedes conversar sin medir.';
      }
    } catch { /* A closed local server will be reported on the next user action. */ }
  }, 3000);
}

function robotStatus(robot) {
  $('device-status').textContent = robot.error || (robot.simulated
    ? 'Pantalla en modo simulación · Mega sin conectar'
    : robot.ready ? (robot.transport === 'wifi' ? 'Mega conectado mediante el Pico' : 'Mega conectado por USB')
      : 'Esperando al robot');
  $('contact-status').textContent = robot.contact && robot.ready
    ? robot.contact.pressed ? 'Contacto de presión detectado.' : 'Sensor de presión libre.' : '';
}
initialize().catch((error) => notice(error.message));
