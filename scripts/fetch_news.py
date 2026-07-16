#!/usr/bin/env python3
"""Met à jour news.json avec les articles FR publiés sur starwarsunlimited.com."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://admin.starwarsunlimited.com/api/articles"
ARTICLE_LINK_TEMPLATE = "https://starwarsunlimited.com/fr/articles/{slug}"
USER_AGENT = "Mozilla/5.0 (compatible; swucardex-news-bot/1.0)"


def fetch_articles(page_size: int) -> list[dict]:
    params = [
        ("pagination[page]", "1"),
        ("pagination[pageSize]", str(page_size)),
        ("sort[0]", "publishedAt:desc"),
        ("locale", "fr"),
        ("populate", "mainImage"),
    ]
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        sys.exit(f"Erreur réseau lors de l'appel à l'API SWU : {exc}")
    except json.JSONDecodeError as exc:
        sys.exit(f"Réponse API SWU illisible (JSON invalide) : {exc}")

    if "data" not in payload or not isinstance(payload["data"], list):
        sys.exit("Réponse API SWU inattendue : clé 'data' absente ou invalide. "
                  "Le format de l'API a peut-être changé.")

    return payload["data"]


def to_news_entry(article: dict) -> dict | None:
    attributes = article.get("attributes", {})
    slug = attributes.get("slug")
    title = attributes.get("title")
    published_at = attributes.get("publishedAt")

    if not slug or not title or not published_at:
        print(f"Article ignoré (champs requis manquants) : id={article.get('id')}",
              file=sys.stderr)
        return None

    image_data = (attributes.get("mainImage") or {}).get("data") or {}
    image_url = image_data.get("attributes", {}).get("url", "")

    return {
        "id": slug,
        "title": title,
        "imageURL": image_url,
        "link": ARTICLE_LINK_TEMPLATE.format(slug=slug),
        "date": published_at[:10],
        "summary": attributes.get("description") or "",
    }


def load_existing(news_path: Path) -> list[dict]:
    if not news_path.exists():
        return []
    with news_path.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Racine du repo swucardex-data")
    parser.add_argument("--news-path", default="news.json",
                         help="Chemin du fichier news.json relatif à --repo-root")
    parser.add_argument("--page-size", type=int, default=20,
                         help="Nombre d'articles les plus récents à récupérer depuis l'API")
    args = parser.parse_args()

    news_path = Path(args.repo_root) / args.news_path

    articles = fetch_articles(args.page_size)
    fetched_entries = [entry for a in articles if (entry := to_news_entry(a)) is not None]
    fetched_entries.sort(key=lambda e: e["date"], reverse=True)

    existing_entries = load_existing(news_path)
    existing_ids = {e["id"] for e in existing_entries}

    new_entries = [e for e in fetched_entries if e["id"] not in existing_ids]
    merged = new_entries + existing_entries
    merged.sort(key=lambda e: e["date"], reverse=True)

    news_path.parent.mkdir(parents=True, exist_ok=True)
    with news_path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"{len(new_entries)} nouvel(s) article(s) ajouté(s) à {news_path}")


if __name__ == "__main__":
    main()
