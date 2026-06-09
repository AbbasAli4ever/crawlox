"""Unit tests for CAPTCHA detection and solver selection."""
import pytest

from app.captcha.detector import detect_captcha
from app.captcha.solver import ManualOnlySolver, get_solver

pytestmark = pytest.mark.asyncio


class MockPage:
    def __init__(self, html: str, url: str = "https://site.com", body_text: str = ""):
        self._html = html
        self.url = url
        self._body = body_text

    async def content(self):
        return self._html

    async def evaluate(self, js):
        return self._body


class TestCaptchaDetection:
    async def test_recaptcha_v2(self):
        html = '<div class="g-recaptcha" data-sitekey="6Le-abc123"></div><script src="https://www.google.com/recaptcha/api.js"></script>'
        ctx = await detect_captcha(MockPage(html))
        assert ctx.detected is True
        assert ctx.captcha_type == "recaptcha_v2"
        assert ctx.sitekey == "6Le-abc123"

    async def test_hcaptcha_wins_over_recaptcha(self):
        # hCaptcha pages also use data-sitekey; hcaptcha must be detected first
        html = '<div class="h-captcha" data-sitekey="hc-xyz789"></div><script src="https://hcaptcha.com/1/api.js"></script>'
        ctx = await detect_captcha(MockPage(html))
        assert ctx.captcha_type == "hcaptcha"
        assert ctx.sitekey == "hc-xyz789"

    async def test_cloudflare_challenge(self):
        html = '<div id="cf-browser-verification">Checking your browser</div><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page"></script>'
        ctx = await detect_captcha(MockPage(html))
        assert ctx.detected is True
        assert ctx.captcha_type == "cloudflare"

    async def test_no_captcha_normal_page(self):
        html = '<article class="product"><h2>Widget</h2><p>A long product description with plenty of real content text here</p></article>'
        ctx = await detect_captcha(MockPage(html, body_text="A long product description"))
        assert ctx.detected is False
        assert ctx.captcha_type == "none"

    async def test_no_false_positive_on_word_captcha(self):
        # Page mentions "captcha" in copy but has no actual challenge — and has lots of text
        html = "<body>" + "<p>We use captcha to protect forms. " * 50 + "</p></body>"
        ctx = await detect_captcha(MockPage(html, body_text="x" * 600))
        assert ctx.detected is False


class TestSolverSelection:
    def test_free_user_gets_manual(self):
        assert isinstance(get_solver("free", "manual_only"), ManualOnlySolver)

    def test_premium_manual_env_gets_manual(self):
        assert isinstance(get_solver("premium", "manual_only"), ManualOnlySolver)

    def test_free_twocaptcha_env_gets_manual(self):
        # free tier never uses 2captcha even if env says twocaptcha
        assert isinstance(get_solver("free", "twocaptcha"), ManualOnlySolver)

    def test_premium_twocaptcha_no_key_falls_back_to_manual(self):
        # No TWOCAPTCHA_API_KEY in dev -> construction fails -> ManualOnlySolver
        assert isinstance(get_solver("premium", "twocaptcha"), ManualOnlySolver)
