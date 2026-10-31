/* Flexible OCR -> editable/selectable table helper for Flask/Jinja apps. */
(() => {
  'use strict';

  const PPMP_FIELDS = [
    'General Description and Objective of the Project to be Procured',
    'Type of the Project to be Procured',
    'Quantity and Size of the Project to be Procured',
    'Recommended Mode of Procurement',
    'Pre-Procurement Conference Applicable (Yes/No)',
    'Start of Procurement Activity',
    'End of Procurement Activity',
    'Expected Delivery/Implementation Period',
    'Source of Funds',
    'Estimated Budget Allocation (Php)',
    'Attached Supporting Documents',
    'Remarks'
  ];

  const PPMP_ALIASES = {
    'general description and objective': PPMP_FIELDS[0],
    'general description and objective of the project to be procured': PPMP_FIELDS[0],
    'description': PPMP_FIELDS[0],
    'project description': PPMP_FIELDS[0],
    'type of project': PPMP_FIELDS[1],
    'type of the project to be procured': PPMP_FIELDS[1],
    'project type': PPMP_FIELDS[1],
    'quantity and size': PPMP_FIELDS[2],
    'quantity / size': PPMP_FIELDS[2],
    'quantity/size': PPMP_FIELDS[2],
    'quantity and size of the project to be procured': PPMP_FIELDS[2],
    'recommended mode of procurement': PPMP_FIELDS[3],
    'mode of procurement': PPMP_FIELDS[3],
    'pre-procurement conference applicable': PPMP_FIELDS[4],
    'pre procurement conference applicable': PPMP_FIELDS[4],
    'start of procurement activity': PPMP_FIELDS[5],
    'end of procurement activity': PPMP_FIELDS[6],
    'expected delivery / implementation period': PPMP_FIELDS[7],
    'expected delivery/implementation period': PPMP_FIELDS[7],
    'source of funds': PPMP_FIELDS[8],
    'estimated budget allocation (php)': PPMP_FIELDS[9],
    'estimated budget allocation': PPMP_FIELDS[9],
    'estimated budget': PPMP_FIELDS[9],
    'budget': PPMP_FIELDS[9],
    'attached supporting documents': PPMP_FIELDS[10],
    'supporting documents': PPMP_FIELDS[10],
    'remarks': PPMP_FIELDS[11]
  };

  const norm = (s) => String(s ?? '')
    .replace(/\u00a0/g, ' ')
    .replace(/[‐‑‒–—]/g, '-')
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/\s+/g, ' ')
    .trim();

  const normKey = (s) => norm(s)
    .toLowerCase()
    .replace(/[:;]+$/, '')
    .replace(/[^a-z0-9()/&+\- ]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  const looksLikeMoney = (s) => /^\s*(?:php|₱)?\s*[\d,]+(?:\.\d{1,2})?\s*$/i.test(norm(s));
  const looksLikeNumber = (s) => /^\s*[\d,.]+\s*$/.test(norm(s));
  const isNoise = (s) => {
    const x = norm(s);
    return !x || /^[\W_]+$/.test(x) || /^page\s+\d+(?:\s+of\s+\d+)?$/i.test(x);
  };

  function splitRawLines(text) {
    return String(text ?? '')
      .replace(/\r\n?/g, '\n')
      .split('\n')
      .map(norm)
      .filter((x) => !isNoise(x));
  }

  function parseKeyValue(lines) {
    const pairs = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const m = line.match(/^([^:]{2,80}):\s*(.*)$/);
      if (!m) continue;
      let value = norm(m[2]);
      if (!value && i + 1 < lines.length && !/^([^:]{2,80}):/.test(lines[i + 1])) {
        value = lines[++i];
      }
      pairs.push({ field: norm(m[1]), value });
    }
    return pairs;
  }

  function mapPPMPKeyValue(pairs) {
    const record = {};
    for (const key of PPMP_FIELDS) record[key] = '';
    const unmapped = [];
    for (const p of pairs) {
      const key = normKey(p.field);
      let target = PPMP_ALIASES[key];
      if (!target) {
        const alias = Object.keys(PPMP_ALIASES).find((a) => key.includes(a) || a.includes(key));
        if (alias) target = PPMP_ALIASES[alias];
      }
      if (target) record[target] = p.value;
      else unmapped.push(p);
    }
    const populated = Object.values(record).filter(Boolean).length;
    return { record, populated, unmapped };
  }

  function splitColumns(line) {
    if (line.includes('|')) return line.split('|').map(norm).filter(Boolean);
    if (line.includes('\t')) return line.split(/\t+/).map(norm).filter(Boolean);
    // Two+ spaces is a common OCR/table boundary. Avoid splitting normal sentence spaces.
    return line.split(/\s{2,}/).map(norm).filter(Boolean);
  }

  function inferMatrix(lines) {
    const candidates = lines.map(splitColumns).filter((r) => r.length >= 2);
    if (candidates.length < 2) return null;
    const counts = new Map();
    candidates.forEach((r) => counts.set(r.length, (counts.get(r.length) || 0) + 1));
    const width = [...counts.entries()].sort((a,b) => b[1]-a[1])[0][0];
    const rows = candidates.filter((r) => Math.abs(r.length - width) <= 1)
      .map((r) => r.length === width ? r : [...r, ...Array(Math.max(0,width-r.length)).fill('')].slice(0,width));
    if (rows.length < 2) return null;

    const first = rows[0];
    const headerScore = first.reduce((score, cell) => {
      if (!cell) return score;
      if (looksLikeMoney(cell) || looksLikeNumber(cell)) return score - 1;
      if (/^[A-Za-z][A-Za-z /()&+\-]{1,40}$/.test(cell)) return score + 1;
      return score;
    }, 0);
    const headers = headerScore > 0
      ? first.map((x, i) => x || `Column ${i+1}`)
      : first.map((_, i) => `Column ${i+1}`);
    const body = headerScore > 0 ? rows.slice(1) : rows;
    return { headers, rows: body };
  }

  function parsePositionedBlocks(blocks) {
    // Supports [{text,x,y,width,height,confidence}, ...] from OCR engines.
    if (!Array.isArray(blocks) || !blocks.length) return null;
    const words = blocks
      .filter((b) => norm(b.text))
      .map((b) => ({...b, text: norm(b.text), x: Number(b.x ?? b.left ?? 0), y: Number(b.y ?? b.top ?? 0), h: Number(b.height ?? b.h ?? 12)}))
      .sort((a,b) => a.y-b.y || a.x-b.x);
    if (!words.length) return null;

    const lines = [];
    for (const word of words) {
      const tol = Math.max(7, word.h * 0.65);
      let line = lines.find((l) => Math.abs(l.y - word.y) <= tol);
      if (!line) { line = {y: word.y, words: []}; lines.push(line); }
      line.words.push(word);
      line.y = (line.y * (line.words.length - 1) + word.y) / line.words.length;
    }
    lines.sort((a,b) => a.y-b.y);
    const textLines = lines.map((l) => l.words.sort((a,b)=>a.x-b.x).map((w)=>w.text).join('  '));
    return inferMatrix(textLines) || {headers:['Text'], rows:textLines.map((t)=>[t])};
  }

  function parseOCR(input, options = {}) {
    if (input && typeof input === 'object' && Array.isArray(input.blocks)) {
      const positional = parsePositionedBlocks(input.blocks);
      if (positional) return {type:'table', ...positional, confidence:'medium', raw: input};
    }
    const rawText = typeof input === 'string' ? input : String(input?.text ?? input?.raw_text ?? '');
    const lines = splitRawLines(rawText);
    const pairs = parseKeyValue(lines);

    // Prefer a PPMP record when labels strongly match the PPMP schema.
    if ((options.schema || '').toLowerCase() === 'ppmp' && pairs.length) {
      const mapped = mapPPMPKeyValue(pairs);
      if (mapped.populated >= 3) {
        return {
          type: 'record',
          headers: PPMP_FIELDS,
          rows: [PPMP_FIELDS.map((h) => mapped.record[h] || '')],
          confidence: mapped.populated >= 8 ? 'high' : 'medium',
          unmapped: mapped.unmapped,
          rawText
        };
      }
    }

    const matrix = inferMatrix(lines);
    if (matrix) return {type:'table', ...matrix, confidence:'medium', rawText};

    if (pairs.length) {
      return {
        type: 'key_value',
        headers: ['Field', 'Value'],
        rows: pairs.map((p) => [p.field, p.value]),
        confidence: 'medium',
        rawText
      };
    }

    return {
      type:'lines',
      headers:['Extracted text'],
      rows: lines.map((l)=>[l]),
      confidence:'low',
      rawText
    };
  }

  class OCRTableEditor {
    constructor(root, options = {}) {
      this.root = typeof root === 'string' ? document.querySelector(root) : root;
      this.options = options;
      this.headers = [];
      this.rows = [];
      this.selected = new Set();
      this.mapping = {};
      this.renderShell();
    }

    load(parsed) {
      this.headers = [...(parsed.headers || [])];
      this.rows = (parsed.rows || []).map((cells, i) => ({ id: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `ocr-${Date.now()}-${i}`, cells:[...cells] }));
      this.selected = new Set(this.rows.map((r)=>r.id));
      this.parsed = parsed;
      // Auto-map exact/known PPMP headers but keep every mapping user-editable.
      this.mapping = {};
      if (this.options.targetFields?.length) {
        this.headers.forEach((header, i) => {
          const exact = this.options.targetFields.find((f) => normKey(f) === normKey(header));
          const alias = PPMP_ALIASES[normKey(header)];
          const target = exact || alias;
          if (target && this.options.targetFields.includes(target)) this.mapping[i] = target;
        });
      }
      this.render();
    }

    renderShell() {
      this.root.innerHTML = `
        <div class="ocr-table-toolbar">
          <div>
            <strong>Structured OCR Preview</strong>
            <span class="ocr-confidence"></span>
          </div>
          <div class="ocr-actions">
            <button type="button" class="btn small" data-act="add-row">+ Add row</button>
            <button type="button" class="btn small" data-act="add-col">+ Add column</button>
            <button type="button" class="btn small" data-act="select-all">Select all</button>
            <button type="button" class="btn small" data-act="clear-selection">Clear</button>
            <button type="button" class="btn small blue" data-act="copy-json">Copy JSON</button>
          </div>
        </div>
        <div class="ocr-mapping"></div>
        <div class="table-scroll"><table class="table ocr-edit-table"><thead></thead><tbody></tbody></table></div>
        <div class="ocr-footer"><span class="ocr-count"></span><textarea class="ocr-json" hidden></textarea></div>`;
      this.root.addEventListener('click', (e) => this.handleClick(e));
      this.root.addEventListener('input', () => this.syncJson());
      this.root.addEventListener('change', () => this.syncJson());
    }

    render() {
      this.root.querySelector('.ocr-confidence').textContent = this.parsed?.confidence ? ` · ${this.parsed.confidence} confidence` : '';
      const thead = this.root.querySelector('thead');
      thead.innerHTML = `<tr><th><input type="checkbox" data-role="all" ${this.selected.size===this.rows.length && this.rows.length?'checked':''}></th>${this.headers.map((h,i)=>`<th><input class="ocr-header" data-col="${i}" value="${escapeHtml(h)}" aria-label="Column ${i+1} header"></th>`).join('')}<th>Action</th></tr>`;
      const tbody = this.root.querySelector('tbody');
      tbody.innerHTML = this.rows.map((r,ri)=>`<tr data-id="${r.id}"><td><input type="checkbox" class="ocr-row-check" ${this.selected.has(r.id)?'checked':''}></td>${this.headers.map((_,ci)=>`<td><textarea class="ocr-cell" data-row="${ri}" data-col="${ci}" rows="2">${escapeHtml(r.cells[ci] ?? '')}</textarea></td>`).join('')}<td><button type="button" class="btn small" data-act="delete-row" data-id="${r.id}">Remove</button></td></tr>`).join('');
      this.renderMapping();
      this.syncJson();
    }

    renderMapping() {
      if (!this.options.targetFields?.length) { this.root.querySelector('.ocr-mapping').innerHTML = ''; return; }
      this.root.querySelector('.ocr-mapping').innerHTML = `<div class="ocr-map-grid">${this.headers.map((h,i)=>`<label><span>${escapeHtml(h || `Column ${i+1}`)}</span><select data-map-col="${i}"><option value="">Do not map</option>${this.options.targetFields.map((f)=>`<option value="${escapeHtml(f)}" ${this.mapping[i]===f?'selected':''}>${escapeHtml(f)}</option>`).join('')}</select></label>`).join('')}</div>`;
    }

    handleClick(e) {
      const btn = e.target.closest('[data-act]');
      if (btn) {
        const act = btn.dataset.act;
        if (act === 'add-row') { this.rows.push({id:`ocr-${Date.now()}-${Math.random()}`,cells:Array(this.headers.length).fill('')}); this.render(); }
        if (act === 'add-col') { this.headers.push(`Column ${this.headers.length+1}`); this.rows.forEach(r=>r.cells.push('')); this.render(); }
        if (act === 'select-all') { this.selected = new Set(this.rows.map(r=>r.id)); this.render(); }
        if (act === 'clear-selection') { this.selected.clear(); this.render(); }
        if (act === 'delete-row') { this.rows = this.rows.filter(r=>r.id!==btn.dataset.id); this.selected.delete(btn.dataset.id); this.render(); }
        if (act === 'copy-json') navigator.clipboard?.writeText(JSON.stringify(this.payload(), null, 2));
      }
      if (e.target.matches('[data-role="all"]')) {
        this.selected = e.target.checked ? new Set(this.rows.map(r=>r.id)) : new Set();
        this.render();
      }
      if (e.target.matches('.ocr-row-check')) {
        const id = e.target.closest('tr').dataset.id;
        if (e.target.checked) this.selected.add(id); else this.selected.delete(id);
        this.syncJson();
      }
    }

    syncFromDOM() {
      this.root.querySelectorAll('.ocr-header').forEach(el => { this.headers[Number(el.dataset.col)] = norm(el.value); });
      this.root.querySelectorAll('.ocr-cell').forEach(el => { this.rows[Number(el.dataset.row)].cells[Number(el.dataset.col)] = el.value; });
      this.root.querySelectorAll('[data-map-col]').forEach(el => { this.mapping[Number(el.dataset.mapCol)] = el.value; });
    }

    payload() {
      this.syncFromDOM();
      const selectedRows = this.rows.filter(r=>this.selected.has(r.id));
      const rows = selectedRows.map(r => {
        const obj = {};
        this.headers.forEach((h,i)=> { obj[h || `Column ${i+1}`] = norm(r.cells[i]); });
        return obj;
      });
      const mappedRows = selectedRows.map(r => {
        const obj = {};
        this.headers.forEach((h,i)=> {
          const target = this.mapping[i];
          if (target) obj[target] = norm(r.cells[i]);
        });
        return obj;
      }).filter(o => Object.keys(o).length);
      return {
        schema: this.options.schema || null,
        headers: this.headers,
        selected_count: selectedRows.length,
        rows,
        mapped_rows: mappedRows,
        parser_type: this.parsed?.type || null,
        parser_confidence: this.parsed?.confidence || null
      };
    }

    syncJson() {
      const json = JSON.stringify(this.payload());
      this.root.querySelector('.ocr-json').value = json;
      const count = this.root.querySelector('.ocr-count');
      count.textContent = `${this.selected.size} of ${this.rows.length} row(s) selected`;
      if (this.options.hiddenInput) {
        const target = document.querySelector(this.options.hiddenInput);
        if (target) target.value = json;
      }
      this.options.onChange?.(this.payload());
    }
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c)=>({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#039;' }[c]));
  }

  window.OCRFlexibleTable = { parseOCR, OCRTableEditor, PPMP_FIELDS };
})();
