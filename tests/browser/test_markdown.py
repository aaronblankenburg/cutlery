"""Browser test: Claude's messages in the job log are shown as Markdown, built from text only."""
import pytest

from test_upload import new_project

pytestmark = pytest.mark.browser

SAMPLE = """cut2 is ready: timeline, **FilmCraft export** (`exports/cut2.mp4`, 360p).

**Length:** 14:13 min instead of 11:49.

| Part | Length |
|---|---|
| Intro with map | 0:24 |
| Day 1 | 2:34 |

**Feedback applied:**
- **Map text too small:** rendered again,
  about 1.6 times as large.
- See [OpenTopoMap](https://opentopomap.org).

<img src=x onerror="window.pwned=1"> [bad](javascript:alert(1))"""


def test_markdown(page, server):
    if page.browser_name != "chrome":
        pytest.skip("one browser is enough")
    new_project(page, server)
    page.goto(f"{server['url']}/")
    r = page.evaluate("""async (text) => {
        const { markdown } = await import('/static/js/md.js');
        const m = markdown(text);
        document.body.append(m);
        await new Promise((ok) => setTimeout(ok, 300));
        return { tags: [...m.children].map((c) => c.tagName), th: [...m.querySelectorAll('th')].map((x) => x.textContent),
                 strong: m.querySelectorAll('strong').length, code: m.querySelector('code').textContent,
                 li: [...m.querySelectorAll('li')].map((x) => x.textContent),
                 links: [...m.querySelectorAll('a')].map((a) => a.href), img: m.querySelectorAll('img').length,
                 pwned: !!window.pwned, last: m.lastElementChild.textContent };
    }""", SAMPLE)
    assert r["tags"] == ["P", "P", "DIV", "P", "UL", "P"]
    assert r["th"] == ["Part", "Length"]
    assert r["strong"] == 4 and r["code"] == "exports/cut2.mp4"
    assert r["li"][0] == "Map text too small: rendered again, about 1.6 times as large."
    assert r["links"] == ["https://opentopomap.org/"]
    assert r["img"] == 0 and not r["pwned"] and r["last"].startswith("<img src=x")
