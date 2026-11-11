/* Replace the vendored client's own topbar identity with Qubit Office.
 *
 * The hosted app renders its upstream logo/wordmark from JS, often just after
 * the iframe load event. The iframe is same-origin, so the shell patches that
 * one identity node and watches the BODY (not the document head, where the
 * title lives) so its own title assignment cannot feed the observer. Legal
 * credit and the MIT notice stay visible.
 */
export function installOfficeBrand(win: Window): void {
  const doc = win?.document;
  if (!doc || !doc.body) return;

  const vendorStyle = doc.createElement("style");
  vendorStyle.setAttribute("data-qubit-vendor-splash", "1");
  vendorStyle.textContent = ".ui-splash{display:none!important}";
  doc.head?.appendChild(vendorStyle);

  const patch = (): void => {
    doc.querySelectorAll(".ui-splash").forEach((node) => node.remove());
    const brand = doc.querySelector(".ui-brand");
    if (!brand) return;
    brand.querySelector(".ui-brand__mark")?.remove();
    brand.querySelector(".ui-wordmark")?.remove();
    const name = brand.querySelector(".ui-brand__name");
    if (!name) return;
    if (name.textContent?.trim() !== "Qubit Office") {
      name.textContent = "Qubit Office";
      name.setAttribute(
        "style",
        "display:inline-flex;align-items:center;color:#f4f1ea;"
        + "font:700 16px/1 ui-monospace,SFMono-Regular,Menlo,monospace;"
        + "letter-spacing:.02em;white-space:nowrap;",
      );
    }
    if (doc.title !== "Qubit Office") doc.title = "Qubit Office";
  };

  patch();
  const Observer = (win as unknown as {
    MutationObserver?: typeof MutationObserver;
  }).MutationObserver;
  if (!Observer) return;
  const observer = new Observer(() => patch());
  observer.observe(doc.body, { childList: true, characterData: true, subtree: true });
  win.setTimeout(() => observer.disconnect(), 60_000);
}
