'use strict';
let csrf = '', limit = 0, pages = [0];
const el = id => document.getElementById(id);
const status = message => { el('status').textContent = message; };
const decoder = new TextDecoder('windows-1252');
const encodeMap = new Map();
for (let i = 0; i < 256; i++) {
  const char = decoder.decode(new Uint8Array([i]));
  if (char !== '\ufffd') encodeMap.set(char, i);
}
function encodeLegacy(text) {
  if (/[|\r\n\x00]/.test(text)) throw new Error('Le nom et le commentaire ne peuvent pas contenir | ou de retour à la ligne.');
  const result = [];
  for (const char of text) {
    if (!encodeMap.has(char)) throw new Error('Ce caractère ne peut pas être enregistré dans le format historique.');
    result.push(encodeMap.get(char));
  }
  return result;
}
async function request(action, form) {
  const options = {credentials: 'same-origin', cache: 'no-store'};
  if (form) { form.set('csrf', csrf); options.method = 'POST'; options.body = form; }
  const response = await fetch('api.php?action=' + encodeURIComponent(action), options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Service indisponible.');
  if (data.csrf) csrf = data.csrf;
  return data;
}
function labelInput(form, labelText, value) {
  const label = document.createElement('label'); label.textContent = labelText;
  const input = document.createElement('input'); input.value = value; label.append(input); form.append(label); return input;
}
async function loadPage() {
  const after = pages[pages.length - 1];
  const response = await fetch('api.php?action=community_list&after=' + after, {credentials:'same-origin',cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Service indisponible.');
  const container = el('items'); container.replaceChildren();
  for (const item of data.items) {
    const raw = Uint8Array.from(atob(item.raw_base64), c => c.charCodeAt(0));
    const fields = []; let start = 0;
    for (let i = 0; i <= raw.length; i++) if (i === raw.length || raw[i] === 124) { fields.push(raw.slice(start, i)); start = i + 1; }
    if (fields.length < 16) throw new Error('Une annotation ne peut pas être affichée.');
    const form = document.createElement('form');
    const info = document.createElement('p'); info.textContent = 'Annotation #' + item.id + ' · version ' + item.revision; form.append(info);
    const name = labelInput(form, 'Nom', decoder.decode(fields[0])); name.required = true;
    const comment = labelInput(form, 'Premier commentaire', decoder.decode(fields[15]));
    const button = document.createElement('button'); button.type = 'submit'; button.textContent = 'Enregistrer la correction'; form.append(button);
    const history = document.createElement('button'); history.type = 'button'; history.textContent = 'Voir les versions précédentes'; form.append(history);
    const versions = document.createElement('div'); form.append(versions);
    history.addEventListener('click', async () => {
      history.disabled = true;
      try {
        const response = await fetch('api.php?action=community_history&id=' + item.id, {credentials:'same-origin',cache:'no-store'});
        const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Historique indisponible.');
        versions.replaceChildren();
        for (const version of data.versions) {
          const line = document.createElement('p');
          const bytes = Uint8Array.from(atob(version.raw_base64), c => c.charCodeAt(0));
          const historicalName = decoder.decode(bytes).split('|')[0];
          line.textContent = 'Version ' + version.revision + ' · ' + historicalName + ' ';
          if (version.revision !== item.revision) {
            const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Restaurer cette version';
            restore.addEventListener('click', async () => {
              restore.disabled = true;
              try {
                const input = new FormData(); input.set('id', String(item.id)); input.set('revision', String(item.revision)); input.set('record_base64', version.raw_base64);
                await request('community_edit', input); await loadPage(); status('Version précédente restaurée.');
              } catch (error) { status(error.message); restore.disabled = false; }
            });
            line.append(restore);
          }
          versions.append(line);
        }
      } catch (error) { status(error.message); }
      finally { history.disabled = false; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); button.disabled = true;
      try {
        if (name.value.includes('\ufffd') || comment.value.includes('\ufffd')) throw new Error('L’encodage de cette annotation ne peut pas être modifié ici.');
        fields[0] = Uint8Array.from(encodeLegacy(name.value)); fields[15] = Uint8Array.from(encodeLegacy(comment.value));
        const bytes = []; fields.forEach((field, index) => { if (index) bytes.push(124); for (const byte of field) bytes.push(byte); });
        const input = new FormData(); input.set('id', String(item.id)); input.set('revision', String(item.revision));
        input.set('record_base64', btoa(bytes.map(byte => String.fromCharCode(byte)).join('')));
        await request('community_edit', input); await loadPage(); status('Correction enregistrée.');
      } catch (error) { status(error.message); }
      finally { button.disabled = false; }
    });
    container.append(form);
  }
  if (!data.items.length) container.textContent = 'Aucune annotation sur cette page.';
  el('previous').hidden = pages.length === 1;
  el('previous').onclick = () => { pages.pop(); loadPage().catch(error => status(error.message)); };
  el('next').hidden = !data.has_more;
  el('next').onclick = () => { pages.push(data.items[data.items.length - 1].id); loadPage().catch(error => status(error.message)); };
}
el('upload').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true;
  try {
    const file = el('file').files[0];
    if (!file || file.size > limit) throw new Error('Fichier absent ou trop volumineux.');
    const data = await request('community_upload', new FormData(event.target));
    pages = [0]; await loadPage(); event.target.reset();
    status(data.import.inserted ? data.import.records + ' annotations partagées.' : 'Ce fichier a déjà été partagé.');
  } catch (error) { status(error.message); }
  finally { button.disabled = false; }
});
request('bootstrap').then(async data => {
  if (!data.open || !data.community_ready) { status('Le service communautaire est en préparation.'); return; }
  limit = data.limits.bytes; el('limits').textContent = 'Maximum : ' + (limit / 1048576).toFixed(1) + ' Mio et ' + data.limits.records + ' annotations par fichier.';
  el('community').hidden = false; await loadPage(); status('Tu peux partager ou corriger des annotations.');
}).catch(error => status(error.message));
