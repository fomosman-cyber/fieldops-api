/**
 * Bedrijven — de bedrijfsprofielen van het platform.
 *
 * Alleen zichtbaar voor de platform-eigenaar. Hier richt je een klantomgeving
 * in zonder als die klant in te loggen: naam, plan, aantal gebruikers, logo,
 * huisstijlkleur en welke onderdelen ze in hun portaal zien.
 *
 * Dat laatste kon al, maar zat verstopt achter een tandwieltje in een tabelcel
 * met "9 / 10" erin. Dat vindt niemand. Hier staat per bedrijf gewoon zichtbaar
 * wat er aan en uit staat.
 *
 * Wordt lazy geladen door bedrijvenLaad() in portaal.html en gebruikt de
 * globals die daar al staan: api(), showToast(), MODULE_LABELS.
 */
(function () {
  'use strict';

  var bedrijven = [];
  var huidig = null;          // volledig profiel van het geopende bedrijf
  var zoekTimer = null;

  // ── Hulpjes ──────────────────────────────────────────────────────

  function esc(v) {
    if (v === null || v === undefined) return '';
    return String(v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(id) { return document.getElementById(id); }

  function melding(tekst, soort) {
    if (typeof showToast === 'function') showToast(tekst, soort || 'info');
  }

  function foutTekst(e) {
    if (e && e.detail) return e.detail;
    if (e && e.message) return e.message;
    return 'Er ging iets mis';
  }

  function datumNL(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('nl-NL',
      { day: '2-digit', month: '2-digit', year: 'numeric' });
  }

  function toon(welk) {
    ['bedrijvenLijst', 'bedrijfProfiel'].forEach(function (id) {
      var n = el(id);
      if (n) n.style.display = (id === welk) ? '' : 'none';
    });
    var f = el('bedrijvenFilters');
    if (f) f.style.display = (welk === 'bedrijvenLijst') ? 'flex' : 'none';
    var t = el('bedrijvenTerugBtn');
    if (t) t.style.display = (welk === 'bedrijvenLijst') ? 'none' : '';
    var n = el('bedrijvenNieuwBtn');
    if (n) n.style.display = (welk === 'bedrijvenLijst') ? '' : 'none';
  }

  function invoerStijl() {
    return 'width:100%;padding:9px 10px;border:1px solid var(--border);border-radius:8px;' +
      'background:var(--card-bg);color:var(--text);font-family:inherit;font-size:14px;';
  }

  function veldBlok(label, inhoud, hulp) {
    return '<div style="margin-bottom:14px;">' +
      '<label style="display:block;font-size:12px;color:var(--text-dim);margin-bottom:4px;' +
      'font-weight:600;">' + esc(label) + '</label>' + inhoud +
      (hulp ? '<p style="margin:5px 0 0;font-size:12px;color:var(--text-dim);">' +
        esc(hulp) + '</p>' : '') + '</div>';
  }

  var STATUS_KLEUR = {
    active: 'var(--success,#16a34a)', trial: 'var(--accent,#0ea5e9)',
    expired: 'var(--warning,#d97706)', suspended: 'var(--danger,#dc2626)'
  };
  var STATUS_LABEL = {
    active: 'Actief', trial: 'Proef', expired: 'Verlopen', suspended: 'Geblokkeerd'
  };

  function badge(tekst, kleur) {
    return '<span style="display:inline-block;padding:2px 9px;border-radius:999px;' +
      'font-size:11px;font-weight:600;border:1px solid ' + kleur + ';color:' + kleur +
      ';white-space:nowrap;">' + esc(tekst) + '</span>';
  }

  /** Het merkje van een bedrijf: het logo als dat er is, anders de initialen
   *  in de huisstijlkleur. Nooit een leeg vlak — dan lijkt de rij stuk. */
  function merkje(b, formaat) {
    var maat = formaat || 40;
    var kleur = b.brand_color || 'var(--accent,#0ea5e9)';
    if (b.logo_data_url) {
      return '<img src="' + esc(b.logo_data_url) + '" alt="" style="width:' + maat +
        'px;height:' + maat + 'px;border-radius:9px;object-fit:contain;background:#fff;' +
        'border:1px solid var(--border);flex:none;">';
    }
    var letters = (b.name || '?').split(/\s+/).slice(0, 2)
      .map(function (w) { return w.charAt(0); }).join('').toUpperCase();
    return '<div style="width:' + maat + 'px;height:' + maat + 'px;border-radius:9px;' +
      'background:' + kleur + ';color:#fff;display:flex;align-items:center;' +
      'justify-content:center;font-weight:700;font-size:' + Math.round(maat / 2.6) +
      'px;flex:none;">' + esc(letters) + '</div>';
  }

  // ── Scherm 1: de lijst ───────────────────────────────────────────

  function bedrijvenOpen() {
    toon('bedrijvenLijst');
    bedrijvenLaadLijst();
  }

  function bedrijvenZoekDebounce() {
    clearTimeout(zoekTimer);
    zoekTimer = setTimeout(tekenLijst, 200);
  }

  function bedrijvenLaadLijst() {
    var doel = el('bedrijvenLijst');
    if (!doel) return;
    doel.innerHTML = '<div class="loading-spinner">Bedrijven laden...</div>';
    api('GET', '/api/admin/overview').then(function (body) {
      bedrijven = (body && body.organizations) || [];
      tekenLijst();
    }).catch(function (e) {
      doel.innerHTML = '<div class="empty-state"><h3>Niet geladen</h3><p>' +
        esc(foutTekst(e)) + '</p></div>';
    });
  }

  function tekenLijst() {
    var doel = el('bedrijvenLijst');
    if (!doel) return;
    var z = el('bedrijvenZoek');
    var naald = z && z.value ? z.value.trim().toLowerCase() : '';
    var s = el('bedrijvenStatus');
    var status = s ? s.value : '';

    var rijen = bedrijven.filter(function (b) {
      if (status && b.status !== status) return false;
      if (naald && (b.name || '').toLowerCase().indexOf(naald) === -1) return false;
      return true;
    });

    if (!rijen.length) {
      doel.innerHTML = '<div class="empty-state"><h3>Geen bedrijven</h3>' +
        '<p>Maak er een aan om een klantomgeving in te richten.</p>' +
        '<button class="btn-primary" onclick="bedrijfNieuw()">+ Nieuw bedrijf</button></div>';
      return;
    }

    var totaalModules = (typeof MODULE_PAGES !== 'undefined') ? MODULE_PAGES.length : 10;
    doel.innerHTML = rijen.map(function (b) {
      var aantal = Array.isArray(b.enabled_modules)
        ? b.enabled_modules.length : totaalModules;
      var alles = !Array.isArray(b.enabled_modules);
      return '<div class="card" style="margin-bottom:10px;cursor:pointer;" ' +
        'onclick="bedrijfOpen(\'' + b.id + '\')">' +
        '<div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap;">' +
          merkje(b, 44) +
          '<div style="flex:1;min-width:190px;">' +
            '<h3 style="margin:0 0 3px;font-size:16px;">' + esc(b.name) + '</h3>' +
            '<div style="font-size:12px;color:var(--text-dim);">' +
              esc(b.plan === 'professional' ? 'Professional' : 'Starter') +
              ' &middot; ' + (b.user_count || 0) + ' van ' + (b.max_users || 0) + ' gebruikers' +
              ' &middot; sinds ' + esc(datumNL(b.created_at)) +
            '</div>' +
          '</div>' +
          '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;">' +
            badge(alles ? 'alle onderdelen' : aantal + ' van ' + totaalModules + ' onderdelen',
                  alles ? 'var(--text-dim)' : 'var(--accent,#0ea5e9)') +
            badge(STATUS_LABEL[b.status] || b.status,
                  STATUS_KLEUR[b.status] || 'var(--text-dim)') +
          '</div>' +
        '</div></div>';
    }).join('');
  }

  // ── Scherm 2: het profiel ────────────────────────────────────────

  function bedrijfOpen(orgId) {
    var doel = el('bedrijfProfiel');
    doel.innerHTML = '<div class="loading-spinner">Profiel laden...</div>';
    toon('bedrijfProfiel');
    api('GET', '/api/admin/organizations/' + orgId).then(function (b) {
      huidig = b;
      tekenProfiel(b);
    }).catch(function (e) {
      doel.innerHTML = '<div class="empty-state"><h3>Niet gevonden</h3><p>' +
        esc(foutTekst(e)) + '</p></div>';
    });
  }

  function tekenProfiel(b) {
    var alles = !Array.isArray(b.enabled_modules);
    var aan = alles ? (b.alle_modules || []).map(function (m) { return m.key; })
                    : b.enabled_modules;

    // Alle onderdelen zichtbaar op een rij, met een schakelaar per stuk.
    // Niet verstopt achter een tandwieltje: hier is in één blik te zien wat
    // deze klant wel en niet in zijn portaal heeft staan.
    var modules = (b.alle_modules || []).map(function (m) {
      var actief = aan.indexOf(m.key) !== -1;
      return '<label style="display:flex;align-items:center;gap:11px;padding:11px 12px;' +
        'border:1px solid ' + (actief ? 'var(--success,#16a34a)' : 'var(--border)') + ';' +
        'border-radius:9px;cursor:pointer;font-size:14px;' +
        'background:' + (actief ? 'rgba(22,163,74,.07)' : 'transparent') + ';">' +
        '<input type="checkbox" class="bedrijfModule" value="' + esc(m.key) + '"' +
        (actief ? ' checked' : '') + ' onchange="bedrijfModulesOpslaan()" ' +
        'style="width:18px;height:18px;accent-color:var(--success,#16a34a);cursor:pointer;">' +
        '<span>' + esc(m.label) + '</span></label>';
    }).join('');

    var gebruikers = (b.gebruikers || []).map(function (u) {
      return '<div style="display:flex;justify-content:space-between;gap:10px;padding:8px 0;' +
        'border-bottom:1px solid var(--border);font-size:13px;align-items:center;">' +
        '<div><strong>' + esc(u.naam) + '</strong>' +
          (u.is_org_admin ? ' ' + badge('beheerder', 'var(--accent,#0ea5e9)') : '') +
          '<div style="color:var(--text-dim);font-size:12px;">' + esc(u.email) + '</div></div>' +
        '<span style="color:var(--text-dim);font-size:12px;">' + esc(u.rol || '') + '</span>' +
        '</div>';
    }).join('') || '<p style="color:var(--text-dim);font-size:13px;">Nog geen gebruikers.</p>';

    el('bedrijfProfiel').innerHTML =
      // Kop met merkje
      '<div class="card" style="margin-bottom:14px;">' +
        '<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;">' +
          merkje(b, 60) +
          '<div style="flex:1;min-width:200px;">' +
            '<h2 style="margin:0 0 4px;font-size:21px;">' + esc(b.name) + '</h2>' +
            '<div style="font-size:13px;color:var(--text-dim);">' +
              (b.contact_email ? esc(b.contact_email) + ' &middot; ' : '') +
              'klant sinds ' + esc(datumNL(b.created_at)) +
            '</div>' +
          '</div>' +
          '<div style="display:flex;gap:7px;">' +
            badge(b.plan === 'professional' ? 'Professional' : 'Starter', 'var(--text-dim)') +
            badge(STATUS_LABEL[b.status] || b.status,
                  STATUS_KLEUR[b.status] || 'var(--text-dim)') +
          '</div>' +
        '</div>' +
        '<div style="display:flex;gap:24px;margin-top:16px;flex-wrap:wrap;font-size:13px;">' +
          kpi('Gebruikers', (b.user_count || 0) + ' / ' + (b.max_users || 0)) +
          kpi('Onderdelen aan', aan.length + ' van ' + (b.alle_modules || []).length) +
          kpi('Meldpunt', b.public_meld_enabled ? 'aan' : 'uit') +
        '</div>' +
      '</div>' +

      // Wat ze zien
      '<div class="card" style="margin-bottom:14px;">' +
        '<div class="card-header"><h3 style="margin:0;">Wat dit bedrijf ziet</h3></div>' +
        '<p style="margin:0 0 14px;font-size:13px;color:var(--text-dim);">' +
          'Vink aan welke onderdelen in hun portaal staan. Dashboard, projecten, ' +
          'assets, meldingen en kaart staan altijd aan en horen er niet bij. ' +
          'Wijzigingen gaan meteen in.</p>' +
        '<div style="display:grid;gap:9px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));">' +
          modules +
        '</div>' +
      '</div>' +

      // Huisstijl
      '<div class="card" style="margin-bottom:14px;">' +
        '<div class="card-header"><h3 style="margin:0;">Huisstijl van het portaal</h3></div>' +
        '<p style="margin:0 0 14px;font-size:13px;color:var(--text-dim);">' +
          'Het logo en de kleur die deze klant in zijn eigen portaal en op zijn ' +
          'rapporten ziet. Je richt dat hier in zonder als die klant in te loggen.</p>' +
        '<div style="display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));">' +
          '<div>' +
            veldBlok('Logo',
              '<input id="bedrijfLogo" type="file" accept="image/png,image/jpeg,image/svg+xml" ' +
              'onchange="bedrijfLogoGekozen(this)" style="' + invoerStijl() + '">',
              'PNG, JPG of SVG. Wordt getoond in de balk en op de PDF.') +
            (b.logo_data_url
              ? '<button class="btn-secondary" style="color:var(--danger,#dc2626);' +
                'font-size:12px;padding:5px 11px;" onclick="bedrijfLogoWissen()">Logo wissen</button>'
              : '') +
          '</div>' +
          '<div>' +
            veldBlok('Huisstijlkleur',
              '<div style="display:flex;gap:9px;align-items:center;">' +
              '<input id="bedrijfKleur" type="color" value="' +
              esc(b.brand_color || '#0284c7') + '" ' +
              'style="width:52px;height:40px;padding:2px;border:1px solid var(--border);' +
              'border-radius:8px;background:var(--card-bg);cursor:pointer;">' +
              '<input id="bedrijfKleurHex" type="text" value="' +
              esc(b.brand_color || '#0284c7') + '" style="' + invoerStijl() + '">' +
              '</div>', 'Hex-code, bijvoorbeeld #0284c7.') +
          '</div>' +
        '</div>' +
        '<div style="display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));">' +
          veldBlok('Contact-e-mail', '<input id="bedrijfEmail" type="email" value="' +
            esc(b.contact_email || '') + '" style="' + invoerStijl() + '">') +
          veldBlok('Telefoon', '<input id="bedrijfTelefoon" type="text" value="' +
            esc(b.contact_phone || '') + '" style="' + invoerStijl() + '">') +
          veldBlok('KvK', '<input id="bedrijfKvk" type="text" value="' +
            esc(b.kvk_number || '') + '" style="' + invoerStijl() + '">') +
          veldBlok('BTW-nummer', '<input id="bedrijfBtw" type="text" value="' +
            esc(b.btw_number || '') + '" style="' + invoerStijl() + '">') +
        '</div>' +
        '<div style="display:flex;justify-content:flex-end;">' +
          '<button class="btn-primary" onclick="bedrijfHuisstijlOpslaan()">Huisstijl opslaan</button>' +
        '</div>' +
      '</div>' +

      // Abonnement
      '<div class="card" style="margin-bottom:14px;">' +
        '<div class="card-header"><h3 style="margin:0;">Abonnement</h3></div>' +
        '<div style="display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">' +
          veldBlok('Naam', '<input id="bedrijfNaam" type="text" value="' +
            esc(b.name) + '" style="' + invoerStijl() + '">') +
          veldBlok('Plan',
            '<select id="bedrijfPlan" style="' + invoerStijl() + '">' +
            '<option value="starter"' + (b.plan !== 'professional' ? ' selected' : '') +
            '>Starter</option>' +
            '<option value="professional"' + (b.plan === 'professional' ? ' selected' : '') +
            '>Professional</option></select>') +
          veldBlok('Status',
            '<select id="bedrijfStatus" style="' + invoerStijl() + '">' +
            ['active', 'trial', 'expired', 'suspended'].map(function (st) {
              return '<option value="' + st + '"' + (b.status === st ? ' selected' : '') +
                '>' + STATUS_LABEL[st] + '</option>';
            }).join('') + '</select>') +
          veldBlok('Max gebruikers', '<input id="bedrijfMax" type="number" min="1" value="' +
            (b.max_users || 10) + '" style="' + invoerStijl() + '">') +
        '</div>' +
        '<div style="display:flex;justify-content:flex-end;">' +
          '<button class="btn-primary" onclick="bedrijfAbonnementOpslaan()">Opslaan</button>' +
        '</div>' +
      '</div>' +

      // Gebruikers
      '<div class="card">' +
        '<div class="card-header"><h3 style="margin:0;">Gebruikers (' +
          (b.gebruikers || []).length + ')</h3></div>' + gebruikers +
      '</div>';

    // Kleurkiezer en hex-veld gelijk houden.
    var kiezer = el('bedrijfKleur');
    var hex = el('bedrijfKleurHex');
    if (kiezer && hex) {
      kiezer.addEventListener('input', function () { hex.value = kiezer.value; });
      hex.addEventListener('input', function () {
        if (/^#[0-9a-fA-F]{6}$/.test(hex.value.trim())) kiezer.value = hex.value.trim();
      });
    }
  }

  function kpi(label, waarde) {
    return '<div><div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;' +
      'letter-spacing:.04em;">' + esc(label) + '</div>' +
      '<div style="font-size:19px;font-weight:700;">' + esc(waarde) + '</div></div>';
  }

  // ── Opslaan ──────────────────────────────────────────────────────

  function bedrijfModulesOpslaan() {
    if (!huidig) return;
    var keys = Array.prototype.slice.call(
      document.querySelectorAll('#bedrijfProfiel .bedrijfModule:checked')
    ).map(function (c) { return c.value; });

    api('PUT', '/api/admin/organizations/' + huidig.id +
        '?enabled_modules=' + encodeURIComponent(keys.join(',')))
      .then(function () {
        huidig.enabled_modules = keys;
        melding('Bijgewerkt — ' + keys.length + ' onderdelen aan', 'success');
        tekenProfiel(huidig);
      })
      .catch(function (e) {
        melding(foutTekst(e), 'error');
        bedrijfOpen(huidig.id);      // terug naar de echte stand
      });
  }

  function bedrijfLogoGekozen(input) {
    var bestand = input.files && input.files[0];
    if (!bestand) return;
    if (bestand.size > 2 * 1024 * 1024) {
      melding('Logo is te groot (max 2 MB)', 'error');
      input.value = '';
      return;
    }
    var lezer = new FileReader();
    lezer.onload = function () {
      bewaarHuisstijl({ logo_data_url: lezer.result });
    };
    lezer.readAsDataURL(bestand);
  }

  function bedrijfLogoWissen() {
    bewaarHuisstijl({ logo_data_url: null });
  }

  function bedrijfHuisstijlOpslaan() {
    var hex = (el('bedrijfKleurHex').value || '').trim();
    if (hex && !/^#[0-9a-fA-F]{6}$/.test(hex)) {
      melding('Huisstijlkleur moet een hex-code zijn, zoals #0284c7', 'error');
      return;
    }
    bewaarHuisstijl({
      brand_color: hex || null,
      contact_email: el('bedrijfEmail').value.trim(),
      contact_phone: el('bedrijfTelefoon').value.trim(),
      kvk_number: el('bedrijfKvk').value.trim(),
      btw_number: el('bedrijfBtw').value.trim()
    });
  }

  function bewaarHuisstijl(payload) {
    if (!huidig) return;
    api('PUT', '/api/admin/organizations/' + huidig.id + '/branding', payload)
      .then(function () {
        melding('Huisstijl opgeslagen', 'success');
        bedrijfOpen(huidig.id);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function bedrijfAbonnementOpslaan() {
    if (!huidig) return;
    var naam = el('bedrijfNaam').value.trim();
    if (!naam) { melding('Geef het bedrijf een naam', 'error'); return; }
    var qs = 'name=' + encodeURIComponent(naam) +
      '&plan=' + encodeURIComponent(el('bedrijfPlan').value) +
      '&status=' + encodeURIComponent(el('bedrijfStatus').value) +
      '&max_users=' + encodeURIComponent(el('bedrijfMax').value || 10);
    api('PUT', '/api/admin/organizations/' + huidig.id + '?' + qs)
      .then(function () {
        melding('Bijgewerkt', 'success');
        bedrijfOpen(huidig.id);
      })
      .catch(function (e) { melding(foutTekst(e), 'error'); });
  }

  function bedrijfNieuw() {
    if (typeof openCreateOrgModal === 'function') {
      openCreateOrgModal();
      return;
    }
    melding('Aanmaken kan via Admin Overzicht', 'info');
  }

  function bedrijvenTerug() {
    huidig = null;
    toon('bedrijvenLijst');
    bedrijvenLaadLijst();
  }

  // ── Naar buiten ──────────────────────────────────────────────────

  window.bedrijvenOpen = bedrijvenOpen;
  window.bedrijvenLaadLijst = bedrijvenLaadLijst;
  window.bedrijvenZoekDebounce = bedrijvenZoekDebounce;
  window.bedrijvenTerug = bedrijvenTerug;
  window.bedrijfOpen = bedrijfOpen;
  window.bedrijfNieuw = bedrijfNieuw;
  window.bedrijfModulesOpslaan = bedrijfModulesOpslaan;
  window.bedrijfLogoGekozen = bedrijfLogoGekozen;
  window.bedrijfLogoWissen = bedrijfLogoWissen;
  window.bedrijfHuisstijlOpslaan = bedrijfHuisstijlOpslaan;
  window.bedrijfAbonnementOpslaan = bedrijfAbonnementOpslaan;
  window.tekenBedrijvenLijst = tekenLijst;
})();
