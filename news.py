"""Parse the official HMT news listing without executing page scripts."""

from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


NEWS_URL = "https://www.wherewindsmeetgame.com/hmt/news/"


def official_url(value, base, *, article=False):
    parsed = urlparse(urljoin(base, value))
    paths = ("/hmt/news/", "/jm/activity/") if article else ("/hmt/news/",)
    if (parsed.scheme != "https" or parsed.netloc != "www.wherewindsmeetgame.com"
            or not parsed.path.startswith(paths)):
        raise RuntimeError("官網新聞連結格式異常")
    return parsed._replace(fragment="").geturl()


class NewsListParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.articles = []
        self.next_page = None
        self.article = None
        self.field = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and "next-btn" in classes:
            self.next_page = official_url(attrs.get("href") or "", self.base)
        if tag == "a" and "news" in classes:
            self.article = {"url": official_url(attrs.get("href") or "", self.base, article=True),
                            "title": attrs.get("title") or "", "summary": "", "image": ""}
        if self.article is None:
            return
        if tag == "p":
            self.field = next((key for key in ("news_tit", "news_text", "date_day", "date_year")
                               if key in classes), None)
            self.parts = []
        if tag == "br" and self.field:
            self.parts.append("\n")
        if tag == "img" and not self.article["image"]:
            url = urljoin(self.base, attrs.get("src") or attrs.get("data-src") or "")
            if urlparse(url).scheme in ("http", "https") and url != self.base:
                self.article["image"] = url

    def handle_data(self, data):
        if self.article is not None and self.field:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.article is None:
            return
        if tag == "p" and self.field:
            key = {"news_tit": "title", "news_text": "summary"}.get(self.field, self.field)
            self.article[key] = "".join(self.parts).strip()
            self.field = None
        if tag == "a":
            article = self.article
            self.article = None
            if not article["title"] or article["url"] == self.base:
                raise RuntimeError("官網新聞缺少標題或文章連結")
            try:
                article["date"] = datetime.strptime(
                    f"{article.pop('date_year')}.{article.pop('date_day')}", "%Y.%m.%d"
                ).date().isoformat()
            except (KeyError, ValueError):
                raise RuntimeError("官網新聞日期格式異常") from None
            self.articles.append(article)


def parse_news_page(html, base=NEWS_URL):
    parser = NewsListParser(base)
    parser.feed(html)
    parser.close()
    if not parser.articles or parser.article is not None:
        raise RuntimeError("官網新聞列表為空或結構已變更，請檢查解析器")
    return parser.articles, parser.next_page
