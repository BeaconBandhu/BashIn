const stage = document.getElementById('stage');
const pill = document.getElementById('pill');
const panel = document.getElementById('panel');
const header = document.getElementById('header');

function expand() {
  stage.classList.add('expanded');
  console.log('BASHIN_ACTION:will_expand');
}

function collapse() {
  stage.classList.remove('expanded');
  // Wait for the panel's OWN opacity transition to genuinely finish before
  // telling Python to shrink the native window back down. This is the fix for
  // the earlier "stuck panel" bug: shrinking the native window on a fragile
  // Qt animation-finished signal (which could double-fire or race a fresh
  // expand click) left the window sized big with the content hidden. Here the
  // native resize is gated on the browser's own transitionend event, which
  // fires exactly once per completed transition.
  const onEnd = (e) => {
    if (e.propertyName !== 'opacity') return;
    panel.removeEventListener('transitionend', onEnd);
    console.log('BASHIN_ACTION:did_collapse');
  };
  panel.addEventListener('transitionend', onEnd);
}

function toggle() {
  if (stage.classList.contains('expanded')) collapse(); else expand();
}

pill.addEventListener('click', toggle);
header.addEventListener('click', toggle);

document.getElementById('btnDashboard').addEventListener('click', (e) => {
  e.stopPropagation();
  console.log('BASHIN_ACTION:open_dashboard');
});
document.getElementById('btnPair').addEventListener('click', (e) => {
  e.stopPropagation();
  console.log('BASHIN_ACTION:pair_new_device');
});
document.getElementById('btnEnterCode').addEventListener('click', (e) => {
  e.stopPropagation();
  console.log('BASHIN_ACTION:enter_pairing_code');
});

// Python -> JS data push (see notch.py's _push_update). Deliberately not
// QWebChannel -- this is simpler and every hop is directly testable.
window.bashinUpdate = function (data) {
  document.getElementById('pillTime').textContent = data.time || '--:--';
  document.getElementById('time').textContent = data.time || '--:--';
  document.getElementById('date').textContent = data.date || '';

  if (data.media) {
    const dot = document.getElementById('playDot');
    const title = document.getElementById('npTitle');
    const artist = document.getElementById('npArtist');
    if (data.media.available && data.media.title) {
      title.textContent = data.media.title;
      artist.textContent = data.media.artist || '';
      dot.className = 'dot' + (data.media.playing ? ' playing' : '');
    } else {
      title.textContent = 'Nothing playing';
      artist.textContent = '';
      dot.className = 'dot';
    }
  }

  if (data.bashin_state) {
    const map = {
      IDLE:       ['Idle', ''],
      LISTENING:  ['Listening', 'active'],
      PROCESSING: ['Processing', 'busy'],
      SPEAKING:   ['Speaking', 'active'],
      GUIDING:    ['Guiding', 'busy'],
    };
    const [label, cls] = map[data.bashin_state] || ['Idle', ''];
    document.getElementById('statusText').textContent = label;
    document.getElementById('statusDot').className = 'dot' + (cls ? ' ' + cls : '');
  }

  if (data.task) {
    const prefix = data.task.done ? '' : '⋯ ';
    document.getElementById('taskText').textContent = prefix + (data.task.text || 'Idle');
    const age = Math.max(0, Math.floor(Date.now() / 1000 - data.task.ts));
    document.getElementById('taskAge').textContent = age > 0 ? age + 's ago' : 'just now';
  }

  if (data.devices) {
    const list = document.getElementById('devicesList');
    list.innerHTML = '';
    if (data.devices.length === 0) {
      list.innerHTML = '<div class="np-artist">No paired devices</div>';
    }
    for (const d of data.devices) {
      const row = document.createElement('div');
      row.className = 'device-row';
      row.innerHTML = `<span class="dot ${d.online ? 'active' : ''}"></span><span>${d.name}</span>`;
      list.appendChild(row);
    }
  }
};
