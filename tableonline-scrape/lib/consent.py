"""Dismissing EU cookie-consent overlays before capturing a page.

Every page on this site opens behind a consent banner. Left in place it is the
only text in the snapshot, it can block the content underneath from rendering,
and it makes every extraction look empty.

The copy observed on tableonline.fi ("We value your privacy", "Customize
Consent Preferences", Accept All / Reject All / Customize) is CookieYes, so its
selectors come first; OneTrust and Cookiebot follow, then generic fallbacks.
"""
from __future__ import annotations

# Ordered most specific first. A generic text match last, because
# "button:has-text('Accept')" will happily click a newsletter dialog.
ACCEPT_SELECTORS = (
    ".cky-btn-accept",                       # CookieYes
    "[data-cky-tag='accept-button']",
    "#onetrust-accept-btn-handler",          # OneTrust
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",   # Cookiebot
    "#CybotCookiebotDialogBodyButtonAccept",
    ".cc-allow",                             # Cookie Consent
    "#hs-eu-confirmation-button",            # HubSpot
    "button[aria-label*='Accept' i]",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Hyväksy kaikki')",
    "button:has-text('Hyväksy')",
    "button:has-text('Nõustu kõigiga')",
    "button:has-text('Nõustu')",
    "button:has-text('Accept')",
)

# Containers to hide if nothing can be clicked, so at least their text stops
# drowning the page.
BANNER_SELECTORS = (
    ".cky-consent-container", ".cky-overlay", "#onetrust-consent-sdk",
    "#CybotCookiebotDialog", ".cc-window", "#cookie-banner", ".cookie-banner",
)


def dismiss(page, timeout_ms: int = 3000) -> str | None:
    """Click an accept button. Returns the selector used, or None.

    Never raises: a page with no banner is the normal case, and a failed click
    must not lose the capture.
    """
    for selector in ACCEPT_SELECTORS:
        try:
            element = page.query_selector(selector)
            if element and element.is_visible():
                element.click(timeout=timeout_ms)
                page.wait_for_timeout(500)
                return selector
        except Exception:  # noqa: BLE001 — overlay handling is best-effort
            continue
    return None


def hide(page) -> None:
    """Last resort: remove the banner from the DOM so its text is not captured."""
    try:
        page.evaluate(
            """(selectors) => {
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                }
                document.documentElement.style.overflow = 'auto';
                document.body.style.overflow = 'auto';
            }""", list(BANNER_SELECTORS))
    except Exception:  # noqa: BLE001
        pass
