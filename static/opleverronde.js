/**
 * Opleverronde met camera — het scherm.
 *
 * Wordt lazy geladen door opleverRondeLaad() in portaal.html en hangt in het
 * detailscherm van een oplevering. Gebruikt de globals die daar al staan:
 * api(), showToast(), escapeHtml().
 *
 * Drie dingen op één plek:
 *   1. De ronde starten en met de camera langs het werk lopen
 *   2. Wat de camera voorstelt bevestigen of weggooien
 *   3. Een restpunt herstellen met een foto als bewijs, en dat laten aftekenen
 *
 * De camera is een extra, geen dwang: de knop "Zelf een punt" staat er even
 * groot naast. Op een bouwplaats werkt lang niet alles, en een module die je
 * dwingt te filmen wordt niet gebruikt.
 */
(function () {
  'use strict';

  var CFG = null;            // /api/opleveringen/restpunt-klassen
  var opleveringId = null;
  var ronde = null;          // de lopende ronde
  var stream = null;         // camerastream, alleen tijdens een ronde
  var bezig = false;         // één frame tegelijk

  // ── Hulpjes ──────────────────────────────────────────────────────

  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(id) { return document.getElementById(id); }

  function melding(t, s) {
    if (typeof showToast === 'function') showToast(t, s || 'info');
  }

  function fout(e) {
    if (e && e.detail) return e.detail;
    if (e && e.message) return e.message;
    return 'Er ging iets mis';
  }

  var ERNST_KLEUR = {
    licht: 'var(--text-dim)',
    matig: 'var(--warning,#d97706)',
    zwaar: 'var(--danger,#dc2626)'
  };

  function badge(tekst, kleur) {
    return '<span style="display:inline-block;padding:2px 9px;border-radius:999px;' +
      'font-size:11px;font-weight:600;border:1px solid ' + kleur + ';color:' + kleur +
      ';white-space:nowrap;">' + esc(tekst) + '</span>';
  }

  function knopStijl(hoofd) {
    return 'padding:13px 16px;border-radius:10px;font-size:15px;font-weight:600;' +
      'cursor:pointer;font-family:inherit;border:' +
      (hoofd ? 'none;background:var(--accent,#0ea5e9);color:#fff;'
             : '1px solid var(--border);background:var(--card-bg);color:var(--text);');
  }

  // ── Ingang ───────────────────────────────────────────────────────

  function opleverRondeOpen(oplId) {
    opleveringId = oplId;
    var doel = el('opRondeBlok');
    if (!doel) return;
    doel.innerHTML = '<div class="loading-spinner">Laden...</div>';

    var stappen = [api('GET', '/api/opleveringen/' + oplId + '/rondes'),
                   api('GET', '/api/opleveringen/' + oplId + '/restpunten')];
    if (!CFG) stappen.push(api('GET', '/api/opleveringen/restpunt-klassen'));

    Promise.all(stappen).then(function (r) {
      if (r[2]) CFG = r[2];
      var rondes = r[0] || [];
      ronde = rondes.filter(function (x) { return x.status === 'bezig'; })[0] || null;
      teken(rondes, r[1]);
    }).catch(function (e) {
      doel.innerHTML = '<div style="color:var(--danger,#dc2626);font-size:13px;">' +
        esc(fout(e)) + '</div>';
    });
  }

  /** Alleen dit blok opnieuw tekenen. Voor frames en het afronden van een ronde. */
  function herlaad() {
    if (opleveringId) opleverRondeOpen(opleveringId);
  }

  /** Het hele detailscherm opnieuw laden.
   *
   * De restpuntenlijst zelf wordt door portaal.html getekend, niet hier. Zodra
   * er een punt bij komt of van status verandert, moet dat scherm dus mee --
   * anders staat een zojuist herstelde regel er nog als open bij. */
  function herlaadAlles() {
    if (typeof openOpleveringDetail === 'function' && opleveringId) {
      openOpleveringDetail(opleveringId);
    } else {
      herlaad();
    }
  }

  // ── Tekenen ──────────────────────────────────────────────────────

  function teken(rondes, lijst) {
    var t = (lijst && lijst.tellingen) || {};
    var voorstellen = (lijst && lijst.voorstellen) || [];

    var kop =
      '<div style="display:flex;justify-content:space-between;align-items:center;' +
      'gap:12px;flex-wrap:wrap;margin:22px 0 10px;">' +
        '<h3 style="margin:0;font-size:16px;">Opleverronde</h3>' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
          (ronde
            ? '<button style="' + knopStijl(false) + '" onclick="opRondeZelfPunt()">Zelf een punt</button>' +
              '<button style="' + knopStijl(true) + '" onclick="opRondeAfronden()">Ronde afronden</button>'
            : '<button style="' + knopStijl(true) + '" onclick="opRondeStart()">Ronde starten</button>') +
        '</div>' +
      '</div>';

    var telling =
      '<div style="display:flex;gap:20px;flex-wrap:wrap;font-size:13px;margin-bottom:12px;">' +
        kpi('Open', t.open || 0, (t.open ? 'var(--danger,#dc2626)' : null)) +
        kpi('Hersteld', t.hersteld || 0) +
        kpi('Afgetekend', t.geverifieerd || 0, 'var(--success,#16a34a)') +
        kpi('Te bevestigen', t.nog_te_bevestigen || 0,
            (t.nog_te_bevestigen ? 'var(--accent,#0ea5e9)' : null)) +
      '</div>';

    var rondeBlok = ronde ? cameraBlok() : historie(rondes);

    var voorstelBlok = voorstellen.length
      ? '<div style="margin-top:16px;padding:14px;border:1px solid var(--accent,#0ea5e9);' +
        'border-radius:10px;background:rgba(14,165,233,.05);">' +
        '<h4 style="margin:0 0 4px;font-size:14px;">De camera zag dit — klopt het?</h4>' +
        '<p style="margin:0 0 12px;font-size:12px;color:var(--text-dim);">' +
        'Niets hiervan staat in de restpuntenlijst tot je het bevestigt. ' +
        'Bijstellen mag; wat de camera meldde blijft bewaard.</p>' +
        voorstellen.map(voorstelRegel).join('') + '</div>'
      : '';

    el('opRondeBlok').innerHTML = kop + telling + rondeBlok + voorstelBlok;
  }

  function kpi(label, waarde, kleur) {
    return '<div><div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;' +
      'letter-spacing:.04em;">' + esc(label) + '</div><div style="font-size:19px;' +
      'font-weight:700;' + (kleur ? 'color:' + kleur + ';' : '') + '">' + waarde +
      '</div></div>';
  }

  function historie(rondes) {
    if (!rondes.length) {
      return '<p style="font-size:13px;color:var(--text-dim);margin:0;">' +
        'Start een ronde en loop het werk af. De camera kijkt mee en stelt ' +
        'restpunten voor; jij bepaalt wat er in de lijst komt.</p>';
    }
    return '<div style="font-size:13px;">' + rondes.map(function (r) {
      return '<div style="display:flex;justify-content:space-between;gap:10px;' +
        'padding:7px 0;border-bottom:1px solid var(--border);">' +
        '<span>Ronde ' + r.nummer + ' &middot; ' + esc(r.soort) +
        ' &middot; ' + esc(r.inspecteur_naam || '') + '</span>' +
        '<span style="color:var(--text-dim);">' + r.frames + ' beelden' +
        (r.frames_onbruikbaar ? ', ' + r.frames_onbruikbaar + ' onbruikbaar' : '') +
        '</span></div>';
    }).join('') + '</div>';
  }

  /** Het camerablok tijdens een lopende ronde. */
  function cameraBlok() {
    // Zonder privacy-bevestiging weigert de backend elk beeld. Een knop
    // tonen die gegarandeerd een foutmelding oplevert is erger dan geen knop:
    // dan lijkt het alsof er iets stuk is in plaats van dat het een keuze was.
    if (!ronde.privacy_bevestigd) {
      return '<div style="padding:14px;border:1px solid var(--border);border-radius:10px;">' +
        '<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">' +
          'Ronde ' + ronde.nummer + ' &middot; ' + esc(ronde.soort) + '</div>' +
        '<p style="margin:0;font-size:13px;">Beeldanalyse staat uit voor deze ronde: ' +
        'de privacycheck is niet bevestigd. Punten zelf vastleggen werkt gewoon. ' +
        'Wil je de camera toch gebruiken, rond deze ronde dan af en start een ' +
        'nieuwe.</p></div>';
    }

    var onbruikbaar = ronde.frames_onbruikbaar
      ? '<div style="margin-top:8px;font-size:12px;color:var(--warning,#d97706);">' +
        ronde.frames_onbruikbaar + ' van de ' + ronde.frames + ' beelden was niet te ' +
        'beoordelen (te donker, onscherp of te vol). Daar is dus niets bekeken — ' +
        'dat is iets anders dan dat er niets is.</div>'
      : '';

    var geenAi = (CFG && CFG.ai_beschikbaar === false)
      ? '<div style="margin-bottom:10px;padding:10px;border-radius:8px;font-size:12px;' +
        'border:1px solid var(--warning,#d97706);color:var(--warning,#d97706);">' +
        'Er staat geen AI-sleutel op deze omgeving, dus de camera analyseert niets. ' +
        'Zelf punten vastleggen werkt gewoon.</div>'
      : '';

    return '<div style="padding:14px;border:1px solid var(--border);border-radius:10px;">' +
      geenAi +
      '<div style="font-size:12px;color:var(--text-dim);margin-bottom:10px;">' +
        'Ronde ' + ronde.nummer + ' &middot; ' + esc(ronde.soort) +
        ' &middot; ' + ronde.frames + ' beelden bekeken</div>' +
      '<video id="opRondeVideo" playsinline muted autoplay ' +
        'style="width:100%;max-height:320px;background:#000;border-radius:8px;' +
        'display:block;object-fit:cover;"></video>' +
      '<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">' +
        '<button id="opRondeSchietBtn" style="' + knopStijl(true) + 'flex:1;min-width:150px;" ' +
          'onclick="opRondeSchiet()">Beeld beoordelen</button>' +
        '<button style="' + knopStijl(false) + '" onclick="opRondeCameraUit()">Camera uit</button>' +
      '</div>' +
      '<p style="margin:9px 0 0;font-size:12px;color:var(--text-dim);">' +
        'Je bepaalt zelf wanneer er een beeld wordt beoordeeld. Elk beeld is een ' +
        'losse analyse, dus schiet waar iets te zien is in plaats van continu.</p>' +
      onbruikbaar +
    '</div>';
  }

  function voorstelRegel(p) {
    var z = p.zekerheid === null || p.zekerheid === undefined
      ? '' : Math.round(p.zekerheid * 100) + '% zeker';
    var twijfel = p.moet_nagekeken
      ? ' ' + badge('goed nakijken', 'var(--warning,#d97706)') : '';
    return '<div style="padding:11px 0;border-bottom:1px solid var(--border);">' +
      '<div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">' +
        '<div style="flex:1;min-width:200px;">' +
          '<strong style="font-size:14px;">' + esc(p.omschrijving) + '</strong>' + twijfel +
          '<div style="font-size:12px;color:var(--text-dim);margin-top:2px;">' +
            esc(p.restpunt_klasse_naam || '') +
            (p.plek ? ' &middot; ' + esc(p.plek) : '') +
            (z ? ' &middot; ' + z : '') +
          '</div>' +
        '</div>' +
        '<div style="display:flex;gap:6px;align-items:flex-start;">' +
          badge(p.ernst || 'licht', ERNST_KLEUR[p.ernst] || 'var(--text-dim)') +
        '</div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;margin-top:9px;flex-wrap:wrap;">' +
        '<button style="' + knopStijl(true) + 'padding:8px 14px;font-size:13px;" ' +
          'onclick="opRondeBevestig(\'' + p.id + '\')">In de lijst</button>' +
        '<button style="' + knopStijl(false) + 'padding:8px 14px;font-size:13px;" ' +
          'onclick="opRondeBijstellen(\'' + p.id + '\')">Bijstellen</button>' +
        (p.heeft_foto
          ? '<button style="' + knopStijl(false) + 'padding:8px 14px;font-size:13px;" ' +
            'onclick="opRondeFotos(\'' + p.id + '\')">Bekijk beeld</button>'
          : '') +
        '<button style="' + knopStijl(false) + 'padding:8px 14px;font-size:13px;' +
          'color:var(--danger,#dc2626);" onclick="opRondeVerwerp(\'' + p.id + '\')">Weg</button>' +
      '</div></div>';
  }

  // ── De ronde ─────────────────────────────────────────────────────

  function opRondeStart() {
    // De privacy-poort. Bewust een expliciete vraag en geen vinkje dat je
    // wegklikt: het beeld gaat naar een verwerker buiten de EU.
    var akkoord = confirm(
      'Een opleverronde loopt over straat en langs woningen.\n\n' +
      'Bevestig dat je zorgt dat er geen herkenbare personen of kentekens in ' +
      'beeld komen. Zonder die bevestiging wordt er geen beeld geanalyseerd.\n\n' +
      'Akkoord?');

    api('POST', '/api/opleveringen/' + opleveringId + '/rondes',
        { privacy_bevestigd: akkoord })
      .then(function (r) {
        ronde = r;
        melding('Ronde ' + r.nummer + ' gestart', 'success');
        herlaad();
        if (akkoord) setTimeout(cameraAan, 400);
      })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  function cameraAan() {
    var video = el('opRondeVideo');
    if (!video || stream) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      melding('Camera niet beschikbaar op dit apparaat', 'error');
      return;
    }
    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' },
               width: { ideal: 1600 }, height: { ideal: 1200 } },
      audio: false
    }).then(function (s) {
      stream = s;
      video.srcObject = s;
      video.play().catch(function () {});
    }).catch(function () {
      melding('Camera niet vrijgegeven — je kunt wel zelf punten vastleggen', 'error');
    });
  }

  function opRondeCameraUit() {
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    var v = el('opRondeVideo');
    if (v) v.srcObject = null;
  }

  function opRondeSchiet() {
    if (!ronde) return;
    if (bezig) return;                     // één frame tegelijk; elk beeld kost
    var video = el('opRondeVideo');
    if (!video || !video.videoWidth) {
      cameraAan();
      melding('Camera start nog op — probeer het zo opnieuw', 'info');
      return;
    }

    var canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    var beeld = canvas.toDataURL('image/jpeg', 0.75);

    bezig = true;
    var knop = el('opRondeSchietBtn');
    if (knop) { knop.disabled = true; knop.textContent = 'Bezig met kijken...'; }

    var payload = { image_data_url: beeld };
    var stuur = function () {
      api('POST', '/api/opleveringen/rondes/' + ronde.id + '/frame', payload)
        .then(function (r) {
          if (!r.bruikbaar) {
            melding(r.reden_onbruikbaar || 'Beeld niet te beoordelen', 'error');
          } else if (!r.gevonden.length) {
            melding('Niets gevonden op dit beeld', 'success');
          } else {
            melding(r.gevonden.length + ' punt(en) voorgesteld — kijk ze na', 'info');
          }
          herlaad();
          setTimeout(cameraAan, 300);
        })
        .catch(function (e) { melding(fout(e), 'error'); })
        .finally(function () {
          bezig = false;
          var k = el('opRondeSchietBtn');
          if (k) { k.disabled = false; k.textContent = 'Beeld beoordelen'; }
        });
    };

    // Plek meesturen als het mag; lukt het niet, dan gaat het beeld gewoon mee.
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(function (pos) {
        payload.lat = pos.coords.latitude;
        payload.lng = pos.coords.longitude;
        stuur();
      }, stuur, { timeout: 4000, maximumAge: 30000 });
    } else {
      stuur();
    }
  }

  function opRondeAfronden() {
    if (!ronde) return;
    api('POST', '/api/opleveringen/rondes/' + ronde.id + '/afronden', {})
      .then(function () {
        opRondeCameraUit();
        ronde = null;
        melding('Ronde afgerond', 'success');
        herlaad();
      })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  // ── Voorstellen ──────────────────────────────────────────────────

  function opRondeBevestig(id) {
    api('PATCH', '/api/opleveringen/punten/' + id + '/bevestigen',
        { besluit: 'bevestigen' })
      .then(function () { melding('In de lijst gezet', 'success'); herlaadAlles(); })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  function opRondeBijstellen(id) {
    var omschrijving = prompt('Omschrijving van het restpunt:');
    if (omschrijving === null) return;
    if (!omschrijving.trim()) { melding('Geef een omschrijving', 'error'); return; }
    var ernst = prompt('Ernst — licht, matig of zwaar:', 'matig');
    if (ernst === null) return;
    ernst = (ernst || '').trim().toLowerCase();
    if (['licht', 'matig', 'zwaar'].indexOf(ernst) === -1) {
      melding('Ernst moet licht, matig of zwaar zijn', 'error');
      return;
    }
    api('PATCH', '/api/opleveringen/punten/' + id + '/bevestigen',
        { besluit: 'bevestigen', omschrijving: omschrijving.trim(), ernst: ernst })
      .then(function () { melding('Bijgesteld en in de lijst gezet', 'success'); herlaadAlles(); })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  function opRondeVerwerp(id) {
    if (!confirm('Dit voorstel weggooien?')) return;
    api('PATCH', '/api/opleveringen/punten/' + id + '/bevestigen',
        { besluit: 'verwerpen' })
      .then(function () { melding('Weggegooid', 'success'); herlaad(); })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  // ── Zelf een punt ────────────────────────────────────────────────

  function opRondeZelfPunt() {
    if (!ronde) return;
    var omschrijving = prompt('Wat is er niet in orde?');
    if (omschrijving === null) return;
    if (!omschrijving.trim()) { melding('Geef een omschrijving', 'error'); return; }
    var plek = prompt('Waar? (bijvoorbeeld: trottoir voor nummer 12)') || '';
    var ernst = (prompt('Ernst — licht, matig of zwaar:', 'matig') || 'matig')
      .trim().toLowerCase();
    if (['licht', 'matig', 'zwaar'].indexOf(ernst) === -1) ernst = 'matig';

    var payload = { omschrijving: omschrijving.trim(), plek: plek.trim() || null,
                    ernst: ernst };
    var stuur = function () {
      api('POST', '/api/opleveringen/rondes/' + ronde.id + '/punt', payload)
        .then(function () { melding('Punt vastgelegd', 'success'); herlaadAlles(); })
        .catch(function (e) { melding(fout(e), 'error'); });
    };
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(function (pos) {
        payload.lat = pos.coords.latitude;
        payload.lng = pos.coords.longitude;
        stuur();
      }, stuur, { timeout: 4000, maximumAge: 30000 });
    } else { stuur(); }
  }

  // ── Herstel met bewijs ───────────────────────────────────────────

  function opRondeHerstel(id) {
    // De foto is verplicht: een restpunt afvinken zonder beeld is precies
    // het afvinken waar deze module vanaf wil.
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/*';
    inp.capture = 'environment';
    inp.onchange = function () {
      var bestand = inp.files && inp.files[0];
      if (!bestand) return;
      if (bestand.size > 8 * 1024 * 1024) {
        melding('Foto is te groot (max 8 MB)', 'error');
        return;
      }
      var lezer = new FileReader();
      lezer.onload = function () {
        var toelichting = prompt('Wat is er gedaan? (mag leeg)') || '';
        api('POST', '/api/opleveringen/punten/' + id + '/herstel',
            { photo_url_after: lezer.result, toelichting: toelichting.trim() || null })
          .then(function () {
            melding('Herstel gemeld — nog aftekenen', 'success');
            herlaadAlles();
          })
          .catch(function (e) { melding(fout(e), 'error'); });
      };
      lezer.readAsDataURL(bestand);
    };
    inp.click();
  }

  function opRondeAftekenen(id, besluit) {
    var reden = null;
    if (besluit === 'afwijzen') {
      reden = prompt('Waarom afgewezen? De aannemer moet weten wat er alsnog moet gebeuren.');
      if (reden === null) return;
      if (!reden.trim()) { melding('Geef een reden op', 'error'); return; }
    }
    api('POST', '/api/opleveringen/punten/' + id + '/verifieren',
        { besluit: besluit, reden: reden })
      .then(function () {
        melding(besluit === 'akkoord' ? 'Afgetekend' : 'Afgewezen', 'success');
        herlaadAlles();
      })
      .catch(function (e) { melding(fout(e), 'error'); });
  }

  function opRondeFotos(id) {
    api('GET', '/api/opleveringen/punten/' + id + '/fotos').then(function (f) {
      var w = window.open('', '_blank');
      if (!w) { melding('Sta pop-ups toe om de foto\'s te zien', 'error'); return; }
      var img = function (src, titel) {
        return src ? '<figure style="margin:0 0 18px;"><figcaption style="font:600 13px ' +
          'system-ui;margin-bottom:6px;">' + titel + '</figcaption>' +
          '<img src="' + src + '" style="max-width:100%;border-radius:8px;"></figure>' : '';
      };
      w.document.write('<title>Restpunt</title><body style="margin:20px;background:#f6f7f8;">' +
        img(f.photo_url, 'Wat de camera zag') +
        img(f.photo_url_after, 'Na herstel') + '</body>');
      w.document.close();
    }).catch(function (e) { melding(fout(e), 'error'); });
  }

  // ── Naar buiten ──────────────────────────────────────────────────

  window.opleverRondeOpen = opleverRondeOpen;
  window.opRondeStart = opRondeStart;
  window.opRondeSchiet = opRondeSchiet;
  window.opRondeCameraUit = opRondeCameraUit;
  window.opRondeAfronden = opRondeAfronden;
  window.opRondeBevestig = opRondeBevestig;
  window.opRondeBijstellen = opRondeBijstellen;
  window.opRondeVerwerp = opRondeVerwerp;
  window.opRondeZelfPunt = opRondeZelfPunt;
  window.opRondeHerstel = opRondeHerstel;
  window.opRondeAftekenen = opRondeAftekenen;
  window.opRondeFotos = opRondeFotos;
})();
