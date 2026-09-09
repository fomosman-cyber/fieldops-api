/**
 * Kwaliteit & keuringen — de front-end van de module.
 *
 * Wordt lazy geladen door kwLaad() in portaal.html en gebruikt de globals die
 * daar al staan: api(), showToast(), currentUser, escapeHtml(). Bewust een
 * apart bestand: portaal.html is 26.000 regels en deze module erbij zou het
 * onwerkbaar maken.
 *
 * Drie schermen in één pagina:
 *   1. de lijst met keuringen        (kwOpen / kwLaadKeuringen)
 *   2. één keuring: velden en eisen  (kwOpenKeuring)
 *   3. één registratie invullen      (kwOpenRegistratie)
 *
 * Het derde scherm is waar de vakman staat. Daar hoort zo min mogelijk te
 * gebeuren: grote knoppen, de camera direct, en alles wat we al weten al
 * ingevuld. De eerste twee zijn beheerwerk en mogen dichter op elkaar staan.
 */
(function () {
  'use strict';

  var CFG = null;            // /api/kwaliteit/config, één keer opgehaald
  var huidigeKeuring = null;
  var huidigeRegistratie = null;
  var zoekTimer = null;

  // ── Kleine hulpjes ───────────────────────────────────────────────

  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(id) { return document.getElementById(id); }

  function toon(welk) {
    ['kwLijst', 'kwDetail', 'kwRegistratie'].forEach(function (id) {
      var n = el(id);
      if (n) n.style.display = (id === welk) ? '' : 'none';
    });
    var filters = el('kwFilters');
    if (filters) filters.style.display = (welk === 'kwLijst') ? 'flex' : 'none';
    var terug = el('kwTerugBtn');
    if (terug) terug.style.display = (welk === 'kwLijst') ? 'none' : '';
    var nieuw = el('kwNieuwBtn');
    if (nieuw) nieuw.style.display = (welk === 'kwLijst') ? '' : 'none';
  }

  function datumNL(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString('nl-NL', { day: '2-digit', month: '2-digit', year: 'numeric' });
  }

  /** Kleur bij een resultaat. Rood is niet decoratief: het betekent dat er
   *  iemand naar moet kijken. Grijs betekent "nog geen oordeel" en dat is
   *  bewust niet groen. */
  function resultaatKleur(res) {
    if (res === 'akkoord') return 'var(--success, #16a34a)';
    if (res === 'niet_akkoord') return 'var(--danger, #dc2626)';
    if (res === 'deels_akkoord') return 'var(--warning, #d97706)';
    return 'var(--text-dim)';
  }

  /** Label bij een resultaatcode.
   *
   *  Bewust niet het `resultaat_label` uit een eerder opgehaald object: na het
   *  invullen van een veld werkt de server alleen de code bij, en dan zou de
   *  kop nog "Niet beoordeeld" tonen terwijl er al een afkeur onder staat.
   */
  function resultaatLabel(code) {
    if (!code) return '';
    var lijst = (CFG && CFG.resultaten) || [];
    for (var i = 0; i < lijst.length; i++) {
      if (lijst[i].code === code) return lijst[i].label;
    }
    return code;
  }

  function badge(tekst, kleur) {
    return '<span style="display:inline-block;padding:2px 8px;border-radius:999px;' +
      'font-size:11px;font-weight:600;border:1px solid ' + kleur + ';color:' + kleur + ';">' +
      esc(tekst) + '</span>';
  }

  function statusBadge(status) {
    var kleuren = {
      concept: 'var(--text-dim)',
      in_uitvoering: 'var(--accent, #0ea5e9)',
      ingediend: 'var(--warning, #d97706)',
      in_beoordeling: 'var(--warning, #d97706)',
      goedgekeurd: 'var(--success, #16a34a)',
      afgekeurd: 'var(--danger, #dc2626)',
      herziening_vereist: 'var(--danger, #dc2626)'
    };
    var labels = {
      concept: 'Concept', in_uitvoering: 'In uitvoering', ingediend: 'Ingediend',
      in_beoordeling: 'In beoordeling', goedgekeurd: 'Goedgekeurd',
      afgekeurd: 'Afgekeurd', herziening_vereist: 'Herziening vereist'
    };
    return badge(labels[status] || status, kleuren[status] || 'var(--text-dim)');
  }

  function melding(tekst, soort) {
    if (typeof showToast === 'function') showToast(tekst, soort || 'info');
  }

  function foutTekst(e) {
    if (e && e.detail) return e.detail;
    if (e && e.message) return e.message;
    return 'Er ging iets mis';
  }

  // ── Scherm 1: de lijst ───────────────────────────────────────────

  function kwOpen() {
    toon('kwLijst');
    if (CFG) { kwLaadKeuringen(); return; }
    api('GET', '/api/kwaliteit/config').then(function (cfg) {
      CFG = cfg;
      var sel = el('kwFilterWerksoort');
      if (sel && sel.options.length <= 1) {
        cfg.werksoorten.forEach(function (w) {
          var o = document.createElement('option');
          o.value = w.code; o.textContent = w.label;
          sel.appendChild(o);
        });
      }
      kwLaadKeuringen();
    }).catch(function (e) {
      el('kwLijst').innerHTML = '<div class="empty-state"><h3>Niet geladen</h3><p>' +
        esc(foutTekst(e)) + '</p></div>';
    });
  }

  function kwZoekDebounce() {
    clearTimeout(zoekTimer);
    zoekTimer = setTimeout(kwLaadKeuringen, 300);
  }

  function kwLaadKeuringen() {
    var doel = el('kwLijst');
    if (!doel) return;
    doel.innerHTML = '<div class="loading-spinner">Keuringen laden...</div>';

    var q = [];
    var w = el('kwFilterWerksoort'); if (w && w.value) q.push('werksoort=' + encodeURIComponent(w.value));
    var s = el('kwFilterStatus'); if (s && s.value) q.push('status=' + encodeURIComponent(s.value));
    var z = el('kwZoek'); if (z && z.value.trim()) q.push('zoek=' + encodeURIComponent(z.value.trim()));

    api('GET', '/api/kwaliteit/keuringen' + (q.length ? '?' + q.join('&') : ''))
      .then(function (body) {
        if (!body.keuringen.length) {
          doel.innerHTML = '<div class="empty-state">' +
            '<h3>Nog geen keuring</h3>' +
            '<p>Begin met een standaardkeuring voor asfalt, fundering, graafwerk of riool ' +
            'en pas hem aan naar dit werk.</p>' +
            '<button class="btn-primary" onclick="kwNieuweKeuring()">+ Nieuwe keuring</button>' +
            '</div>';
          return;
        }
        doel.innerHTML = body.keuringen.map(keuringKaart).join('');
      })
      .catch(function (e) {
        doel.innerHTML = '<div class="empty-state"><h3>Laden mislukt</h3><p>' +
          esc(foutTekst(e)) + '</p></div>';
      });
  }

  function keuringKaart(k) {
    var voortgang = (k.voortgang === null || k.voortgang === undefined)
      ? '<span style="color:var(--text-dim);font-size:12px;">geen verwacht aantal</span>'
      : '<div style="display:flex;align-items:center;gap:8px;">' +
          '<div style="flex:1;height:6px;background:var(--border);border-radius:999px;overflow:hidden;">' +
            '<div style="width:' + k.voortgang + '%;height:100%;background:var(--accent,#0ea5e9);"></div>' +
          '</div><span style="font-size:12px;font-weight:600;">' + k.voortgang + '%</span></div>';

    var tel = k.registraties || {};
    var afwijkend = tel.afwijkend
      ? ' &middot; <span style="color:var(--danger,#dc2626);font-weight:600;">' +
        tel.afwijkend + ' afwijkend</span>'
      : '';

    return '<div class="card" style="margin-bottom:12px;cursor:pointer;" ' +
      'onclick="kwOpenKeuring(\'' + k.id + '\')">' +
      '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start;">' +
        '<div style="flex:1;min-width:200px;">' +
          '<h3 style="margin:0 0 4px;font-size:16px;">' + esc(k.naam) + '</h3>' +
          '<p style="margin:0 0 8px;font-size:12px;color:var(--text-dim);">' +
            esc(k.werksoort_label) +
            (k.project_naam ? ' &middot; ' + esc(k.project_naam) : '') +
            (k.werkvak ? ' &middot; ' + esc(k.werkvak) : '') +
            ' &middot; ' + esc(k.frequentie_label || '') +
          '</p>' +
          '<p style="margin:0;font-size:12px;color:var(--text-dim);">' +
            k.aantal_velden + ' velden &middot; ' + k.aantal_eisen + ' eisen &middot; ' +
            (tel.uitgevoerd || 0) + ' van ' + (k.verwacht_aantal || '?') + ' uitgevoerd' +
            afwijkend +
          '</p>' +
        '</div>' +
        '<div style="min-width:140px;">' + voortgang + '</div>' +
      '</div></div>';
  }

  // ── Een keuring aanmaken ─────────────────────────────────────────

  function kwNieuweKeuring() {
    if (!CFG) { kwOpen(); return; }
    api('GET', '/api/kwaliteit/templates').then(function (templates) {
      toonNieuwFormulier(templates);
    }).catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function toonNieuwFormulier(templates) {
    var doel = el('kwDetail');
    var perWerksoort = {};
    templates.forEach(function (t) {
      (perWerksoort[t.werksoort_label] = perWerksoort[t.werksoort_label] || []).push(t);
    });

    var opties = Object.keys(perWerksoort).map(function (groep) {
      return '<optgroup label="' + esc(groep) + '">' +
        perWerksoort[groep].map(function (t) {
          return '<option value="' + esc(t.code) + '">' + esc(t.naam) +
            ' (' + t.aantal_velden + ' velden, ' + t.aantal_eisen + ' eisen)</option>';
        }).join('') + '</optgroup>';
    }).join('');

    var freq = CFG.frequenties.map(function (f) {
      return '<option value="' + esc(f.code) + '">' + esc(f.label) + '</option>';
    }).join('');

    doel.innerHTML =
      '<div class="card">' +
made_veld('Standaardkeuring',
        '<select id="kwNieuwTemplate" style="' + inputStijl() + '">' +
          '<option value="">Leeg beginnen</option>' + opties + '</select>' +
        '<p style="margin:6px 0 0;font-size:12px;color:var(--text-dim);">' +
        'Een sjabloon vult de velden en eisen alvast. Aanpassen mag daarna; het ' +
        'sjabloon verandert er niet van mee.</p>') +
      made_veld('Naam van de keuring', '<input id="kwNieuwNaam" type="text" ' +
        'placeholder="Bijv. Controle fundering N201" style="' + inputStijl() + '">') +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;">' +
        made_veld('Keuringsnummer (optioneel)',
          '<input id="kwNieuwNummer" type="text" style="' + inputStijl() + '">') +
        made_veld('Werkvak of locatie (optioneel)',
          '<input id="kwNieuwWerkvak" type="text" placeholder="Werkvak 03" style="' + inputStijl() + '">') +
        made_veld('Frequentie',
          '<select id="kwNieuwFreq" style="' + inputStijl() + '">' + freq + '</select>') +
        made_veld('Verwacht aantal registraties',
          '<input id="kwNieuwVerwacht" type="number" min="0" placeholder="bijv. 25" style="' + inputStijl() + '">' +
          '<p style="margin:6px 0 0;font-size:12px;color:var(--text-dim);">' +
          'Leeg laten mag. Zonder dit getal is er geen percentage te tonen.</p>') +
      '</div>' +
      '<div style="margin-top:16px;display:flex;gap:8px;justify-content:flex-end;">' +
        '<button class="btn-secondary" onclick="kwTerug()">Annuleren</button>' +
        '<button class="btn-primary" onclick="kwBewaarNieuw()">Keuring aanmaken</button>' +
      '</div></div>';
    toon('kwDetail');
  }

  function inputStijl() {
    return 'width:100%;padding:9px 10px;border:1px solid var(--border);border-radius:8px;' +
      'background:var(--card-bg);color:var(--text);font-family:inherit;font-size:14px;';
  }

  function made_veld(label, inhoud) {
    return '<div style="margin-bottom:14px;"><label style="display:block;font-size:12px;' +
      'color:var(--text-dim);margin-bottom:4px;font-weight:600;">' + esc(label) + '</label>' +
      inhoud + '</div>';
  }

  function kwBewaarNieuw() {
    var payload = {
      template_code: el('kwNieuwTemplate').value || null,
      naam: el('kwNieuwNaam').value.trim() || null,
      keuringsnummer: el('kwNieuwNummer').value.trim() || null,
      werkvak: el('kwNieuwWerkvak').value.trim() || null,
      frequentie: el('kwNieuwFreq').value || null,
      verwacht_aantal: el('kwNieuwVerwacht').value ? parseInt(el('kwNieuwVerwacht').value, 10) : null
    };
    if (!payload.template_code && !payload.naam) {
      melding('Geef de keuring een naam of kies een sjabloon', 'error');
      return;
    }
    api('POST', '/api/kwaliteit/keuringen', payload).then(function (k) {
      melding('Keuring aangemaakt', 'success');
      kwOpenKeuring(k.id);
    }).catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  // ── Scherm 2: één keuring ────────────────────────────────────────

  function kwOpenKeuring(id) {
    var doel = el('kwDetail');
    doel.innerHTML = '<div class="loading-spinner">Laden...</div>';
    toon('kwDetail');

    Promise.all([
      api('GET', '/api/kwaliteit/keuringen/' + id),
      api('GET', '/api/kwaliteit/registraties?keuring_id=' + encodeURIComponent(id))
    ]).then(function (res) {
      huidigeKeuring = res[0];
      tekenKeuring(res[0], res[1].registraties);
    }).catch(function (e) {
      doel.innerHTML = '<div class="empty-state"><h3>Niet gevonden</h3><p>' +
        esc(foutTekst(e)) + '</p></div>';
    });
  }

  function tekenKeuring(k, registraties) {
    var tel = k.registraties || {};

    var velden = k.velden.length
      ? k.velden.map(veldRegel).join('')
      : '<p style="color:var(--text-dim);font-size:13px;">Nog geen velden. Voeg er een toe ' +
        'voordat je kunt registreren.</p>';

    var eisen = k.eisen.length
      ? k.eisen.map(eisRegel).join('')
      : '<p style="color:var(--text-dim);font-size:13px;">Nog geen eisen vastgelegd.</p>';

    var regs = registraties.length
      ? registraties.map(registratieRegel).join('')
      : '<p style="color:var(--text-dim);font-size:13px;">Nog geen registratie uitgevoerd.</p>';

    el('kwDetail').innerHTML =
      '<div class="card" style="margin-bottom:16px;">' +
        '<h2 style="margin:0 0 4px;font-size:20px;">' + esc(k.naam) + '</h2>' +
        '<p style="margin:0 0 12px;font-size:13px;color:var(--text-dim);">' +
          esc(k.werksoort_label) +
          (k.keuringsnummer ? ' &middot; nr. ' + esc(k.keuringsnummer) : '') +
          ' &middot; ' + esc(k.frequentie_label || '') +
          (k.verwacht_aantal ? ' &middot; ' + k.verwacht_aantal + ' verwacht' : '') +
        '</p>' +
        (k.omschrijving ? '<p style="margin:0 0 12px;font-size:13px;">' + esc(k.omschrijving) + '</p>' : '') +
        '<div style="display:flex;flex-wrap:wrap;gap:16px;font-size:13px;margin-bottom:14px;">' +
          kpi('Uitgevoerd', (tel.uitgevoerd || 0) + (k.verwacht_aantal ? ' / ' + k.verwacht_aantal : '')) +
          kpi('Goedgekeurd', tel.goedgekeurd || 0) +
          kpi('Afgekeurd', tel.afgekeurd || 0) +
          kpi('Afwijkend', tel.afwijkend || 0, tel.afwijkend ? 'var(--danger,#dc2626)' : null) +
        '</div>' +
        '<button class="btn-primary" onclick="kwStartRegistratie(\'' + k.id + '\')">' +
          'Registratie starten</button>' +
      '</div>' +

      '<div class="card" style="margin-bottom:16px;">' +
        '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">' +
          '<h3 style="margin:0;">Eisen</h3>' +
          '<button class="btn-secondary" onclick="kwNieuweEis()">+ Eis</button>' +
        '</div>' + eisen +
      '</div>' +

      '<div class="card" style="margin-bottom:16px;">' +
        '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">' +
          '<h3 style="margin:0;">Velden</h3>' +
          '<button class="btn-secondary" onclick="kwNieuwVeld()">+ Veld</button>' +
        '</div>' + velden +
      '</div>' +

      '<div class="card">' +
        '<div class="card-header"><h3 style="margin:0;">Registraties</h3></div>' +
        regs +
      '</div>';
  }

  function kpi(label, waarde, kleur) {
    return '<div><div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;' +
      'letter-spacing:.04em;">' + esc(label) + '</div>' +
      '<div style="font-size:20px;font-weight:700;' + (kleur ? 'color:' + kleur + ';' : '') + '">' +
      esc(waarde) + '</div></div>';
  }

  function veldRegel(v) {
    var norm = '';
    if (v.norm_min !== null || v.norm_max !== null) {
      var d = [];
      if (v.norm_min !== null) d.push('min ' + v.norm_min);
      if (v.norm_max !== null) d.push('max ' + v.norm_max);
      if (v.tolerantie) d.push('± ' + v.tolerantie);
      norm = '<span style="color:var(--success,#16a34a);">' + esc(d.join(' / ')) +
        (v.eenheid ? ' ' + esc(v.eenheid) : '') + '</span>';
    } else if (v.veldtype === 'meetwaarde') {
      // Geen norm = geen oordeel. Dat hoort de beheerder te zien.
      norm = '<span style="color:var(--warning,#d97706);">geen norm ingevuld — ' +
        'deze meting krijgt geen oordeel</span>';
    }

    return '<div style="display:flex;justify-content:space-between;gap:12px;padding:9px 0;' +
      'border-bottom:1px solid var(--border);font-size:13px;align-items:flex-start;">' +
      '<div style="flex:1;">' +
        '<strong>' + esc(v.label) + '</strong>' +
        (v.verplicht ? ' <span style="color:var(--danger,#dc2626);">*</span>' : '') +
        '<div style="font-size:12px;color:var(--text-dim);">' +
          esc(v.veldtype_label) + (v.eenheid ? ' &middot; ' + esc(v.eenheid) : '') +
          (norm ? ' &middot; ' + norm : '') +
        '</div>' +
      '</div>' +
      '<button class="btn-secondary" style="padding:4px 10px;font-size:12px;" ' +
        'onclick="kwWijzigVeld(\'' + v.id + '\')">Wijzig</button>' +
      '</div>';
  }

  function eisRegel(e) {
    return '<div style="padding:9px 0;border-bottom:1px solid var(--border);font-size:13px;">' +
      '<strong>' + esc(e.eisnummer) + ' &middot; ' + esc(e.titel) + '</strong>' +
      (e.bewijs_vereist
        ? ' ' + badge('bewijs vereist', 'var(--warning,#d97706)')
        : '') +
      (e.omschrijving ? '<div style="color:var(--text-dim);margin-top:2px;">' +
        esc(e.omschrijving) + '</div>' : '') +
      '<div style="font-size:12px;color:var(--text-dim);margin-top:2px;">' +
        (e.norm ? 'Norm: ' + esc(e.norm) + ' &middot; ' : '') +
        (e.bron ? 'Bron: ' + esc(e.bron) : '') +
        (e.meetmethode ? ' &middot; ' + esc(e.meetmethode) : '') +
      '</div></div>';
  }

  function registratieRegel(r) {
    return '<div style="display:flex;justify-content:space-between;gap:12px;padding:10px 0;' +
      'border-bottom:1px solid var(--border);font-size:13px;cursor:pointer;align-items:center;" ' +
      'onclick="kwOpenRegistratie(\'' + r.id + '\')">' +
      '<div><strong>#' + (r.volgnummer || '?') + '</strong> &middot; ' + esc(datumNL(r.datum)) +
        (r.werkvak ? ' &middot; ' + esc(r.werkvak) : '') +
        '<div style="font-size:12px;color:var(--text-dim);">' +
          esc(r.uitvoerder_naam || '') + ' &middot; ' +
          r.aantal_ingevuld + '/' + r.aantal_velden + ' ingevuld &middot; ' +
          r.aantal_bewijs + ' bewijsstuk' + (r.aantal_bewijs === 1 ? '' : 'ken') +
        '</div></div>' +
      '<div style="display:flex;gap:6px;align-items:center;">' +
        (r.resultaat ? badge(resultaatLabel(r.resultaat), resultaatKleur(r.resultaat)) : '') +
        statusBadge(r.status) +
      '</div></div>';
  }

  // ── Velden en eisen toevoegen ────────────────────────────────────

  function kwNieuwVeld() {
    if (!huidigeKeuring) return;
    var types = CFG.veldtypes.map(function (t) {
      return '<option value="' + esc(t.code) + '">' + esc(t.label) + '</option>';
    }).join('');
    var eisen = '<option value="">Geen</option>' + huidigeKeuring.eisen.map(function (e) {
      return '<option value="' + esc(e.id) + '">' + esc(e.eisnummer + ' ' + e.titel) + '</option>';
    }).join('');

    var html =
      made_veld('Wat moet er ingevuld worden?',
        '<input id="kwVeldLabel" type="text" placeholder="Bijv. Verdichtingsgraad" style="' + inputStijl() + '">') +
      made_veld('Soort veld',
        '<select id="kwVeldType" onchange="kwVeldTypeGewijzigd()" style="' + inputStijl() + '">' + types + '</select>') +
      '<div id="kwVeldOptiesBlok" style="display:none;">' +
        made_veld('Keuzes (één per regel)',
          '<textarea id="kwVeldOpties" rows="4" style="' + inputStijl() + '"></textarea>') +
      '</div>' +
      '<div id="kwVeldNormBlok" style="display:none;">' +
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;">' +
          made_veld('Eenheid', '<input id="kwVeldEenheid" type="text" placeholder="%, mm, °C" style="' + inputStijl() + '">') +
          made_veld('Minimaal', '<input id="kwVeldMin" type="number" step="any" style="' + inputStijl() + '">') +
          made_veld('Maximaal', '<input id="kwVeldMax" type="number" step="any" style="' + inputStijl() + '">') +
          made_veld('Tolerantie', '<input id="kwVeldTol" type="number" step="any" min="0" style="' + inputStijl() + '">') +
        '</div>' +
        '<p style="margin:-6px 0 14px;font-size:12px;color:var(--warning,#d97706);">' +
        'Vul de grenzen in uit het bestek. Zonder norm krijgt de meting geen oordeel — ' +
        'het systeem zegt dan niet "akkoord".</p>' +
      '</div>' +
      made_veld('Hoort bij eis (optioneel)',
        '<select id="kwVeldEis" style="' + inputStijl() + '">' + eisen + '</select>') +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:14px;">' +
        '<input id="kwVeldVerplicht" type="checkbox"> Verplicht invullen voor indienen</label>';

    modal('Veld toevoegen', html, function () {
      var type = el('kwVeldType').value;
      var opties = el('kwVeldOpties').value.split('\n')
        .map(function (s) { return s.trim(); }).filter(Boolean);
      var payload = {
        label: el('kwVeldLabel').value.trim(),
        veldtype: type,
        verplicht: el('kwVeldVerplicht').checked,
        eis_id: el('kwVeldEis').value || null,
        opties: opties.length ? opties : null,
        eenheid: el('kwVeldEenheid').value.trim() || null,
        norm_min: el('kwVeldMin').value !== '' ? parseFloat(el('kwVeldMin').value) : null,
        norm_max: el('kwVeldMax').value !== '' ? parseFloat(el('kwVeldMax').value) : null,
        tolerantie: el('kwVeldTol').value !== '' ? parseFloat(el('kwVeldTol').value) : null
      };
      if (!payload.label) { melding('Geef het veld een naam', 'error'); return false; }
      api('POST', '/api/kwaliteit/keuringen/' + huidigeKeuring.id + '/velden', payload)
        .then(function () {
          melding('Veld toegevoegd', 'success');
          kwOpenKeuring(huidigeKeuring.id);
        }).catch(function (e) { melding(foutTekst(e), 'error'); });
      return true;
    });
    kwVeldTypeGewijzigd();
  }

  function kwVeldTypeGewijzigd() {
    var type = el('kwVeldType') ? el('kwVeldType').value : '';
    var def = (CFG.veldtypes || []).filter(function (t) { return t.code === type; })[0] || {};
    var opties = el('kwVeldOptiesBlok');
    var norm = el('kwVeldNormBlok');
    if (opties) opties.style.display = def.heeft_opties ? '' : 'none';
    if (norm) norm.style.display = def.heeft_norm ? '' : 'none';
  }

  function kwWijzigVeld(veldId) {
    var v = huidigeKeuring.velden.filter(function (x) { return x.id === veldId; })[0];
    if (!v) return;
    var heeftNorm = (v.veldtype === 'meetwaarde' || v.veldtype === 'getal');

    var html =
      made_veld('Naam', '<input id="kwEdLabel" type="text" value="' + esc(v.label) + '" style="' + inputStijl() + '">') +
      (heeftNorm
        ? '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;">' +
            made_veld('Eenheid', '<input id="kwEdEenheid" type="text" value="' + esc(v.eenheid || '') + '" style="' + inputStijl() + '">') +
            made_veld('Minimaal', '<input id="kwEdMin" type="number" step="any" value="' + (v.norm_min !== null ? v.norm_min : '') + '" style="' + inputStijl() + '">') +
            made_veld('Maximaal', '<input id="kwEdMax" type="number" step="any" value="' + (v.norm_max !== null ? v.norm_max : '') + '" style="' + inputStijl() + '">') +
            made_veld('Tolerantie', '<input id="kwEdTol" type="number" step="any" min="0" value="' + (v.tolerantie !== null ? v.tolerantie : '') + '" style="' + inputStijl() + '">') +
          '</div>'
        : '') +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:14px;">' +
        '<input id="kwEdVerplicht" type="checkbox"' + (v.verplicht ? ' checked' : '') +
        '> Verplicht invullen voor indienen</label>' +
      '<button class="btn-secondary" style="color:var(--danger,#dc2626);" ' +
        'onclick="kwVerwijderVeld(\'' + v.id + '\')">Veld verwijderen</button>';

    modal('Veld wijzigen', html, function () {
      var payload = {
        label: el('kwEdLabel').value.trim(),
        verplicht: el('kwEdVerplicht').checked
      };
      if (heeftNorm) {
        payload.eenheid = el('kwEdEenheid').value.trim() || null;
        payload.norm_min = el('kwEdMin').value !== '' ? parseFloat(el('kwEdMin').value) : null;
        payload.norm_max = el('kwEdMax').value !== '' ? parseFloat(el('kwEdMax').value) : null;
        payload.tolerantie = el('kwEdTol').value !== '' ? parseFloat(el('kwEdTol').value) : null;
      }
      api('PATCH', '/api/kwaliteit/keuringen/' + huidigeKeuring.id + '/velden/' + v.id, payload)
        .then(function () {
          melding('Veld bijgewerkt', 'success');
          kwOpenKeuring(huidigeKeuring.id);
        }).catch(function (e) { melding(foutTekst(e), 'error'); });
      return true;
    });
  }

  function kwVerwijderVeld(veldId) {
    if (!confirm('Dit veld en de antwoorden erop verdwijnen. Doorgaan?')) return;
    api('DELETE', '/api/kwaliteit/keuringen/' + huidigeKeuring.id + '/velden/' + veldId)
      .then(function () {
        sluitModal();
        melding('Veld verwijderd', 'success');
        kwOpenKeuring(huidigeKeuring.id);
      }).catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function kwNieuweEis() {
    if (!huidigeKeuring) return;
    var html =
      made_veld('Waaraan moet worden voldaan?',
        '<input id="kwEisTitel" type="text" placeholder="Bijv. Fundering verdichting" style="' + inputStijl() + '">') +
      made_veld('Toelichting (optioneel)',
        '<textarea id="kwEisOmschrijving" rows="2" style="' + inputStijl() + '"></textarea>') +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;">' +
        made_veld('Norm', '<input id="kwEisNorm" type="text" placeholder="Bijv. NEN-EN 1610" style="' + inputStijl() + '">') +
        made_veld('Bron', '<input id="kwEisBron" type="text" placeholder="Bestek, RAW" style="' + inputStijl() + '">') +
        made_veld('Tolerantie', '<input id="kwEisTol" type="text" placeholder="-5 / +10 mm" style="' + inputStijl() + '">') +
        made_veld('Meetmethode', '<input id="kwEisMeet" type="text" placeholder="Waterpas" style="' + inputStijl() + '">') +
      '</div>' +
      '<label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:6px;">' +
        '<input id="kwEisBewijs" type="checkbox"> Bewijs verplicht</label>' +
      '<p style="margin:0 0 14px;font-size:12px;color:var(--text-dim);">' +
      'Staat dit aan, dan kan een registratie niet worden ingediend zonder bewijsstuk ' +
      'bij deze eis.</p>';

    modal('Eis toevoegen', html, function () {
      var payload = {
        titel: el('kwEisTitel').value.trim(),
        omschrijving: el('kwEisOmschrijving').value.trim() || null,
        norm: el('kwEisNorm').value.trim() || null,
        bron: el('kwEisBron').value.trim() || null,
        tolerantie: el('kwEisTol').value.trim() || null,
        meetmethode: el('kwEisMeet').value.trim() || null,
        bewijs_vereist: el('kwEisBewijs').checked
      };
      if (!payload.titel) { melding('Geef de eis een titel', 'error'); return false; }
      api('POST', '/api/kwaliteit/keuringen/' + huidigeKeuring.id + '/eisen', payload)
        .then(function () {
          melding('Eis toegevoegd', 'success');
          kwOpenKeuring(huidigeKeuring.id);
        }).catch(function (e) { melding(foutTekst(e), 'error'); });
      return true;
    });
  }

  // ── Scherm 3: registreren ────────────────────────────────────────

  function kwStartRegistratie(keuringId) {
    var payload = {};
    // GPS meepakken als het kan. Lukt het niet, dan gaat de registratie
    // gewoon door -- een keuring die stukloopt op een geo-permissie is
    // een keuring die niet gedaan wordt.
    var start = function () {
      api('POST', '/api/kwaliteit/keuringen/' + keuringId + '/registraties', payload)
        .then(function (r) {
          huidigeRegistratie = r;
          tekenRegistratie(r);
          toon('kwRegistratie');
        })
        .catch(function (e) { melding(foutTekst(e), 'error'); });
    };
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(function (pos) {
        payload.lat = pos.coords.latitude;
        payload.lng = pos.coords.longitude;
        start();
      }, start, { timeout: 5000, maximumAge: 60000 });
    } else {
      start();
    }
  }

  function kwOpenRegistratie(id) {
    el('kwRegistratie').innerHTML = '<div class="loading-spinner">Laden...</div>';
    toon('kwRegistratie');
    api('GET', '/api/kwaliteit/registraties/' + id).then(function (r) {
      huidigeRegistratie = r;
      tekenRegistratie(r);
    }).catch(function (e) {
      el('kwRegistratie').innerHTML = '<div class="empty-state"><h3>Niet gevonden</h3><p>' +
        esc(foutTekst(e)) + '</p></div>';
    });
  }

  function tekenRegistratie(r) {
    var vast = (r.status === 'goedgekeurd' || r.status === 'afgekeurd');

    var eisenMetBewijs = {};
    (r.bewijs || []).forEach(function (b) { if (b.eis_id) eisenMetBewijs[b.eis_id] = true; });

    var eisenBlok = (r.eisen || []).filter(function (e) { return e.bewijs_vereist; })
      .map(function (e) {
        var heeft = !!eisenMetBewijs[e.id];
        return '<div style="display:flex;justify-content:space-between;gap:10px;padding:8px 0;' +
          'border-bottom:1px solid var(--border);font-size:13px;align-items:center;">' +
          '<div><strong>' + esc(e.eisnummer) + '</strong> ' + esc(e.titel) +
            (e.norm ? '<div style="font-size:12px;color:var(--text-dim);">' + esc(e.norm) + '</div>' : '') +
          '</div>' +
          (heeft
            ? badge('bewijs aanwezig', 'var(--success,#16a34a)')
            : (vast ? badge('geen bewijs', 'var(--danger,#dc2626)')
                    : '<button class="btn-secondary" style="padding:5px 10px;font-size:12px;" ' +
                      'onclick="kwVoegBewijsToe(\'' + e.id + '\')">Bewijs toevoegen</button>')) +
          '</div>';
      }).join('');

    var acties = '';
    if (!vast && r.status !== 'ingediend' && r.status !== 'in_beoordeling') {
      acties = '<button class="btn-primary" style="width:100%;padding:14px;font-size:16px;" ' +
        'onclick="kwIndienen()">Keuring indienen</button>';
    } else if (r.status === 'ingediend' || r.status === 'in_beoordeling') {
      acties =
        '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
          '<button class="btn-primary" style="flex:1;" onclick="kwBeoordeel(\'goedkeuren\')">Goedkeuren</button>' +
          '<button class="btn-secondary" style="flex:1;" onclick="kwBeoordeel(\'terugsturen\')">Terugsturen</button>' +
          '<button class="btn-secondary" style="flex:1;color:var(--danger,#dc2626);" ' +
            'onclick="kwBeoordeel(\'afkeuren\')">Afkeuren</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--text-dim);margin:8px 0 0;">' +
        'Alleen een toezichthouder, projectleider of beheerder kan beoordelen.</p>';
    }

    el('kwRegistratie').innerHTML =
      '<div class="card" style="margin-bottom:14px;">' +
        '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:flex-start;">' +
          '<div><h2 style="margin:0 0 3px;font-size:18px;">' + esc(r.keuring_naam) + '</h2>' +
            '<p style="margin:0;font-size:13px;color:var(--text-dim);">Registratie #' +
              (r.volgnummer || '?') + ' &middot; ' + esc(datumNL(r.datum)) +
              (r.werkvak ? ' &middot; ' + esc(r.werkvak) : '') +
              ' &middot; ' + esc(r.uitvoerder_naam || '') + '</p></div>' +
          '<div style="display:flex;gap:6px;">' +
            (r.resultaat ? badge(resultaatLabel(r.resultaat), resultaatKleur(r.resultaat)) : '') +
            statusBadge(r.status) +
          '</div>' +
        '</div>' +
        (r.beoordeling_reden
          ? '<div style="margin-top:10px;padding:10px;border-radius:8px;background:var(--card-bg);' +
            'border:1px solid var(--danger,#dc2626);font-size:13px;"><strong>Reden:</strong> ' +
            esc(r.beoordeling_reden) + '</div>'
          : '') +
      '</div>' +

      (eisenBlok
        ? '<div class="card" style="margin-bottom:14px;">' +
            '<div class="card-header"><h3 style="margin:0;">Bewijs dat verplicht is</h3></div>' +
            eisenBlok + '</div>'
        : '') +

      '<div class="card" style="margin-bottom:14px;">' +
        '<div class="card-header"><h3 style="margin:0;">Controlepunten</h3></div>' +
        (r.antwoorden || []).map(function (a) { return antwoordVeld(a, vast); }).join('') +
      '</div>' +

      '<div class="card" style="margin-bottom:14px;">' +
        '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;">' +
          '<h3 style="margin:0;">Bewijsmateriaal (' + (r.bewijs || []).length + ')</h3>' +
          (vast ? '' : '<button class="btn-secondary" onclick="kwVoegBewijsToe(null)">+ Toevoegen</button>') +
        '</div>' +
        ((r.bewijs || []).length
          ? r.bewijs.map(bewijsRegel).join('')
          : '<p style="color:var(--text-dim);font-size:13px;">Nog geen bewijs toegevoegd.</p>') +
      '</div>' +

      '<div class="card">' + acties + '</div>';
  }

  function antwoordVeld(a, vast) {
    var oordeel = '';
    if (a.oordeel === 'niet_akkoord') {
      oordeel = '<div style="margin-top:6px;padding:8px 10px;border-radius:8px;font-size:12px;' +
        'border:1px solid var(--danger,#dc2626);color:var(--danger,#dc2626);">' +
        'Buiten de norm' +
        (a.norm_min !== null && a.norm_min !== undefined ? ' (minimaal ' + a.norm_min : '') +
        (a.norm_max !== null && a.norm_max !== undefined ? (a.norm_min !== null ? ', maximaal ' : ' (maximaal ') + a.norm_max : '') +
        (a.norm_min !== null || a.norm_max !== null ? ')' : '') +
        ' — leg vast wat er aan de hand is en voeg bewijs toe.</div>';
    } else if (a.oordeel === 'akkoord') {
      oordeel = '<div style="margin-top:4px;font-size:12px;color:var(--success,#16a34a);">Akkoord</div>';
    } else if (a.oordeel === 'niet_beoordeeld' && a.waarde !== null) {
      oordeel = '<div style="margin-top:4px;font-size:12px;color:var(--warning,#d97706);">' +
        'Vastgelegd, maar er is geen norm om aan te toetsen.</div>';
    }

    var invoer = invoerVoorType(a, vast);
    var norm = '';
    if (a.norm_min !== null || a.norm_max !== null) {
      var d = [];
      if (a.norm_min !== null && a.norm_min !== undefined) d.push('min ' + a.norm_min);
      if (a.norm_max !== null && a.norm_max !== undefined) d.push('max ' + a.norm_max);
      if (a.tolerantie) d.push('± ' + a.tolerantie);
      norm = ' <span style="color:var(--text-dim);font-weight:400;">(' + esc(d.join(' / ')) + ')</span>';
    }

    return '<div style="padding:12px 0;border-bottom:1px solid var(--border);">' +
      '<label style="display:block;font-size:14px;font-weight:600;margin-bottom:6px;">' +
        esc(a.label) + (a.eenheid ? ' <span style="color:var(--text-dim);">in ' + esc(a.eenheid) + '</span>' : '') +
        norm +
      '</label>' + invoer + oordeel +
      '</div>';
  }

  function invoerVoorType(a, vast) {
    var id = 'kwA_' + a.id;
    var uit = vast ? ' disabled' : '';
    var bewaar = ' onchange="kwBewaarAntwoord(\'' + a.id + '\')"';
    var stijl = inputStijl() + 'font-size:16px;padding:12px;';

    if (a.veldtype === 'ja_nee') {
      return knopGroep(a, [['true', 'Ja'], ['false', 'Nee']], vast);
    }
    if (a.veldtype === 'ja_nee_nvt') {
      return knopGroep(a, [['ja', 'Ja'], ['nee', 'Nee'], ['nvt', 'N.v.t.']], vast);
    }
    if (a.veldtype === 'akkoord') {
      return knopGroep(a, [['akkoord', 'Akkoord'], ['niet_akkoord', 'Niet akkoord'], ['nvt', 'N.v.t.']], vast);
    }
    if (a.veldtype === 'keuring') {
      return knopGroep(a, [['goed', 'Goed'], ['afgekeurd', 'Afgekeurd'], ['nvt', 'N.v.t.']], vast);
    }
    if (a.veldtype === 'meetwaarde' || a.veldtype === 'getal') {
      return '<input id="' + id + '" type="number" step="any" inputmode="decimal" value="' +
        (a.waarde !== null && a.waarde !== undefined ? esc(a.waarde) : '') + '"' +
        uit + bewaar + ' style="' + stijl + '">';
    }
    if (a.veldtype === 'tekst_lang') {
      return '<textarea id="' + id + '" rows="3"' + uit + bewaar + ' style="' + stijl + '">' +
        esc(a.waarde || '') + '</textarea>';
    }
    if (a.veldtype === 'datum' || a.veldtype === 'datumtijd') {
      var v = a.waarde ? String(a.waarde).slice(0, a.veldtype === 'datum' ? 10 : 16) : '';
      return '<input id="' + id + '" type="' + (a.veldtype === 'datum' ? 'date' : 'datetime-local') +
        '" value="' + esc(v) + '"' + uit + bewaar + ' style="' + stijl + '">';
    }
    if (a.veldtype === 'tijd') {
      return '<input id="' + id + '" type="time" value="' + esc(a.waarde || '') + '"' +
        uit + bewaar + ' style="' + stijl + '">';
    }
    if (a.veldtype === 'foto') {
      return (a.heeft_foto
          ? '<div style="margin-bottom:8px;"><img src="' + esc(a.photo_url) + '" ' +
            'style="max-width:100%;max-height:220px;border-radius:8px;border:1px solid var(--border);"></div>'
          : '') +
        (vast ? '' :
          '<input id="' + id + '" type="file" accept="image/*" capture="environment" ' +
          'onchange="kwFotoGekozen(\'' + a.id + '\', this)" style="' + inputStijl() + '">');
    }
    if (a.veldtype === 'bestand') {
      return (a.waarde ? '<div style="font-size:13px;margin-bottom:6px;">Bestand toegevoegd</div>' : '') +
        (vast ? '' :
          '<input id="' + id + '" type="file" accept=".pdf,image/*" ' +
          'onchange="kwFotoGekozen(\'' + a.id + '\', this)" style="' + inputStijl() + '">');
    }
    if (a.veldtype === 'locatie') {
      return '<div style="display:flex;gap:8px;align-items:center;">' +
        '<span style="font-size:13px;">' + (a.waarde ? esc(a.waarde) : 'Nog niet vastgelegd') + '</span>' +
        (vast ? '' : '<button class="btn-secondary" style="padding:6px 12px;" ' +
          'onclick="kwPakLocatie(\'' + a.id + '\')">Locatie vastleggen</button>') +
        '</div>';
    }
    if (a.veldtype === 'keuze' || a.veldtype === 'meerkeuze' || a.veldtype === 'checklist') {
      // Opties staan op de velddefinitie; die zit in de keuring, niet in het
      // antwoord. Zonder geladen keuring vallen we terug op vrije tekst.
      var veld = (huidigeKeuring && huidigeKeuring.velden || [])
        .filter(function (v) { return v.id === a.veld_id; })[0];
      var opties = (veld && veld.opties) || [];
      if (a.veldtype === 'keuze') {
        return '<select id="' + id + '"' + uit + bewaar + ' style="' + stijl + '">' +
          '<option value="">Kies...</option>' +
          opties.map(function (o) {
            return '<option value="' + esc(o) + '"' + (a.waarde === o ? ' selected' : '') + '>' +
              esc(o) + '</option>';
          }).join('') + '</select>';
      }
      var gekozen = Array.isArray(a.waarde) ? a.waarde : [];
      return '<div id="' + id + '">' + opties.map(function (o, i) {
        return '<label style="display:flex;gap:10px;align-items:center;padding:10px 0;font-size:15px;">' +
          '<input type="checkbox" value="' + esc(o) + '"' +
          (gekozen.indexOf(o) !== -1 ? ' checked' : '') + uit +
          ' onchange="kwBewaarAntwoord(\'' + a.id + '\')" ' +
          'style="width:22px;height:22px;"> ' + esc(o) + '</label>';
      }).join('') + '</div>';
    }
    // tekst_kort, handtekening en de rest
    return '<input id="' + id + '" type="text" value="' + esc(a.waarde || '') + '"' +
      uit + bewaar + ' style="' + stijl + '">';
  }

  function knopGroep(a, keuzes, vast) {
    return '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + keuzes.map(function (k) {
      var waarde = k[0], label = k[1];
      var actief = String(a.waarde) === waarde ||
        (a.veldtype === 'ja_nee' && a.waarde !== null && String(!!a.waarde) === waarde);
      var kleur = actief
        ? (waarde === 'nee' || waarde === 'false' || waarde === 'niet_akkoord' || waarde === 'afgekeurd'
            ? 'var(--danger,#dc2626)' : 'var(--success,#16a34a)')
        : 'var(--border)';
      return '<button type="button"' + (vast ? ' disabled' : '') +
        ' onclick="kwZetKeuze(\'' + a.id + '\', \'' + waarde + '\')" ' +
        'style="flex:1;min-width:90px;padding:14px 10px;border-radius:10px;font-size:15px;' +
        'font-weight:600;cursor:pointer;background:' + (actief ? kleur : 'var(--card-bg)') + ';' +
        'color:' + (actief ? '#fff' : 'var(--text)') + ';border:2px solid ' + kleur + ';">' +
        esc(label) + '</button>';
    }).join('') + '</div>';
  }

  // ── Antwoorden opslaan ───────────────────────────────────────────

  function kwZetKeuze(antwoordId, waarde) {
    var a = vindAntwoord(antwoordId);
    if (!a) return;
    var w = waarde;
    if (a.veldtype === 'ja_nee') w = (waarde === 'true');
    stuurAntwoord(antwoordId, { waarde: w });
  }

  function kwBewaarAntwoord(antwoordId) {
    var a = vindAntwoord(antwoordId);
    if (!a) return;
    var node = el('kwA_' + antwoordId);
    if (!node) return;
    var waarde;

    if (a.veldtype === 'meerkeuze' || a.veldtype === 'checklist') {
      waarde = Array.prototype.slice.call(node.querySelectorAll('input[type=checkbox]'))
        .filter(function (c) { return c.checked; })
        .map(function (c) { return c.value; });
    } else if (a.veldtype === 'meetwaarde' || a.veldtype === 'getal') {
      waarde = node.value === '' ? null : parseFloat(node.value);
    } else {
      waarde = node.value === '' ? null : node.value;
    }
    stuurAntwoord(antwoordId, { waarde: waarde });
  }

  function vindAntwoord(id) {
    return (huidigeRegistratie && huidigeRegistratie.antwoorden || [])
      .filter(function (a) { return a.id === id; })[0];
  }

  function stuurAntwoord(antwoordId, payload) {
    api('PATCH', '/api/kwaliteit/registraties/' + huidigeRegistratie.id +
        '/antwoorden/' + antwoordId, payload)
      .then(function (body) {
        // Alleen dit antwoord bijwerken en opnieuw tekenen. Een volledige
        // herlaadslag zou het toetsenbord sluiten terwijl iemand nog typt.
        var i = huidigeRegistratie.antwoorden.findIndex(function (a) { return a.id === antwoordId; });
        if (i >= 0) huidigeRegistratie.antwoorden[i] = body.antwoord;
        huidigeRegistratie.status = body.registratie.status;
        huidigeRegistratie.resultaat = body.registratie.resultaat;
        huidigeRegistratie.afwijkend = body.registratie.afwijkend;
        if (body.antwoord.oordeel === 'niet_akkoord') {
          melding('Buiten de norm — leg vast wat er aan de hand is', 'error');
        }
        tekenRegistratie(huidigeRegistratie);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function kwFotoGekozen(antwoordId, input) {
    var bestand = input.files && input.files[0];
    if (!bestand) return;
    if (bestand.size > 8 * 1024 * 1024) {
      melding('Bestand is te groot (max 8 MB)', 'error');
      return;
    }
    var lezer = new FileReader();
    lezer.onload = function () {
      stuurAntwoord(antwoordId, { photo_url: lezer.result });
    };
    lezer.readAsDataURL(bestand);
  }

  function kwPakLocatie(antwoordId) {
    if (!navigator.geolocation) { melding('Geen locatie beschikbaar', 'error'); return; }
    navigator.geolocation.getCurrentPosition(function (pos) {
      var tekst = pos.coords.latitude.toFixed(6) + ', ' + pos.coords.longitude.toFixed(6);
      stuurAntwoord(antwoordId, { waarde: tekst });
    }, function () {
      melding('Locatie niet gelukt — staat de toestemming aan?', 'error');
    }, { timeout: 8000 });
  }

  // ── Bewijs ───────────────────────────────────────────────────────

  function kwVoegBewijsToe(eisId) {
    var soorten = (CFG.bewijs_soorten || ['foto']).map(function (s) {
      return '<option value="' + esc(s) + '"' + (s === 'foto' ? ' selected' : '') + '>' +
        esc(s.charAt(0).toUpperCase() + s.slice(1)) + '</option>';
    }).join('');
    var eisen = '<option value="">Geen specifieke eis</option>' +
      ((huidigeRegistratie.eisen || []).map(function (e) {
        return '<option value="' + esc(e.id) + '"' + (e.id === eisId ? ' selected' : '') + '>' +
          esc(e.eisnummer + ' ' + e.titel) + '</option>';
      }).join(''));

    var html =
      made_veld('Soort', '<select id="kwBwSoort" style="' + inputStijl() + '">' + soorten + '</select>') +
      made_veld('Hoort bij eis', '<select id="kwBwEis" style="' + inputStijl() + '">' + eisen + '</select>') +
      made_veld('Omschrijving (optioneel)',
        '<input id="kwBwTitel" type="text" placeholder="Bijv. Meetrapport verdichting" style="' + inputStijl() + '">') +
      made_veld('Bestand of foto',
        '<input id="kwBwBestand" type="file" accept="image/*,.pdf" capture="environment" style="' + inputStijl() + '">');

    modal('Bewijs toevoegen', html, function () {
      var input = el('kwBwBestand');
      var bestand = input.files && input.files[0];
      if (!bestand) { melding('Kies een bestand of maak een foto', 'error'); return false; }
      if (bestand.size > 8 * 1024 * 1024) { melding('Bestand is te groot (max 8 MB)', 'error'); return false; }

      var lezer = new FileReader();
      lezer.onload = function () {
        api('POST', '/api/kwaliteit/registraties/' + huidigeRegistratie.id + '/bewijs', {
          soort: el('kwBwSoort').value,
          eis_id: el('kwBwEis').value || null,
          titel: el('kwBwTitel').value.trim() || bestand.name,
          bestandsnaam: bestand.name,
          mime: bestand.type || null,
          url: lezer.result
        }).then(function () {
          melding('Bewijs toegevoegd', 'success');
          kwOpenRegistratie(huidigeRegistratie.id);
        }).catch(function (e) { melding(foutTekst(e), 'error'); });
      };
      lezer.readAsDataURL(bestand);
      return true;
    });
  }

  function bewijsRegel(b) {
    return '<div style="display:flex;justify-content:space-between;gap:10px;padding:9px 0;' +
      'border-bottom:1px solid var(--border);font-size:13px;align-items:center;">' +
      '<div><strong>' + esc(b.titel || b.bestandsnaam || b.soort) + '</strong>' +
        '<div style="font-size:12px;color:var(--text-dim);">' + esc(b.soort) +
        ' &middot; ' + esc(datumNL(b.created_at)) +
        (b.aangeleverd_door ? ' &middot; ' + esc(b.aangeleverd_door) : '') + '</div></div>' +
      '<button class="btn-secondary" style="padding:5px 10px;font-size:12px;" ' +
        'onclick="kwBekijkBewijs(\'' + b.id + '\')">Bekijken</button>' +
      '</div>';
  }

  function kwBekijkBewijs(bewijsId) {
    api('GET', '/api/kwaliteit/registraties/' + huidigeRegistratie.id + '/bewijs/' + bewijsId)
      .then(function (b) {
        var inhoud = (b.mime && b.mime.indexOf('image/') === 0) || /^data:image\//.test(b.url || '')
          ? '<img src="' + esc(b.url) + '" style="max-width:100%;border-radius:8px;">'
          : '<p style="font-size:13px;">' + esc(b.bestandsnaam || 'Bestand') +
            ' — open het via de download in je browser.</p>';
        modal(b.titel || 'Bewijsstuk', inhoud, null);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  // ── Indienen en beoordelen ───────────────────────────────────────

  function kwIndienen() {
    api('POST', '/api/kwaliteit/registraties/' + huidigeRegistratie.id + '/indienen', {})
      .then(function (r) {
        huidigeRegistratie = r;
        melding('Keuring ingediend', 'success');
        tekenRegistratie(r);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function kwBeoordeel(besluit) {
    var reden = null;
    if (besluit === 'afkeuren' || besluit === 'terugsturen') {
      reden = prompt(besluit === 'afkeuren'
        ? 'Waarom keur je af? De uitvoerder moet weten wat er moet gebeuren.'
        : 'Wat moet er anders voordat dit opnieuw wordt ingediend?');
      if (reden === null) return;
      if (!reden.trim()) { melding('Geef een reden op', 'error'); return; }
    }
    api('POST', '/api/kwaliteit/registraties/' + huidigeRegistratie.id + '/beoordelen',
        { besluit: besluit, reden: reden })
      .then(function (r) {
        huidigeRegistratie = r;
        melding('Beoordeling vastgelegd', 'success');
        tekenRegistratie(r);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  // ── Navigatie binnen de module ───────────────────────────────────

  function kwTerug() {
    if (el('kwRegistratie').style.display !== 'none' && huidigeKeuring) {
      kwOpenKeuring(huidigeKeuring.id);
      return;
    }
    huidigeKeuring = null;
    huidigeRegistratie = null;
    toon('kwLijst');
    kwLaadKeuringen();
  }

  // ── Modal ────────────────────────────────────────────────────────
  // Een eigen, kleine modal: de bestaande modals in portaal.html zitten aan
  // vaste id's vast en zijn niet te hergebruiken zonder ze te verbouwen.

  var modalNode = null;
  var modalBevestig = null;

  function modal(titel, inhoud, bijOpslaan) {
    sluitModal();
    modalBevestig = bijOpslaan;
    modalNode = document.createElement('div');
    modalNode.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9000;' +
      'display:flex;align-items:center;justify-content:center;padding:16px;overflow:auto;';
    modalNode.innerHTML =
      '<div style="background:var(--card-bg,#fff);color:var(--text);border-radius:14px;' +
      'max-width:560px;width:100%;max-height:90vh;overflow:auto;padding:20px;' +
      'border:1px solid var(--border);">' +
        '<h3 style="margin:0 0 14px;font-size:18px;">' + esc(titel) + '</h3>' +
        '<div id="kwModalInhoud">' + inhoud + '</div>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">' +
          '<button class="btn-secondary" id="kwModalSluit">Sluiten</button>' +
          (bijOpslaan ? '<button class="btn-primary" id="kwModalOk">Opslaan</button>' : '') +
        '</div></div>';
    document.body.appendChild(modalNode);
    modalNode.addEventListener('click', function (e) {
      if (e.target === modalNode) sluitModal();
    });
    el('kwModalSluit').onclick = sluitModal;
    if (bijOpslaan) {
      el('kwModalOk').onclick = function () {
        if (modalBevestig() !== false) sluitModal();
      };
    }
  }

  function sluitModal() {
    if (modalNode && modalNode.parentNode) modalNode.parentNode.removeChild(modalNode);
    modalNode = null;
    modalBevestig = null;
  }

  // ── Naar buiten ──────────────────────────────────────────────────
  // portaal.html roept deze aan vanuit onclick-handlers en navigateTo().

  window.kwOpen = kwOpen;
  window.kwLaadKeuringen = kwLaadKeuringen;
  window.kwZoekDebounce = kwZoekDebounce;
  window.kwNieuweKeuring = kwNieuweKeuring;
  window.kwBewaarNieuw = kwBewaarNieuw;
  window.kwOpenKeuring = kwOpenKeuring;
  window.kwNieuwVeld = kwNieuwVeld;
  window.kwVeldTypeGewijzigd = kwVeldTypeGewijzigd;
  window.kwWijzigVeld = kwWijzigVeld;
  window.kwVerwijderVeld = kwVerwijderVeld;
  window.kwNieuweEis = kwNieuweEis;
  window.kwStartRegistratie = kwStartRegistratie;
  window.kwOpenRegistratie = kwOpenRegistratie;
  window.kwZetKeuze = kwZetKeuze;
  window.kwBewaarAntwoord = kwBewaarAntwoord;
  window.kwFotoGekozen = kwFotoGekozen;
  window.kwPakLocatie = kwPakLocatie;
  window.kwVoegBewijsToe = kwVoegBewijsToe;
  window.kwBekijkBewijs = kwBekijkBewijs;
  window.kwIndienen = kwIndienen;
  window.kwBeoordeel = kwBeoordeel;
  window.kwTerug = kwTerug;
})();
