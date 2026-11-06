"use strict";
// Offline form only: no fetch, storage, external library, or implicit approval.
const data = JSON.parse(document.getElementById("review-data").textContent);
const el = id => document.getElementById(id);
const positions = new Map(), landmarks = new Map(), seams = new Map();
let selected = null, selectedPoint = null;
const message = text => { el("message").textContent = text; };
const integer = value => value !== "" && Number.isInteger(Number(value));
const sameHashes = (left, right) => ["standard", "high"].every(res => right?.[res] && left?.[res] === right[res]);
const tileImage = name => `review-tiles/${encodeURIComponent(name)}.png`;
const seamKey = seam => `${seam.source}|${seam.direction}|${seam.target}`;
function option(select, value, text) {
  const item = document.createElement("option"); item.value = value; item.textContent = text; select.append(item);
}
function refresh() {
  el("count").textContent = `${positions.size} positions déclarées ; ${landmarks.size} repères ; ${seams.size} raccords observés. Géographie non vérifiée indépendamment.`;
  el("saved").textContent = JSON.stringify({ tiles: [...positions.values()], landmarks: [...landmarks.values()], seam_observations: [...seams.values()] }, null, 2);
}
function selectTile(tile, event, button) {
  selected = tile;
  selectedPoint = event?.detail ? {
    x: Math.floor((event.clientX - button.getBoundingClientRect().left) * data.tiles[tile.file].width / button.getBoundingClientRect().width),
    y: Math.floor((event.clientY - button.getBoundingClientRect().top) * data.tiles[tile.file].height / button.getBoundingClientRect().height)
  } : null;
  for (const element of el("grid").querySelectorAll("button")) element.setAttribute("aria-pressed", String(element === button));
  const observed = positions.get(tile.file);
  el("selection").textContent = `${tile.file} — position candidate (${tile.x}, ${tile.y}), non validée automatiquement.`;
  el("tile-x").value = observed?.x ?? tile.x; el("tile-y").value = observed?.y ?? tile.y;
  el("tile-evidence").value = observed?.evidence ?? ""; el("original-confirmed").checked = Boolean(observed);
  el("pixel-x").value = selectedPoint?.x ?? ""; el("pixel-y").value = selectedPoint?.y ?? "";
  el("pixel-x").max = data.tiles[tile.file].width - 1; el("pixel-y").max = data.tiles[tile.file].height - 1;
  el("landmark-evidence").value = "";
}
function render() {
  const grid = el("grid"); grid.replaceChildren(); selected = null;
  const tiles = data.components[Number(el("component").value)] ?? [];
  if (!tiles.length) return;
  const scale = Number(el("scale").value) / 100;
  const width = data.tiles[tiles[0].file].width * scale, height = data.tiles[tiles[0].file].height * scale;
  const minX = Math.min(...tiles.map(tile => tile.x)), minY = Math.min(...tiles.map(tile => tile.y));
  grid.style.gridTemplateColumns = `repeat(${Math.max(...tiles.map(tile => tile.x)) - minX + 1}, ${width}px)`;
  grid.style.gridAutoRows = `${height}px`;
  el("scale-value").textContent = `${el("scale").value} %`;
  for (const tile of tiles) {
    const button = document.createElement("button"), image = document.createElement("img"), label = document.createElement("span");
    button.type = "button"; button.setAttribute("aria-label", tile.file); button.setAttribute("aria-pressed", "false");
    button.style.gridColumn = tile.x - minX + 1; button.style.gridRow = tile.y - minY + 1;
    image.src = tileImage(tile.file); image.alt = ""; image.draggable = false; label.textContent = tile.file;
    button.append(image, label); button.addEventListener("click", event => selectTile(tile, event, button)); grid.append(button);
  }
}
function checkTile(tile, previous) {
  if (!tile || !data.tiles[tile.file] || !sameHashes(tile.sha256, data.tiles[tile.file].sha256)) throw new Error("Tuile absente, HD indisponible ou empreintes différentes.");
  if (![tile.x, tile.y].every(value => Number.isInteger(value) && Math.abs(value) <= 100)) throw new Error("Positions entières entre -100 et 100 requises.");
  if (typeof tile.evidence !== "string" || !tile.evidence.trim()) throw new Error("Une preuve de position est requise.");
  if ([...previous.values()].some(other => other.file !== tile.file && other.x === tile.x && other.y === tile.y)) throw new Error("Deux tuiles ne peuvent pas occuper la même case.");
}
function checkMark(mark, tiles) {
  const size = data.tiles[mark?.file], point = mark?.standard_pixel;
  if (!size || !tiles.has(mark.file) || typeof mark.name !== "string" || !mark.name.trim() || typeof mark.evidence !== "string" || !mark.evidence.trim()) throw new Error("Repère : nom, preuve et position enregistrée requis.");
  if (!point || !Number.isInteger(point.x) || !Number.isInteger(point.y) || point.x < 0 || point.y < 0 || point.x >= size.width || point.y >= size.height) throw new Error("Le pixel du repère doit être dans la tuile standard.");
}
function checkSeam(seam, tiles) {
  const source = tiles.get(seam?.source), target = tiles.get(seam?.target);
  const directions = { right: [1, 0], down: [0, 1], left: [-1, 0], up: [0, -1] };
  const delta = Object.hasOwn(directions, seam?.direction) ? directions[seam.direction] : null;
  if (!source || !target || source === target || !delta || !["matches", "contradicts", "uncertain"].includes(seam.result) || typeof seam.evidence !== "string" || !seam.evidence.trim()) throw new Error("Raccord : enregistrer les deux positions, le résultat et la preuve.");
  if (seam.result === "matches" && (target.x - source.x !== delta[0] || target.y - source.y !== delta[1])) throw new Error("Le raccord concordant contredit les positions enregistrées. Corriger les positions ou le résultat.");
}
function perform(action) { try { action(); refresh(); } catch (error) { message(error.message); } }
el("save-tile").onclick = () => perform(() => {
  if (!selected || !el("original-confirmed").checked) throw new Error("Sélectionner une tuile et confirmer son contrôle dans WoWCartographe 1.20.");
  if (!integer(el("tile-x").value) || !integer(el("tile-y").value)) throw new Error("Coordonnées entières requises.");
  const tile = { file: selected.file, x: Number(el("tile-x").value), y: Number(el("tile-y").value), sha256: data.tiles[selected.file].sha256, evidence: el("tile-evidence").value.trim() };
  const next = new Map(positions); next.set(tile.file, tile); checkTile(tile, positions);
  for (const seam of seams.values()) checkSeam(seam, next);
  positions.set(tile.file, tile); message("Position conservée en mémoire. Télécharger les observations avant de fermer.");
});
el("remove-tile").onclick = () => perform(() => {
  if (!selected) throw new Error("Sélectionner une tuile.");
  positions.delete(selected.file);
  for (const [id, mark] of landmarks) if (mark.file === selected.file) landmarks.delete(id);
  for (const [id, seam] of seams) if ([seam.source, seam.target].includes(selected.file)) seams.delete(id);
  el("original-confirmed").checked = false; message("Position, repères et raccords associés retirés des observations.");
});
el("landmark").onchange = () => {
  el("landmark-name").value = el("landmark").selectedOptions[0].textContent;
};
el("save-landmark").onclick = () => perform(() => {
  if (!selected || !integer(el("pixel-x").value) || !integer(el("pixel-y").value)) throw new Error("Sélectionner une tuile et renseigner les pixels du repère.");
  const mark = { id: el("landmark").value, name: el("landmark-name").value.trim(), file: selected.file, standard_pixel: { x: Number(el("pixel-x").value), y: Number(el("pixel-y").value) }, evidence: el("landmark-evidence").value.trim() };
  checkMark(mark, positions); landmarks.set(mark.id, mark); message("Repère déclaré conservé en mémoire, sans validation automatique de la géographie.");
});
function renderSeam() {
  const seam = data.links[Number(el("seam").value)], container = el("seam-images"); container.replaceChildren();
  if (!seam) return;
  container.style.flexDirection = seam.direction === "down" ? "column" : "row";
  for (const name of [seam.source, seam.target]) {
    const figure = document.createElement("figure"), caption = document.createElement("figcaption"), image = document.createElement("img");
    caption.textContent = name; image.src = tileImage(name); image.alt = name; figure.append(caption, image); container.append(figure);
  }
  const saved = seams.get(seamKey(seam)); el("seam-result").value = saved?.result ?? "uncertain"; el("seam-evidence").value = saved?.evidence ?? "";
}
el("save-seam").onclick = () => perform(() => {
  const candidate = data.links[Number(el("seam").value)];
  if (!candidate) throw new Error("Aucun raccord candidat à contrôler.");
  const seam = { source: candidate.source, target: candidate.target, direction: candidate.direction, result: el("seam-result").value, evidence: el("seam-evidence").value.trim() };
  checkSeam(seam, positions); seams.set(seamKey(seam), seam); message("Observation de raccord conservée. Les hypothèses d'origine restent inchangées.");
});
el("download").onclick = () => perform(() => {
  if (!positions.size) throw new Error("Enregistrer au moins une position observée avant de télécharger.");
  const layout = { schema_version: 1, status: "user_observations_not_independently_verified", tool_version: data.tool_version,
    reference_build: data.build, tiles: [...positions.values()], landmarks: [...landmarks.values()], seam_observations: [...seams.values()] };
  const url = URL.createObjectURL(new Blob([JSON.stringify(layout, null, 2) + "\n"], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = "layout-observed.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  message("Observations téléchargées. Conserver ce JSON pour les reprendre ou les transmettre, après vérification des textes saisis.");
});
el("resume").onchange = async () => {
  try {
    const file = el("resume").files[0]; if (!file) return;
    if (file.size > 2 * 1024 * 1024) throw new Error("JSON supérieur à 2 Mio refusé.");
    const layout = JSON.parse(await file.text()), next = new Map(), marks = new Map(), checks = new Map();
    if (layout.schema_version !== 1 || !Array.isArray(layout.tiles) || !layout.tiles.length || !Array.isArray(layout.landmarks ?? []) || !Array.isArray(layout.seam_observations ?? [])) throw new Error("Format d'observations non reconnu.");
    for (const tile of layout.tiles) { checkTile(tile, next); if (next.has(tile.file)) throw new Error("Tuile dupliquée."); next.set(tile.file, tile); }
    for (const mark of layout.landmarks ?? []) { checkMark(mark, next); if (typeof mark.id !== "string" || marks.has(mark.id)) throw new Error("Identifiant de repère absent ou dupliqué."); marks.set(mark.id, mark); }
    for (const seam of layout.seam_observations ?? []) { checkSeam(seam, next); checks.set(seamKey(seam), seam); }
    positions.clear(); landmarks.clear(); seams.clear();
    for (const [key, value] of next) positions.set(key, value);
    for (const [key, value] of marks) landmarks.set(key, value);
    for (const [key, value] of checks) seams.set(key, value);
    refresh(); render(); renderSeam(); message("Observations reprises après contrôle des empreintes. Elles restent des déclarations utilisateur.");
  } catch (error) { message(`Import refusé ; observations conservées : ${error.message}`); }
  el("resume").value = "";
};
el("component").onchange = render; el("scale").oninput = render; el("seam").onchange = renderSeam;
data.components.forEach((tiles, index) => option(el("component"), index, `${index + 1} — ${tiles.length} tuile(s), positions candidates`));
data.links.forEach((link, index) => option(el("seam"), index, `${link.source} → ${link.direction} → ${link.target} — ${link.reason ? "écarté par l'algorithme" : "retenu par l'algorithme"}, non validé`));
el("build").textContent = `Outil ${data.tool_version} — build Era relevé : ${data.build.version ?? "non identifié"}. Référence JPG : Cataclysm rapporté, non déduit du build Era.`;
render(); renderSeam(); refresh();
window.addEventListener("beforeunload", event => { if (positions.size) { event.preventDefault(); event.returnValue = ""; } });
