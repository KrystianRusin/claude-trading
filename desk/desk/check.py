"""Deploy-time check: the settings load, and each shown book's database is readable with the tables
the API needs. A missing database is only reported (the bot may not have written yet); an unreadable
one or missing tables fail the check.

    .venv/bin/python -m desk.check
"""
import sys

from . import config, sources


def main():
    try:
        settings = config.load()
    except config.ConfigError as e:
        print(f"settings: {e}")
        return 1
    failed = False
    for bot in settings.bots:
        for book in settings.books:
            path = sources.db_path(settings, bot, book)
            problems = sources.check(path)
            if not problems:
                print(f"{bot} {book}: OK")
            elif "missing" in problems[0]:
                print(f"{bot} {book}: no database yet ({path})")
            else:
                failed = True
                for p in problems:
                    print(f"{bot} {book}: {p}")
    public = [s for s, on in settings.sections.items() if on]
    print(f"public sections: {', '.join(public) or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
