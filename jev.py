#!/usr/bin/env python3
"""Jev Decisions playground. Python 3.10+, standard library only."""
import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.error
import urllib.request

from scenarios import cases

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


def read_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY":
                key = value.strip().strip("\"'")
    if not key or key == "put-your-key-here":
        raise ValueError("Добавь OPENROUTER_API_KEY в .env или окружение. Для проверки без ключа: --dry-run или --demo.")
    return key


def payload(case, model):
    return {"model": model, "state": case["state"], "questions": case["questions"]}


def request(body, key, timeout):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:1500].replace(key, "[REDACTED]")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise ValueError("API не вернул объект answers; ответ: " + json.dumps(data, ensure_ascii=False)[:1500].replace(key, "[REDACTED]"))
    return data


def probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"Некорректная вероятность: {value!r}")
    return value


def evaluate(case, response):
    checks = []
    for name, question in case["questions"].items():
        answer = response["answers"].get(name)
        if not isinstance(answer, dict):
            raise ValueError(f"Отсутствует ответ {name}")
        kind = question["type"]
        value = answer.get(kind)
        if kind == "noul":
            probability(value)
            predicted = value >= 0.5
        elif kind == "choice":
            if value not in question["criteria"]:
                raise ValueError(f"Неизвестный choice в {name}: {value!r}")
            predicted = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Некорректный score в {name}: {value!r}")
            predicted = value
        if name in case["expected"]:
            expected = case["expected"][name]
            check = dict(question=name, expected=expected, predicted=predicted, correct=predicted == expected)
            if kind == "noul":
                check["brier"] = (value - int(expected)) ** 2
            checks.append(check)
    return checks


def demo_response(case):
    """Synthetic plumbing fixture, deliberately not model predictions."""
    answers = {}
    for name, q in case["questions"].items():
        if q["type"] == "noul":
            answers[name] = {"noul": 0.5}
        elif q["type"] == "choice":
            options = list(q["criteria"])
            answers[name] = {"choice": options[0], "probabilities": {k: 1 / len(options) for k in options}}
        else:
            n = len(q["criteria"])
            answers[name] = {"score": (n - 1) / 2, "probabilities": {str(i): 1 / n for i in range(n)}}
    return {"answers": answers, "synthetic": True}


def print_answers(case, response):
    print("Ответы модели:")
    for name, answer in response["answers"].items():
        question = case["questions"].get(name, {})
        print(f'  {name}: {question.get("instructions", "")}')
        if "noul" in answer:
            p = answer["noul"]
            print(f'    Вероятность «да»: {p:.1%}; «нет»: {1-p:.1%}')
        elif "choice" in answer:
            label = answer["choice"]
            description = question.get("criteria", {}).get(label, "")
            print(f'    Выбор: {label} — {description}')
        elif "score" in answer:
            print(f'    Оценка: {answer["score"]}')
            print("    Шкала: " + "; ".join(f"{i}: {text}" for i, text in enumerate(question.get("criteria", []))))
        if "probabilities" in answer:
            print("    Распределение: " + json.dumps(answer["probabilities"], ensure_ascii=False))


def summary(rows):
    checks = [c for row in rows for c in row.get("checks", [])]
    briers = [c["brier"] for c in checks if "brier" in c]
    latencies = [r["elapsed_s"] for r in rows if "error" not in r]
    costs = [r["response"].get("usage", {}).get("cost") for r in rows if isinstance(r.get("response", {}).get("usage"), dict)]
    costs = [v for v in costs if isinstance(v, (int, float)) and not isinstance(v, bool)]
    groups = {}
    for row in rows:
        group = groups.setdefault(row["case"]["group"], {"correct": 0, "labelled": 0, "errors": 0})
        group["errors"] += int("error" in row)
        group["labelled"] += len(row.get("checks", []))
        group["correct"] += sum(c["correct"] for c in row.get("checks", []))
    ranking = sorted([{"id": r["case"]["id"], "score": r["response"]["answers"]["relevance"]["score"]}
                      for r in rows if r["case"]["group"] == "ranking" and "error" not in r],
                     key=lambda item: item["score"], reverse=True)
    return dict(requests=len(rows), errors=sum("error" in r for r in rows),
                labelled_answers=len(checks), accuracy=sum(c["correct"] for c in checks) / len(checks) if checks else None,
                brier=statistics.mean(briers) if briers else None,
                median_seconds=statistics.median(latencies) if latencies else None,
                reported_cost_usd=sum(costs) if costs else None, requests_with_cost=len(costs),
                by_group=groups, ranking=ranking)


def write_report(rows, folder, model, demo):
    folder.mkdir(parents=True, exist_ok=True)
    stats = summary(rows)
    document = dict(created_at=dt.datetime.now(dt.timezone.utc).isoformat(), model=model,
                    endpoint=ENDPOINT, synthetic=demo, summary=stats, results=rows)
    (folder / "results.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    esc = lambda x: html.escape(str(x))
    pretty = lambda x: esc(json.dumps(x, ensure_ascii=False, indent=2))
    cards = []
    for row in rows:
        case = row["case"]
        parts = [f'<article><h2>{esc(case["id"])}</h2><p>{row["elapsed_s"]:.3f} с</p>',
                 f'<pre>{pretty(case["state"])}</pre>']
        if "error" in row:
            parts.append(f'<p class="bad">{esc(row["error"])}</p>')
        else:
            for name, answer in row["response"]["answers"].items():
                parts.append(f'<h3>{esc(name)}</h3><pre>{pretty(answer)}</pre>')
                dist = answer.get("probabilities", {})
                if isinstance(dist, dict):
                    for label, p in dist.items():
                        if isinstance(p, (int, float)) and 0 <= p <= 1:
                            parts.append(f'<div>{esc(label)} <meter min="0" max="1" value="{p}"></meter> {p:.1%}</div>')
            for check in row["checks"]:
                css = "ok" if check["correct"] else "bad"
                parts.append(f'<p class="{css}">{esc(check["question"])}: ожидалось {esc(check["expected"])}, получено {esc(check["predicted"])} — {"✓" if check["correct"] else "✗"}</p>')
        parts.append(f'<p>{esc(case["note"])}</p><details><summary>Вопросы и критерии</summary><pre>{pretty(case["questions"])}</pre></details></article>')
        cards.append("".join(parts))
    title = "ДЕМО — СИНТЕТИЧЕСКИЕ ДАННЫЕ, НЕ ОТВЕТЫ JEV" if demo else f"Jev: {model}"
    page = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{esc(title)}</title><style>body{{font:16px system-ui;background:#f4f6fa;color:#182334;max-width:1000px;margin:32px auto;padding:0 20px}}article{{background:white;padding:24px;margin:20px 0;border-radius:14px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1f7;padding:14px;border-radius:8px}}meter{{width:200px}}.ok{{color:#087442}}.bad{{color:#b32929}}h3{{margin-bottom:4px}}</style>
<h1>{esc(title)}</h1><p>Accuracy — доля совпадений с ручными метками при пороге noul 0.5. Brier — средний квадрат ошибки вероятности (меньше лучше). Это маленький учебный набор, не доказательство калибровки или общего качества.</p>
<pre>{pretty(stats)}</pre><p>Стоимость — только usage.cost, если API её вернул; null означает «неизвестно». Score показан как вернул API, без пересчёта в проценты. Пропущенные/ошибочные ответы не входят в accuracy; число ошибок показано отдельно.</p>{''.join(cards)}</html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Список сценариев")
    parser.add_argument("--scenario", default="all", help="Группа или ID; по умолчанию all")
    parser.add_argument("--limit", type=int, help="Максимум примеров")
    parser.add_argument("--repeat", type=int, default=1, help="Повторы для проверки стабильности")
    parser.add_argument("--model", default="typesafe/jev-1.13")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Показать запросы без API")
    mode.add_argument("--demo", action="store_true", help="Отчёт на синтетических данных без API")
    parser.add_argument("--input", type=Path, help="JSON со своими state и questions")
    parser.add_argument("--out", type=Path, help="Папка результатов")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if args.repeat < 1 or (args.limit is not None and args.limit < 1) or args.timeout <= 0:
        parser.error("repeat, limit и timeout должны быть положительными")
    selected = cases()
    if args.list:
        for c in selected:
            print(f'{c["id"]:30} {", ".join(c["questions"])}')
        return 0
    if args.input:
        custom = json.loads(args.input.read_text(encoding="utf-8"))
        selected = [dict(id="custom", group="custom", state=custom["state"], questions=custom["questions"], expected=custom.get("expected", {}), note="Пользовательский пример")]
    elif args.scenario != "all":
        selected = [c for c in selected if args.scenario in (c["id"], c["group"])]
    if not selected:
        parser.error("Сценарий не найден. Используй --list")
    selected = selected[:args.limit] * args.repeat
    if args.dry_run:
        print(json.dumps([payload(c, args.model) for c in selected], ensure_ascii=False, indent=2))
        return 0
    key = None if args.demo else read_key()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = args.out or ROOT / "results" / (("demo-" if args.demo else "run-") + stamp)
    rows = []
    print(f'{"ДЕМО" if args.demo else "API"}: {len(selected)} запросов; {args.model}', flush=True)
    try:
        for i, case in enumerate(selected, 1):
            start = time.monotonic()
            row = dict(case=case)
            try:
                row["response"] = demo_response(case) if args.demo else request(payload(case, args.model), key, args.timeout)
                row["checks"] = evaluate(case, row["response"])
            except (RuntimeError, ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
                row["error"] = str(exc).replace(key, "[REDACTED]") if key else str(exc)
            row["elapsed_s"] = time.monotonic() - start
            rows.append(row)
            print(f'[{i}/{len(selected)}] {case["id"]}: {row.get("error", "OK")} ({row["elapsed_s"]:.2f} с)', flush=True)
            if "error" not in row:
                print_answers(case, row["response"])
            write_report(rows, folder, args.model, args.demo)
            if "error" in row and not args.demo:
                print("Остановлено после ошибки; отчёт сохранён. Автоматических повторов нет.")
                break
    except KeyboardInterrupt:
        print("\nПрервано. Завершённые запросы сохранены.")
        return 130
    print(json.dumps(summary(rows), ensure_ascii=False, indent=2))
    print(f'Отчёт: {folder / "report.html"}')
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
