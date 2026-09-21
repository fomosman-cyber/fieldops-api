# COCO-SSD (lite_mobilenet_v2)

Herkenningsmodel voor het verpixelen van mensen en voertuigen in schouwbeelden,
op het toestel zelf en vóór het versturen.

- **Bron:** `https://storage.googleapis.com/tfjs-models/savedmodel/ssdlite_mobilenet_v2/`
  (de standaardlocatie van `@tensorflow-models/coco-ssd`, basis `lite_mobilenet_v2`)
- **Licentie:** Apache-2.0 (TensorFlow.js models)
- **Opgehaald:** 19 september 2026, ongewijzigd
- **Gebruikt door:** `templates/portaal.html` (`_schouwAnoniemLaad`), met
  `@tensorflow/tfjs@4.22.0` en `@tensorflow-models/coco-ssd@2.2.3` van jsdelivr

Wat het verpixelt: `person`, `car`, `truck`, `bus`, `motorcycle`. Het model is
getraind op COCO en ziet kleine of half verborgen mensen niet altijd. Het
verpixelen is daarom een extra laag, geen vervanging van de privacyregel dat
de inspecteur de camera zelf richt.

Hier staan de bestanden zelf, niet een link naar Google, zodat er bij het
schouwen geen verzoeken naar derden gaan en het portaal zijn eigen
beveiligingsinstellingen (`connect-src 'self'`) niet hoeft te verruimen.
