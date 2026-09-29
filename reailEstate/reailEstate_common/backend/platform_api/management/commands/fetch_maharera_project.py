"""Fetch public MahaRERA details with a visible, manual CAPTCHA handoff."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from filelock import FileLock, Timeout

from platform_api.external_sources import REGISTRATION, REGISTRY_SEARCH
from .refresh_google_project_places import read_report, report_default, write_report


DETAIL_HOST = "maharerait.maharashtra.gov.in"
PROJECT_URL_PATTERN = re.compile(r"/public/project/view/(\d+)/?$")
CAPTCHA_SELECTOR = "input[placeholder*='CAPTCHA' i], input[name*='captcha' i], iframe[src*='recaptcha']"


def detail_project_id(url):
    try:
        parsed = urlsplit(url)
        match = PROJECT_URL_PATTERN.fullmatch(parsed.path)
        if (parsed.scheme != "https" or parsed.hostname != DETAIL_HOST or parsed.username is not None
                or parsed.password is not None or parsed.port not in {None, 443} or not match):
            raise ValueError
        return match.group(1)
    except (TypeError, ValueError):
        raise CommandError("MahaRERA returned an unexpected project detail URL.") from None


def _store_mapping(path, number, project_id, detail_url):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path.with_suffix(".lock")), timeout=0):
            mappings = read_report(path) if path.exists() else {}
            mappings[number] = {"projectId": project_id, "detailUrl": detail_url,
                "discoveredAt": timezone.now().isoformat()}
            write_report(path, mappings)
    except Timeout:
        raise CommandError("Another MahaRERA lookup is updating the mapping. Retry shortly.") from None


def _captcha_handoff(page, headless, stdout):
    # TODO(maharera-captcha-resolver): Define and approve a compliant resolver interface before
    # automating any challenge handling. Until then, CAPTCHA completion is human-operated only.
    captcha = page.locator(CAPTCHA_SELECTOR).first
    if not captcha.is_visible():
        return
    if headless:
        raise CommandError("CAPTCHA requires a visible browser; rerun without --headless.")
    stdout.write("Complete and submit the CAPTCHA in the browser, then press Enter here.")
    input()
    captcha.wait_for(state="hidden")


def _extract_project_data(page):
    return page.locator("body").evaluate(r"""body => {
        const clean = value => (value || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
        const visible = element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
        const fields = {};
        for (const row of body.querySelectorAll('tr')) {
            if (!visible(row)) continue;
            const cells = Array.from(row.querySelectorAll(':scope > th, :scope > td')).map(cell => clean(cell.innerText));
            if (cells.length === 2 && cells[0] && cells[1]) fields[cells[0]] = cells[1];
        }
        const tables = Array.from(body.querySelectorAll('table')).filter(visible).map(table => ({
            headers: Array.from(table.querySelectorAll('thead th')).map(cell => clean(cell.innerText)),
            rows: Array.from(table.querySelectorAll('tbody tr')).filter(visible).map(row =>
                Array.from(row.querySelectorAll(':scope > th, :scope > td')).map(cell => clean(cell.innerText)))
        }));
        const links = Array.from(body.querySelectorAll('a[href]')).filter(visible).map(link => ({
            text: clean(link.innerText), url: link.href
        })).filter(link => link.text && /^https?:/.test(link.url));
        return {title: clean(body.ownerDocument.title), text: clean(body.innerText), fields, tables, links};
    }""")


class Command(BaseCommand):
    help = "Search exact MahaRERA registration, record its internal ID and export visible public details."

    def add_arguments(self, parser):
        parser.add_argument("registration_number")
        parser.add_argument("--mapping", type=Path, default=report_default("maharera-project-map.json"))
        parser.add_argument("--output", type=Path)
        parser.add_argument("--timeout-seconds", type=int, default=60)
        parser.add_argument("--headless", action="store_true", help="Stop safely if manual CAPTCHA interaction is required.")

    def handle(self, *args, **options):
        number = options["registration_number"].strip().upper()
        if not REGISTRATION.fullmatch(number):
            raise CommandError("Enter an exact valid MahaRERA registration number.")
        if not 5 <= options["timeout_seconds"] <= 600:
            raise CommandError("--timeout-seconds must be between 5 and 600.")
        output = options["output"] or report_default("maharera") / f"{number}.json"
        if output.resolve() == options["mapping"].resolve():
            raise CommandError("Mapping and output must use different files.")
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise CommandError("Install playwright and run python -m playwright install chromium in the backend environment.") from None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=options["headless"])
                try:
                    context = browser.new_context(accept_downloads=False)
                    page = context.new_page()
                    page.set_default_timeout(options["timeout_seconds"] * 1000)
                    page.goto(REGISTRY_SEARCH, wait_until="domcontentloaded")
                    _captcha_handoff(page, options["headless"], self.stdout)
                    page.locator("#edit-project-name").fill(number)
                    page.locator("#edit-submit").click()
                    _captcha_handoff(page, options["headless"], self.stdout)
                    card = page.locator("div.row.shadow", has_text=re.compile(rf"(?<![A-Z0-9]){re.escape(number)}(?![A-Z0-9])", re.I))
                    link = card.get_by_role("link", name="View Details").first
                    link.wait_for(state="visible")
                    detail_url = link.get_attribute("href") or ""
                    project_id = detail_project_id(detail_url)
                    _store_mapping(options["mapping"], number, project_id, detail_url)
                    self.stdout.write(f"Saved registration mapping: {number} -> {project_id}")
                    page.goto(detail_url, wait_until="domcontentloaded")
                    page.wait_for_function("""selector => Boolean(document.querySelector(selector)) ||
                        (document.body?.innerText.trim().length || 0) > 200""", arg=CAPTCHA_SELECTOR)
                    _captcha_handoff(page, options["headless"], self.stdout)
                    page.wait_for_function("number => document.body?.innerText.toUpperCase().includes(number)", arg=number)
                    if detail_project_id(page.url) != project_id:
                        raise CommandError("MahaRERA navigated to a different project; no details were exported.")
                    _captcha_handoff(page, options["headless"], self.stdout)
                    extracted = _extract_project_data(page)
                    if not re.search(rf"(?<![A-Z0-9]){re.escape(number)}(?![A-Z0-9])", extracted["text"], re.I):
                        raise CommandError("Rendered project does not contain the exact registration; no details were exported.")
                    write_report(output, {"registrationNumber": number, "projectId": project_id,
                        "sourceUrl": page.url, "retrievedAt": timezone.now().isoformat(), "data": extracted})
                    self.stdout.write(f"Saved public project data to {output}")
                finally:
                    browser.close()
        except PlaywrightError:
            raise CommandError("MahaRERA browser lookup did not complete. Check Chromium installation, network and visible page; no new details report was saved.") from None