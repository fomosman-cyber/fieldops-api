# Email-naar-melding setup-handleiding

Per organisatie kun je een email-adres koppelen waar inkomende emails automatisch worden omgezet naar meldingen in FieldOps.

**Use-case**: een gemeente publiceert `meldingen@gemeente.nl`. Burgers sturen een email met foto. FieldOps maakt automatisch een melding aan in het juiste project, met de foto als bewijsmateriaal.

## Hoe het werkt

```
Burger stuurt email naar           Mailgun/Postmark             POST naar              Melding in
meldingen@klant.nl          ─────► inbound-parser       ──────► /api/incoming/  ─────► FieldOps
                                                                email/{token}
```

1. **DNS/MX**: je richt een subdomein (bv. `inbound.fieldopsapp.nl`) of MX-record op naar Mailgun/Postmark
2. **Provider routes** alle mail naar `meldingen-*@inbound.fieldopsapp.nl` door naar onze webhook
3. **FieldOps** valideert de token, parsed de email, maakt melding aan

## Stap 1 — Email-route aanmaken in FieldOps

In de webportaal (na deploy van deze feature):
- Ga naar **Instellingen → Email-routes** (org-admin only, komt in volgende UI-PR)

Tot dan via API:

```bash
curl -X POST https://portaal.fieldopsapp.nl/api/email-inbox/ \
  -H "Authorization: Bearer $YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "label": "Burgermeldingen Gemeente X",
    "address_prefix": "meldingen-gemeentex",
    "default_project_id": "uuid-of-project",
    "default_category": "Bestrating",
    "default_priority": "normaal",
    "allowed_senders": ["@gemeentex.nl", "specifieke@partner.nl"]
  }'
```

Response bevat eenmalig de **token** + `webhook_url`:

```json
{
  "id": "abc-123",
  "token": "G7kf8j2hZpQv...",
  "webhook_url": "/api/incoming/email/G7kf8j2hZpQv..."
}
```

Bewaar de token — die zie je later alleen als prefix.

## Stap 2 — Provider koppelen

### Optie A — Mailgun (aanbevolen)

1. Inloggen op [mailgun.com](https://mailgun.com), kies je domein (bv. `inbound.fieldopsapp.nl`)
2. **Receiving → Routes → Create Route**:
   - Expression: `match_recipient("meldingen-gemeentex@inbound.fieldopsapp.nl")`
   - Action: `forward("https://portaal.fieldopsapp.nl/api/incoming/email/G7kf8j2hZpQv...")`
   - Priority: `1`
3. Test door een email te sturen naar `meldingen-gemeentex@inbound.fieldopsapp.nl`

**Mailgun stuurt** subject, sender, body-plain, body-html in een form-urlencoded POST. Onze parser herkent dat automatisch.

### Optie B — Postmark

1. Inloggen op [postmarkapp.com](https://postmarkapp.com)
2. **Servers → Inbound Stream → Settings**:
   - Webhook URL: `https://portaal.fieldopsapp.nl/api/incoming/email/G7kf8j2hZpQv...`
   - Content type: `application/json`
3. Inbound email-adres genereert Postmark zelf (`<hash>@inbound.postmarkapp.com`)
4. **Aliasing** vanaf eigen domein: in Postmark **Inbound Domains** → `meldingen-gemeentex@inbound.fieldopsapp.nl` → forward naar Postmark-hash

**Postmark stuurt** JSON met `From`, `Subject`, `TextBody`, `Attachments[]` (base64). Onze parser herkent en extract de eerste image-attachment als foto.

### Optie C — SendGrid Inbound Parse

1. Inloggen op [sendgrid.com](https://sendgrid.com)
2. **Settings → Inbound Parse → Add Host & URL**:
   - Hostname: `inbound.fieldopsapp.nl`
   - Webhook URL: `https://portaal.fieldopsapp.nl/api/incoming/email/G7kf8j2hZpQv...`
   - Check both **POST the raw, full MIME message** + **Spam check**
3. MX-record toevoegen aan DNS: `mx 10 mx.sendgrid.net`

SendGrid stuurt multipart/form-data met `from`, `subject`, `text`, `attachment-info`.

## Stap 3 — Test de koppeling

Stuur een test-email vanaf jouw eigen account:

```
Naar: meldingen-gemeentex@inbound.fieldopsapp.nl
Onderwerp: Test - Scheur in wegdek Hoofdstraat 12
Inhoud: Vanmorgen gemeld door bewoner. Ik zie een diepe scheur over de hele breedte. Bijgaand foto.
Bijlage: (foto)
```

Binnen ~10 sec verschijnt:
- Nieuwe melding in FieldOps met titel "Test - Scheur in wegdek Hoofdstraat 12"
- Foto als `photo_url` (data-URL, inline base64)
- Footer in description: `Van: jouw-email · Ontvangen via: email (mailgun)`

## Beveiliging

**Whitelist** (`allowed_senders`): zonder dit accepteert de route alle emails. Strikte productie-config:

```json
{
  "allowed_senders": ["@gemeentex.nl", "@partner-bouw.nl"]
}
```

Domein-prefix (`@example.com`) of exact adres (`specifiek@example.com`) wordt geaccepteerd. Anderen krijgen 202 (om reply-loops te voorkomen) maar er wordt geen melding aangemaakt. De `last_error` veld in de route logt het.

**Token-rotatie**: bij verdenking van lekkage:

```bash
curl -X POST https://portaal.fieldopsapp.nl/api/email-inbox/$ROUTE_ID/regenerate-token \
  -H "Authorization: Bearer $YOUR_TOKEN"
```

Oude token vervalt direct. Update de webhook-URL in je provider.

**Spam-bescherming**: Mailgun/Postmark/SendGrid filteren spam vóór ze bij ons aankomen. Extra DKIM/SPF/DMARC op `inbound.fieldopsapp.nl` aanbevolen.

## Beperkingen MVP

- **Mailgun attachments**: vereist extra API-call met API-key om foto's op te halen. MVP haalt ze niet binnen — zie issue voor v2.
- **Body-HTML naar plaintext**: naïeve regex-strip. Voor goed gevormde HTML-mails werkt het, voor uitzonderlijke layouts kan tekst rommelig zijn.
- **Multi-attachment**: alleen eerste image-attachment wordt als `photo_url` gezet. Andere attachments worden genegeerd.
- **Threading**: emails in dezelfde thread worden nu als losse meldingen aangemaakt. Auto-koppeling op `In-Reply-To` header komt in v2.

## DNS-record voorbeeld

Voor `inbound.fieldopsapp.nl` via Mailgun:

```
inbound.fieldopsapp.nl. 3600  IN  MX  10  mxa.mailgun.org.
inbound.fieldopsapp.nl. 3600  IN  MX  10  mxb.mailgun.org.
inbound.fieldopsapp.nl. 3600  IN  TXT "v=spf1 include:mailgun.org ~all"
```

Plus `_dmarc.inbound.fieldopsapp.nl. TXT "v=DMARC1; p=quarantine; rua=mailto:abuse@fieldopsapp.nl"` voor anti-spoof.

## Endpoints overzicht

| Endpoint | Auth | Doel |
|---|---|---|
| `POST /api/incoming/email/{token}` | Token in URL | Webhook (Mailgun/Postmark/SendGrid) |
| `GET /api/email-inbox/` | Org-admin | Lijst eigen routes |
| `POST /api/email-inbox/` | Org-admin | Nieuwe route + token |
| `PATCH /api/email-inbox/{id}` | Org-admin | Update defaults/whitelist |
| `POST /api/email-inbox/{id}/regenerate-token` | Org-admin | Nieuwe token |
| `DELETE /api/email-inbox/{id}` | Org-admin | Soft-delete (`is_active=False`) |
