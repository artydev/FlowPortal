"use strict";
/**
 * Utilitaires DOM et gabarits HTML : raccourci `$`, échappement automatique,
 * étiquette de gabarit `html`, `joinHtml`. Aucune dépendance.
 */
/** Raccourci : premier élément du DOM correspondant au sélecteur CSS (ou null). */
const $ = (selector) => document.querySelector(selector);
/**
 * Échappe une valeur avant de l'injecter dans du HTML.
 * Utilisée par le gabarit `html` : on n'a normalement jamais à l'appeler soi-même.
 * Toute donnée venant du backend ou de l'utilisateur (titres, noms de fichiers,
 * sorties d'étapes…) doit passer par `html` avant d'atteindre innerHTML.
 * Les guillemets doubles sont échappés car les attributs sont toujours écrits
 * entre guillemets doubles ; l'apostrophe n'a donc pas besoin de l'être.
 */
const escapeHtml = (value) =>
  String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
/**
 * Fragment HTML déjà sûr (produit par `html` ou `joinHtml`) : il est inséré tel
 * quel dans un autre gabarit, sans être échappé une seconde fois.
 */
class SafeHtml {
  constructor(value) {
    this.value = value;
  }
  toString() {
    return this.value;
  }
}

/**
 * Valeur interpolée dans un gabarit `html` :
 *  - fragment `SafeHtml` : inséré tel quel ;
 *  - tableau : éléments rendus puis concaténés (pas de `.join("")` à écrire) ;
 *  - null / undefined : rien ;
 *  - tout le reste (texte, nombre, booléen) : échappé.
 */
const renderHtmlValue = (value) =>
  value instanceof SafeHtml
    ? value.value
    : Array.isArray(value)
      ? value.map(renderHtmlValue).join("")
      : value == null
        ? ""
        : escapeHtml(value);

/**
 * Gabarit HTML à échappement automatique : html`<b>${titre}</b>`.
 * Toute valeur interpolée est échappée, sauf un autre fragment `html` ; on ne
 * peut donc plus oublier d'échapper une donnée venant du backend. Le résultat
 * est un `SafeHtml` : `element.innerHTML = html`…`` fonctionne directement.
 *
 * Les retours à la ligne (et l'indentation) du gabarit sont ignorés : ils ne
 * servent qu'à la lisibilité et ne créent jamais d'espace dans la page. Pour une
 * vraie espace, l'écrire sur la même ligne que le texte voisin.
 */
const html = (strings, ...values) =>
  new SafeHtml(
    strings.reduce((output, part, index) => {
      const compact = part.replace(/\s*\n\s*/g, (gap, offset, whole) => {
        const before = whole[offset - 1];
        const after = whole[offset + gap.length];
        // Entre deux mots, un retour à la ligne vaut une espace ; ailleurs, rien.
        return before && after && before !== ">" && after !== "<" ? " " : "";
      });
      return (
        output +
        compact +
        (index < values.length ? renderHtmlValue(values[index]) : "")
      );
    }, ""),
  );

/**
 * Concatène une liste de fragments/valeurs avec un séparateur de confiance
 * (ex. html`<br>` ou "\n"). Les éléments sont rendus comme dans `html`, donc
 * échappés sauf s'ils sont déjà des fragments `html`.
 */
const joinHtml = (items, separator) =>
  new SafeHtml(items.map(renderHtmlValue).join(separator));
