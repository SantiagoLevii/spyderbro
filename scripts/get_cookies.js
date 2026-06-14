/*
 * ScrapBro — get_cookies.js
 *
 * Exports the cookies of the site you are currently logged into, in the
 * Cookie-Editor JSON format that ScrapBro's "paste cookie" flow accepts.
 *
 * How to use:
 *   1. Open the target site (Instagram / Facebook / LinkedIn / X / MercadoLibre)
 *      in your browser, logged in.
 *   2. Press F12 -> Console tab.
 *   3. Paste this whole file and press Enter.
 *   4. The JSON is copied to your clipboard (and printed). Paste it into ScrapBro.
 *
 * Note: document.cookie cannot read HttpOnly cookies (e.g. Instagram's
 * `sessionid`). For those, use the Cookie-Editor browser extension instead
 * (Export -> Copy to clipboard). This script is a no-install fallback.
 */
(function () {
  const cookies = document.cookie
    .split(";")
    .map((pair) => pair.trim())
    .filter(Boolean)
    .map((pair) => {
      const eq = pair.indexOf("=");
      const name = eq === -1 ? pair : pair.slice(0, eq);
      const value = eq === -1 ? "" : pair.slice(eq + 1);
      return {
        name: name,
        value: value,
        domain: "." + location.hostname.replace(/^www\./, ""),
        path: "/",
        secure: location.protocol === "https:",
        httpOnly: false,
      };
    });

  const json = JSON.stringify(cookies, null, 2);
  console.log(json);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard
      .writeText(json)
      .then(() => console.log("[ScrapBro] " + cookies.length + " cookies copied to clipboard."))
      .catch(() => console.log("[ScrapBro] Copy the JSON above manually."));
  }
  return json;
})();
